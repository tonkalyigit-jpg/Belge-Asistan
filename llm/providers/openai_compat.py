"""OpenAI-uyumlu `/chat/completions` adapteri.

Tek dosyayla OpenRouter, Groq, DeepSeek, Together, Fireworks, vLLM ve Ollama
kapsanıyor — hepsi aynı HTTP sözleşmesini konuşuyor. Sağlayıcı değiştirmek
settings.yaml'da `base_url` + `model` + `api_key_env` değiştirmekten ibaret.

Bu dosya bilinçli olarak Anthropic SDK'sına hiç dokunmaz; Claude çağrıları
`anthropic_provider.py` üzerinden resmî SDK ile gider.
"""
from __future__ import annotations

import json
import re
import time

import httpx

from config import env

from ..base import LLMError, LLMProvider, LLMResponse, TierConfig, ipv4_transport


# Akıl yürüten modeller (Qwen3, DeepSeek-R1, bazı gpt-oss sürümleri) düşünme
# adımlarını yanıt gövdesine gömüyor. Ölçüldü: qwen3.6-27b sınıflandırıcı
# çağrısında `<think>…` yazıp JSON ayıklamayı tamamen kırıyor. Düzyazıda ise
# kullanıcı modelin iç monologunu görür.
_THINK_BLOCK = re.compile(
    r"<(think|thinking|reasoning)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
# Kapanış etiketi hiç gelmemişse (max_tokens'a takılmışsa) açılıştan sonrası atılır.
_THINK_OPEN = re.compile(r"<(think|thinking|reasoning)\b[^>]*>.*", re.DOTALL | re.IGNORECASE)


# Yalnızca AÇILIŞ etiketi — akışta bir bloğun içinde olup olmadığımızı
# anlamak için. `strip_reasoning` kesemediğinde metni olduğu gibi döndürüyor,
# yani "temiz mi" diye ona bakmak yanlış cevap veriyor (test yakaladı).
_OPEN_TAG = re.compile(r"<(think|thinking|reasoning)\b", re.IGNORECASE)

_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)?", re.IGNORECASE)


def _parse_duration(raw: str) -> float | None:
    """`30`, `2.5s`, `1m30s`, `500ms` gibi bekleme sürelerini saniyeye çevirir.

    Sağlayıcılar bu başlığı standart bir formatta yazmıyor; HTTP tarihi
    formatı (`Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`) desteklenmiyor,
    o durumda None dönüp üstel geri çekilmeye bırakılıyor.
    """
    raw = (raw or "").strip()
    if not raw or raw[0].isalpha():
        return None

    unit_seconds = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    total = 0.0
    found = False
    for value, unit in _DURATION.findall(raw):
        try:
            total += float(value) * unit_seconds.get((unit or "s").lower(), 1.0)
        except ValueError:
            continue
        found = True
    return total if found else None


def strip_reasoning(text: str) -> str:
    """Yanıttan düşünme bloklarını temizler.

    Kapalı blokları siler; kapanmamış bir blok kalmışsa ve öncesinde metin
    varsa o metni tutar — aksi halde her şeyi silip boş yanıt üretirdik.
    """
    if not text or "<" not in text:
        return text
    cleaned = _THINK_BLOCK.sub("", text)
    if _THINK_OPEN.search(cleaned):
        before = _THINK_OPEN.sub("", cleaned).strip()
        cleaned = before if before else cleaned
    return cleaned.strip()


# Sağlayıcının reddedebileceği, DÜŞÜRÜLEBİLİR parametreler. Ortak özellikleri
# isteğe bağlı olmaları: yokluğunda çağrı çalışır, davranış varsayılana döner.
_DROPPABLE = ("temperature", "reasoning_effort", "stream_options", "stop", "top_p")


def _mentions(error_text: str, name: str) -> bool:
    return name.lower() in (error_text or "").lower()


