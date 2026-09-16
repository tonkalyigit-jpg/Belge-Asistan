"""Anthropic adapteri — resmî `anthropic` SDK'sı üzerinden."""
from __future__ import annotations

import time

from config import get as config_get

from ..base import LLMError, LLMProvider, LLMResponse, TierConfig, ipv4_transport


class AnthropicProvider(LLMProvider):
    """Claude modelleri için LLMProvider uygulaması.

    Not: `thinking` parametresi bilinçli olarak gönderilmiyor. Haiku 4.5 gibi
    eski modellerde bu "düşünme kapalı" (ucuz + hızlı, sınıflandırma/puanlama
    düğümleri için istediğimiz davranış) demek; Sonnet 5 / Opus 5 gibi güncel
    modellerde ise varsayılan zaten adaptive thinking, yani güçlü kademe
    otomatik olarak akıl yürütme ile çalışıyor.
    """

    def __init__(self, tier: TierConfig):
        super().__init__(tier)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - kurulum hatası
            raise LLMError(
                "`anthropic` paketi kurulu değil. `pip install anthropic` çalıştır."
            ) from exc

        kwargs: dict = {}
        if tier.api_key_env:
            from config import env

            key = env(tier.api_key_env)
            if key:
                kwargs["api_key"] = key
        if tier.base_url:
            kwargs["base_url"] = tier.base_url

        # Kimliğe bağlı (identity-linked) anahtarlar her istekte hangi çalışma
        # alanında hareket edildiğini bildirmek zorunda; bildirilmezse API 400
        # döner ve çalışma alanı listesini sormak ayrı bir yetki istiyor.
        # Ayarlarda `workspace_id` verilmişse başlık olarak eklenir.
        workspace = tier.extra.get("workspace_id")
        if workspace:
            kwargs["default_headers"] = {"anthropic-workspace-id": str(workspace)}

        # ZAMAN AŞIMI AÇIKÇA VERİLİYOR. Anthropic SDK'sının varsayılanı 10 DAKİKA
        # ve 2 yeniden deneme — yani tek bir takılan istek boru hattını yarım
        # saate kadar kilitleyebiliyor. `limits.max_wall_seconds` bunu kesemez,
        # çünkü o guard yalnızca düğümler ARASINDA kontrol ediliyor; süren bir
        # HTTP çağrısına müdahale edemiyor. Gözlemlendi: sağlayıcı tarafındaki
        # bir sorun kullanıcıya sonsuz dönen bir spinner olarak göründü, hata
        # olarak değil.
        #
        # Tavan duvar saati limitine bağlanıyor ki iki ayar birbirinden
        # kopmasın: limit büyürse zaman aşımı da büyür.
        wall = float(config_get("limits.max_wall_seconds", 90))
        timeout = float(tier.extra.get("timeout", wall))
        kwargs["timeout"] = timeout
        kwargs["max_retries"] = int(tier.extra.get("max_retries", 1))

        # Anahtar verilmezse SDK kendi kimlik zincirini kullanır
        # (ANTHROPIC_API_KEY -> ANTHROPIC_AUTH_TOKEN -> `ant auth login` profili).
        # DİKKAT: Anthropic SDK 1.x httpx2 üzerine kurulu; `httpx` istemcisi
        # verilirse TypeError atıyor. Bu yüzden transport httpx2'den alınıyor.
        transport = ipv4_transport("httpx2")
        if transport is not None:
            import httpx2

            kwargs["http_client"] = httpx2.Client(
                transport=transport, timeout=httpx2.Timeout(timeout)
            )

        self._client = anthropic.Anthropic(**kwargs)
        self._effort = tier.extra.get("effort")

    def stream(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        cache_system: bool = False,
        on_delta=None,
    ) -> LLMResponse:
        """Anthropic SDK'sının akış yardımcısıyla metni parça parça verir.

        `messages.stream()` bir bağlam yöneticisi; `text_stream` yalnızca metin
        parçalarını üretiyor (düşünme blokları hariç), `get_final_message()` ise
        akış bittikten sonra kullanım/maliyet muhasebesini eksiksiz veriyor —
        yani akışa geçmek maliyet takibini kaybettirmiyor.
        """
        import anthropic

        params = self._params(system, user, max_tokens, None, cache_system)
        started = time.perf_counter()
        try:
            with self._client.messages.stream(**params) as stream:
                for chunk in stream.text_stream:
                    if on_delta and chunk:
                        on_delta(chunk)
                msg = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic bağlantı hatası: {exc}") from exc

        return self._to_response(msg, (time.perf_counter() - started) * 1000)

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        cache_system: bool = False,
    ) -> LLMResponse:
        import anthropic

        params = self._params(system, user, max_tokens, stop, cache_system)
        started = time.perf_counter()
        try:
            msg = self._client.messages.create(**params)
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic bağlantı hatası: {exc}") from exc

        return self._to_response(msg, (time.perf_counter() - started) * 1000)

    # --- iki yolun paylaştığı parçalar --------------------------------------
    #
    # `complete` ve `stream` aynı istek gövdesini ve aynı maliyet muhasebesini
    # kullanmak zorunda. Ayrı ayrı yazılsalardı biri güncellenip diğeri
    # unutulurdu; prompt cache fiyatlandırması gibi ince bir ayrıntıda bu
    # sessiz bir maliyet hatasına dönerdi.

    def _params(self, system, user, max_tokens, stop, cache_system) -> dict:
        system_blocks = [{"type": "text", "text": system}]
        if cache_system:
            # Sabit sistem prompt'unu cache breakpoint'i yap; dinamik few-shot'lar
            # bu noktadan SONRA, user mesajının içinde gelir.
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}

        params: dict = {
            "model": self.tier.model,
            "max_tokens": max_tokens or self.tier.max_tokens,
            "system": system_blocks,
            "messages": [{"role": "user", "content": user}],
        }
        if stop:
            params["stop_sequences"] = stop
        if self._effort:
            params["output_config"] = {"effort": self._effort}
        return params

    def _to_response(self, msg, latency_ms: float) -> LLMResponse:
        if getattr(msg, "stop_reason", None) == "refusal":
            raise LLMError("Model güvenlik sınıflandırıcısı isteği reddetti (refusal)")

        text = "".join(
            block.text for block in msg.content if getattr(block, "type", None) == "text"
        )

        usage = msg.usage
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0
        created = getattr(usage, "cache_creation_input_tokens", 0) or 0

        # Cache okuması %10, cache yazımı %125 fiyatlanır.
        billable_in = in_tok + created * 1.25 + cached * 0.10
        cost = self.tier.cost(int(billable_in), out_tok)

        return LLMResponse(
            text=text,
            model=self.tier.model,
            provider="anthropic",
            input_tokens=in_tok + created + cached,
            output_tokens=out_tok,
            cached_input_tokens=cached,
            cost_usd=cost,
            latency_ms=latency_ms,
            raw=msg,
        )