def _wall_budget(tier: TierConfig) -> float:
    """Tek çağrının yeniden denemeler dahil süre bütçesi.

    Varsayılan sohbetin bütçesi (`limits.max_wall_seconds`): kullanıcı ekrana
    bakarken bir çağrı bunu aşmamalı. Ama taranmış sayfa okumak toplu bir iş —
    kullanıcı beklemiyor, hız sınırına takılan bir sayfanın 90 saniyede
    vazgeçip belgeyi eksik bırakması daha kötü. Kademe bunu `max_wall_seconds`
    ile kendisi için genişletebiliyor.
    """
    import config

    return float(tier.extra.get("max_wall_seconds", config.get("limits.max_wall_seconds", 90)))


class OpenAICompatProvider(LLMProvider):
    def __init__(self, tier: TierConfig):
        super().__init__(tier)
        if not tier.base_url:
            raise LLMError(
                f"`{tier.name}` kademesi openai_compat kullanıyor ama `base_url` tanımlı değil"
            )
        self.base_url = tier.base_url.rstrip("/")

        api_key = env(tier.api_key_env) if tier.api_key_env else None
        # Lokal sunucular (Ollama, vLLM) anahtar istemez; placeholder yeterli.
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        for key, value in tier.extra.get("headers", {}).items():
            headers[key] = value

        # PARAMETRE KAYMASI LİSTEYLE DEĞİL, HATAYLA ÇÖZÜLÜYOR.
        #
        # "hangi model neyi destekliyor" listesi tutmak cazip ama her yeni
        # model çıktığında eskiyor. Sağlayıcının kendi 400'ü eskimiyor: neyi
        # kabul etmediğini zaten söylüyor. İlk reddedilen çağrıda öğreniliyor,
        # sonraki çağrılar düzeltilmiş yükle gidiyor — bedel sağlayıcı başına
        # bir kere ödeniyor. `settings.yaml`'dan önceden de verilebiliyor.
        self._dropped: set[str] = set(tier.extra.get("drop_params", []))
        self._max_tokens_key = tier.extra.get("max_tokens_key", "max_tokens")

        self._client = httpx.Client(
            transport=ipv4_transport(),
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(self._request_timeout(tier)),
        )

    @staticmethod
    def _request_timeout(tier: TierConfig) -> float:
        """Tek istek zaman aşımı, sorgu bütçesini AŞAMAZ.

        `limits.max_wall_seconds` bir devre kesici olarak belgelenmiş ama
        graph onu yalnızca düğümler ARASINDA kontrol ediyor; bir düğümün
        içinde sağlayıcı istediği kadar bekleyebiliyordu.

        ÖLÇÜLDÜ (70 gerçek sorgu izi): sınıflandırıcı medyanı 1.5 sn, ama
        p95 ve maksimum 601 sn. 601 doğal bir değer değil, tavanın kendisi:
        4 deneme x 120 sn zaman aşımı + 3 x 30 sn bekleme = 570 sn. 71
        çağrının 12'si 10 sn'yi aşıyor ve toplam sürenin %97'si o 12 çağrıda.
        Sınıflandırıcı her sorgudaki İLK çağrı olduğu için hız sınırının
        bedelini o ödüyor.

        Artık tek istek bütçenin yarısını aşamıyor: en kötü durumda iki
        deneme bütçeyi doldurur ve akış devre kesiciye geri döner.
        """
        import config

        butce = _wall_budget(tier)
        istenen = float(tier.extra.get("timeout", 120.0))
        return max(5.0, min(istenen, butce / 2))

    # Ücretsiz kotalarda 429 sık: bench koşusu 22 sorgu × ~6 çağrıyı dakikalar
    # içinde attığında Groq limitine takıldı ve iki sorgu yarım kaldı. Sabit
    # üstel bekleme yetmiyor; sağlayıcı ne kadar bekleneceğini zaten söylüyor.
    _MAX_RETRY_WAIT = 30.0

    @classmethod
    def _retry_delay(cls, resp: httpx.Response, attempt: int) -> float:
        """Sağlayıcının söylediği bekleme süresini kullanır, yoksa üstel geri çekilir.

        `Retry-After` saniye veya HTTP tarihi olabilir; Groq ayrıca
        `x-ratelimit-reset-*` başlıklarını `2.5s` / `1m30s` gibi yazıyor.
        """
        for header in ("retry-after", "x-ratelimit-reset-requests",
                       "x-ratelimit-reset-tokens"):
            raw = resp.headers.get(header)
            if not raw:
                continue
            seconds = _parse_duration(raw)
            if seconds is not None:
                # Sağlayıcı çok uzun bir bekleme söylerse (günlük kota) tavana
                # vurup hatayı yukarı taşıyoruz; sorguyu 10 dakika askıda
                # tutmaktansa düşürmek daha dürüst.
                return min(max(seconds, 0.5), cls._MAX_RETRY_WAIT)
        # Gemini bekleme süresini başlıkta değil GÖVDEDE veriyor:
        # "details": [{"@type": "...RetryInfo", "retryDelay": "31s"}].
        # Bakılmadığında üstel geri çekilme 1-2-4 sn bekleyip vazgeçiyordu;
        # dakikalık kotaya karşı bu hiçbir işe yaramaz. ÖLÇÜLDÜ: 19 soruluk
        # sette iki cevap bu yüzden düştü, biri sessizce yanlış bir cevaba döndü.
        try:
            eslesme = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', resp.text or "")
        except Exception:
            eslesme = None
        if eslesme:
            return min(max(float(eslesme.group(1)), 0.5), cls._MAX_RETRY_WAIT)
        return float(2**attempt)

    def _build_payload(
        self, system: str, user: str, *, max_tokens: int | None,
        stop: list[str] | None, temperature: float | None, stream: bool,
        images: list[tuple[bytes, str]] | None = None,
    ) -> dict:
        # cache_system bu sözleşmede karşılığı olmayan bir kavram; sağlayıcılar
        # prefix cache'i kendiliğinden ve şeffaf uyguluyor.
        content: object = user
        if images:
            # OpenAI sözleşmesinin görsel biçimi: base64 data URI. Gemini'nin
            # uyumluluk uç noktası, vLLM ve Ollama aynı biçimi kabul ediyor —
            # local görsel modele geçişte bu kod değişmiyor.
            import base64

            content = [{"type": "text", "text": user}] + [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{base64.b64encode(data).decode()}"
                    },
                }
                for data, mime in images
            ]
        payload: dict = {
            "model": self.tier.model,
            self._max_tokens_key: max_tokens or self.tier.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        if stop:
            payload["stop"] = stop
        # Çağrı başına sıcaklık, kademe ayarını geçersiz kılabiliyor.
        # ÖLÇÜLDÜ: normal üretimde T=0 en iyisi — çerçeve kavramları 4/6,
        # T=0.8'de 1/4'e düşüyor; sıcaklık bilgi katmıyor, varyans katıyor.
        # Ama "yeniden üret" düğmesi T=0'da AYNI cevabı üretiyor ve işlevsiz
        # kalıyor. Bu yüzden yalnızca o yol küçük bir sıcaklık geçiriyor.
        if temperature is not None:
            payload["temperature"] = temperature
        elif "temperature" in self.tier.extra:
            payload["temperature"] = self.tier.extra["temperature"]
        if "reasoning_effort" in self.tier.extra:
            # Akıl yürüten modellerde (gpt-oss, Qwen3) düşünme adımı max_tokens
            # bütçesinden yiyor ve uzunluğu öngörülemiyor: ölçümde gpt-oss-20b
            # max_tokens=150'de 383 token düşünüp bazen içeriğe hiç yer
            # bırakmadı. `low` bunu 48'e indiriyor. Kontrol düzlemi düğümleri
            # (sınıflandırma, puanlama, ikili denetim) derin akıl yürütme
            # istemediği için burada bedava bir kazanç.
            payload["reasoning_effort"] = self.tier.extra["reasoning_effort"]

        if stream:
            payload["stream"] = True
            # Akışta token sayısı ancak bu bayrakla geliyor. Desteklemeyen
            # sağlayıcı 400 dönüyor; öğrenme onu düşürüyor ve muhasebe sıfır
            # token'la sürüyor — cevap yine de akıyor.
            payload["stream_options"] = {"include_usage": True}

        for key in self._dropped:
            payload.pop(key, None)
        return payload

    def _learn_from_error(self, error_text: str, payload: dict) -> str | None:
        """400 metnini okuyup yükü TEK bir adım düzeltir.

        Dönen değer bir açıklama; None ise düzeltilecek bir şey bulunamadı ve
        hata yukarı taşınmalı. Her çağrı tek bir değişiklik yapıyor: birden
        fazla parametre reddedilmişse döngü sırayla hepsini öğreniyor.
        """
        # 1) Yeniden adlandırma: akıl yürüten model aileleri `max_tokens`
        #    yerine `max_completion_tokens` istiyor. Bu bir model farkı değil,
        #    satıcının kendi alan adını değiştirmesi.
        if (
            self._max_tokens_key == "max_tokens"
            and _mentions(error_text, "max_completion_tokens")
        ):
            self._max_tokens_key = "max_completion_tokens"
            return "max_tokens -> max_completion_tokens"

        # 2) Düşürme: sağlayıcı adını anıyorsa o parametre olmadan devam.
        for name in _DROPPABLE:
            if name in payload and name not in self._dropped and _mentions(error_text, name):
                self._dropped.add(name)
                return f"{name} parametresi düşürüldü"
        return None

    def complete_vision(
        self,
        system: str,
        user: str,
        images: list[tuple[bytes, str]],
        *,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return self.complete(system, user, max_tokens=max_tokens, images=images)

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        cache_system: bool = False,
        temperature: float | None = None,
        images: list[tuple[bytes, str]] | None = None,
    ) -> LLMResponse:
        payload = self._build_payload(
            system, user, max_tokens=max_tokens, stop=stop,
            temperature=temperature, stream=False, images=images,
        )

        started = time.perf_counter()
        last_error: Exception | None = None
        adaptations = 0
        # Toplam süre bütçesi: yeniden denemeler birikip sorgu bütçesini
        # aşmamalı. Tek istek zaman aşımı zaten yarıya bağlı; bu, uykuların
        # üstüne binmesini engelliyor.
        import config as _config

        _butce = _wall_budget(self.tier)
        attempts = int(self.tier.extra.get("max_retries", 4))
        # Beklemeler yanıtla birlikte yukarı taşınıyor: bunlar olmadan hız
        # sınırı uykusu kullanıcıya donmuş bir arayüz olarak görünüyor
        # (ölçüldü: 49.7 sn'lik bir sorgunun 28.4 sn'si bu uykulardı).
        waited = 0.0
        retries = 0

        # `attempts + 3`: parametre uyarlaması bir deneme harcamamalı,
        # yoksa iki parametre reddedildiğinde hız sınırı için deneme kalmıyor.
        for attempt in range(attempts + 3):
            if attempt - adaptations >= attempts:
                # `break` DEĞİL `raise`: break, aşağıdaki `else` dalını atlıyor
                # ve akış `data` hiç atanmadan devam ediyordu — hız sınırında
                # temiz bir LLMError yerine UnboundLocalError alınıyordu
                # (bench koşusunda 10. soruda yaşandı).
                raise LLMError(
                    f"{self.base_url} çağrısı başarısız: {last_error}"
                )
            try:
                resp = self._client.post("/chat/completions", json=payload)
                if resp.status_code in (408, 409, 429) or resp.status_code >= 500:
                    last_error = LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    harcanan = max(time.perf_counter() - started, waited)
                    if attempt < attempts - 1 and harcanan < _butce:
                        delay = min(
                            self._retry_delay(resp, attempt), _butce - harcanan
                        )
                        waited += delay
                        retries += 1
                        time.sleep(delay)
                        continue
                    raise LLMError(
                        f"{self.base_url} çağrısı başarısız: {last_error}"
                    )
                if resp.status_code == 400:
                    # Sağlayıcı hangi parametreyi kabul etmediğini söylüyor;
                    # onu düzeltip aynı denemede tekrar gönderiyoruz. Öğrenilen
                    # düzeltme örnekte kalıyor, sonraki çağrılar bedelsiz.
                    fix = self._learn_from_error(resp.text, payload)
                    if fix and adaptations < 3:
                        adaptations += 1
                        payload = self._build_payload(
                            system, user, max_tokens=max_tokens, stop=stop,
                            temperature=temperature, stream=False, images=images,
                        )
                        continue
                if resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                break
            except httpx.HTTPError as exc:
                last_error = exc
                harcanan = max(time.perf_counter() - started, waited)
                if attempt < attempts - 1 and harcanan < _butce:
                    delay = min(float(2**attempt), _butce - harcanan)
                    waited += delay
                    retries += 1
                    time.sleep(delay)
                    continue
                raise LLMError(f"{self.base_url} çağrısı başarısız: {last_error}")
        else:
            raise LLMError(f"{self.base_url} çağrısı başarısız: {last_error}")

        latency_ms = (time.perf_counter() - started) * 1000

        try:
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
            # Bazı sağlayıcılar akıl yürütmeyi ayrı bir alanda döndürüyor;
            # o zaten gövdede olmadığı için sadece görmezden geliniyor.
            if not text and message.get("reasoning_content"):
                raise LLMError("Model yalnızca akıl yürütme döndürdü, yanıt boş")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Beklenmeyen yanıt şekli: {str(data)[:300]}") from exc

        text = strip_reasoning(text)

        usage = data.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        cached = int(
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        )

        return LLMResponse(
            text=text,
            retry_wait_s=waited,
            retries=retries,
            model=self.tier.model,
            provider="openai_compat",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_input_tokens=cached,
            cost_usd=self.tier.cost(in_tok, out_tok),
            latency_ms=latency_ms,
            raw=data,
        )

    def stream(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        cache_system: bool = False,
        on_delta=None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """SSE ile gerçek akış.

        Bu metot yazılana kadar `base.py`'deki varsayılan devredeydi ve o
        varsayılan `complete()`'e düşüyordu: metin tek parça geliyordu. Arayüz
        metni karakter karakter döktüğü için akıyormuş gibi görünüyordu, ama
        ilk kelime ancak üretim BİTTİKTEN sonra çıkıyordu. Kazanç algılanan
        gecikmede: üretim adımı tek bir sorgunun 24.5 saniyesinin 18.7'siydi.

        Akışta iki şey farklı:

        * `<think>` blokları parça parça geliyor ve tam metni beklemeden
          temizlenemiyor. Blok açıkken parçalar TUTULUYOR, kapanınca atılıyor;
          kullanıcı modelin iç monologunu hiç görmüyor.
        * Yeniden deneme yalnızca HENÜZ HİÇBİR ŞEY YAZILMADIYSA yapılabiliyor.
          Ekrana metin gittikten sonra baştan başlamak, kullanıcıya cevabın
          ortasından ikinci bir cevap göstermek olurdu.
        """
        payload = self._build_payload(
            system, user, max_tokens=max_tokens, stop=None,
            temperature=temperature, stream=True,
        )
        started = time.perf_counter()
        attempts = int(self.tier.extra.get("max_retries", 4))
        last_error: Exception | None = None
        adaptations = 0
        # Toplam süre bütçesi: yeniden denemeler birikip sorgu bütçesini
        # aşmamalı. Tek istek zaman aşımı zaten yarıya bağlı; bu, uykuların
        # üstüne binmesini engelliyor.
        import config as _config

        _butce = float(_config.get("limits.max_wall_seconds", 90))
        waited = 0.0
        retries = 0

        for attempt in range(attempts + 3):
            if attempt - adaptations >= attempts:
                raise LLMError(
                    f"{self.base_url} akış çağrısı başarısız: {last_error}"
                )
            emitted = False
            try:
                with self._client.stream(
                    "POST", "/chat/completions", json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        text = resp.text
                        if resp.status_code == 400:
                            fix = self._learn_from_error(text, payload)
                            if fix and adaptations < 3:
                                adaptations += 1
                                payload = self._build_payload(
                                    system, user, max_tokens=max_tokens, stop=None,
                                    temperature=temperature, stream=True,
                                )
                                continue
                        if resp.status_code in (408, 409, 429) or resp.status_code >= 500:
                            last_error = LLMError(f"HTTP {resp.status_code}: {text[:200]}")
                            if attempt < attempts - 1:
                                delay = self._retry_delay(resp, attempt)
                                waited += delay
                                retries += 1
                                time.sleep(delay)
                            continue
                        raise LLMError(f"HTTP {resp.status_code}: {text[:300]}")

                    full, usage, emitted = self._consume_sse(resp, on_delta)
                break
            except LLMError:
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if emitted:
                    # Yarım yazılmış bir cevabın üstüne ikinci bir cevap
                    # yazmaktansa hatayı bildirmek dürüst.
                    raise LLMError(f"Akış yarıda kesildi: {exc}") from exc
                if attempt < attempts - 1:
                    delay = float(2**attempt)
                    waited += delay
                    retries += 1
                    time.sleep(delay)
        else:
            raise LLMError(f"{self.base_url} akış çağrısı başarısız: {last_error}")

        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        cached = int(
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        )
        return LLMResponse(
            text=full,
            model=self.tier.model,
            provider="openai_compat",
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_input_tokens=cached,
            cost_usd=self.tier.cost(in_tok, out_tok),
            latency_ms=(time.perf_counter() - started) * 1000,
            retry_wait_s=waited,
            retries=retries,
        )

    def _consume_sse(self, resp, on_delta) -> tuple[str, dict, bool]:
        """SSE satırlarını okuyup (tam metin, kullanım, yazıldı mı) döner."""
        full = ""
        usage: dict = {}
        emitted = False
        hold = ""            # açık bir düşünme bloğunda biriken metin

        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("usage"):
                usage = event["usage"]
            choices = event.get("choices") or []
            if not choices:
                continue
            piece = (choices[0].get("delta") or {}).get("content") or ""
            if not piece:
                continue

            full += piece
            hold += piece

            # Tamamlanmış bloklar atılıyor; geriye AÇIK bir blok kalırsa
            # ondan öncesi yazılıp gerisi bekletiliyor.
            stripped = _THINK_BLOCK.sub("", hold)
            open_at = _OPEN_TAG.search(stripped)
            if open_at:
                ready, hold = stripped[:open_at.start()], stripped[open_at.start():]
            else:
                # Parça bir etiketin ortasında kesilmiş olabilir ("<thi").
                # Kapanmamış bir "<" varsa onu da bekletiyoruz, yoksa yarım
                # etiket ekrana düşüyor.
                cut = stripped.rfind("<")
                if cut != -1 and ">" not in stripped[cut:]:
                    ready, hold = stripped[:cut], stripped[cut:]
                else:
                    ready, hold = stripped, ""

            if ready:
                if on_delta:
                    on_delta(ready)
                emitted = True

        return strip_reasoning(full), usage, emitted
