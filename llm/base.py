"""Sağlayıcıdan bağımsız LLM arayüzü.

Pipeline'daki hiçbir düğüm somut bir sağlayıcı tanımaz; yalnızca `LLMProvider`
sözleşmesini bilir. Model/sağlayıcı değişimi config/settings.yaml'dan yapılır.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    """Bir LLM çağrısının metni ve maliyet muhasebesi."""

    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    # Hız sınırı yüzünden uyunan süre. Sağlayıcı 429 alıp beklediğinde bu
    # süre kullanıcıya "düşünülüyor" olarak görünüyordu; ölçümde tek bir
    # sorguda 28.4 sn buradan geldi (bkz. observability/trace.py notları).
    retry_wait_s: float = 0.0
    retries: int = 0
    raw: Any = field(default=None, repr=False)


class LLMError(RuntimeError):
    """Sağlayıcıdan bağımsız LLM hatası."""


@dataclass
class TierConfig:
    """settings.yaml `tiers.<ad>` bloğunun tiplenmiş hali."""

    name: str
    provider: str
    model: str
    price_in: float = 0.0     # USD / 1M input token
    price_out: float = 0.0    # USD / 1M output token
    max_tokens: int = 2048
    base_url: str | None = None
    api_key_env: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "TierConfig":
        known = {
            "provider", "model", "price_in", "price_out",
            "max_tokens", "base_url", "api_key_env",
        }
        return cls(
            name=name,
            provider=data["provider"],
            model=data["model"],
            price_in=float(data.get("price_in", 0.0)),
            price_out=float(data.get("price_out", 0.0)),
            max_tokens=int(data.get("max_tokens", 2048)),
            base_url=data.get("base_url"),
            api_key_env=data.get("api_key_env"),
            extra={k: v for k, v in data.items() if k not in known},
        )

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000


class LLMProvider(ABC):
    """Tüm sağlayıcı adapterlarının uyduğu sözleşme."""

    def __init__(self, tier: TierConfig):
        self.tier = tier

    @property
    def model(self) -> str:
        return self.tier.model

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        cache_system: bool = False,
    ) -> LLMResponse:
        """Düz metin tamamlama.

        `cache_system=True` ise sağlayıcı destekliyorsa sistem prompt'u
        prompt-cache breakpoint'i olarak işaretlenir.
        """

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
        """`complete` ile aynı sonucu döner, ama metni geldikçe `on_delta`'ya verir.

        Toplam süreyi DEĞİŞTİRMEZ; algılanan gecikmeyi değiştirir. Ölçüldü:
        üretim adımı tek bir sorgunun 24.5 saniyesinin 18.7'siydi (%76) ve bu
        süre boyunca kullanıcı boş ekrana bakıyordu.

        KAZANÇ SAĞLAYICIYA GÖRE DEĞİŞİYOR ve abartılmamalı. Ölçüldü
        (2026-09-04, Gemini ücretsiz katman, 399 çıktı token'ı): ilk parça
        13.2 sn, toplam 14.8 sn — yani %11 erken. Sağlayıcı çıktıyı kendisi
        tamponluyor. Anında akıtan bir sağlayıcıda fark çok daha büyük olur.

        Varsayılan uygulama akış YAPMAZ: `complete`'i çağırır ve metni tek
        parça olarak verir. Böylece akışı desteklemeyen bir sağlayıcı eklendiğinde
        çağıran taraf değişmek zorunda kalmaz — sadece kesintisiz akış yerine
        tek seferde dolu bir ekran görür.
        """
        kwargs = {"max_tokens": max_tokens, "cache_system": cache_system}
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = self.complete(system, user, **kwargs)
        if on_delta and response.text:
            on_delta(response.text)
        return response

    def complete_vision(
        self,
        system: str,
        user: str,
        images: list[tuple[bytes, str]],
        *,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Metin + görsel girdiyle tamamlama. `images`: (bayt, mime) çiftleri.

        Varsayılan uygulama DESTEKLEMİYOR ve bunu açıkça söylüyor. Sessizce
        yalnızca metni gönderen bir varsayılan, taranmış sayfayı hiç görmeyen
        bir modelin "okuduğu" metni uydurması demekti — boş bir sayfa değil,
        kendinden emin bir yalan dönerdi.
        """
        raise LLMError(
            f"{type(self).__name__} ({self.tier.model}) görsel girdiyi desteklemiyor; "
            f"`vision` kademesine görsel destekli bir model tanımlayın"
        )

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        cache_system: bool = False,
    ) -> tuple[dict[str, Any], LLMResponse]:
        """JSON dönmesi beklenen çağrı.

        Ayıklama metin üzerinden yapılır; sağlayıcıya özgü structured-output
        özelliklerine bilerek girilmiyor, çünkü kademeler farklı sağlayıcılara
        bağlanabiliyor ve bench karşılaştırmasının adil kalması gerekiyor.

        Küçük modeller (8B sınıfı) düzenli olarak bozuk JSON üretiyor: açıklama
        cümlesi ekliyor, kod bloğunu kapatmıyor, tek tırnak kullanıyor. Bu
        yüzden bir kez, hatayı modele göstererek yeniden deneniyor — retry'nin
        maliyeti, düğümün tamamen düşmesinden çok daha az.
        """
        instruction = "\n\nRespond with a single valid JSON object and nothing else."
        resp = self.complete(
            system=system + instruction,
            user=user,
            max_tokens=max_tokens,
            cache_system=cache_system,
        )
        try:
            return extract_json(resp.text), resp
        except LLMError as first_error:
            retry = self.complete(
                system=system + instruction,
                user=(
                    f"{user}\n\n"
                    f"Your previous reply could not be parsed as JSON "
                    f"({first_error}). Reply again with ONLY the JSON object: "
                    f"no prose before or after it, no markdown fence, "
                    f"double-quoted keys and strings."
                ),
                max_tokens=max_tokens,
                cache_system=cache_system,
            )
            # Maliyet iki çağrının toplamı; çağıran tek bir yanıt görüyor.
            retry.input_tokens += resp.input_tokens
            retry.output_tokens += resp.output_tokens
            retry.cost_usd += resp.cost_usd
            return extract_json(retry.text), retry


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Model çıktısından ilk geçerli JSON nesnesini çıkarır.

    Modeller JSON'u kod bloğuna sarabiliyor veya öncesinde açıklama yazabiliyor;
    bu yüzden ham `json.loads` yeterli değil.
    """
    text = (text or "").strip()
    if not text:
        raise LLMError("Model boş yanıt döndürdü, JSON beklenmişti")

    candidates: list[str] = []
    fence = _JSON_FENCE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(text)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}

    raise LLMError(f"Yanıttan JSON ayıklanamadı: {text[:200]!r}")


def ipv4_transport(module: str = "httpx"):
    """`network.force_ipv4` açıksa IPv4'e sabitlenmiş bir transport döner.

    Neden gerekli: sağlayıcı alan adları hem IPv6 hem IPv4 adresi döndürüyor.
    IPv6 yolu yarım çalışan bir ağda (ölçüldü: mobil hotspot) bağlantı kurulumu
    zaman aşımını bekleyip IPv4'e düşüyor ve İLK istek 77 saniye sürüyor —
    sonrakiler aynı bağlantıyı kullandığı için 2 saniye. Belirti bu yüzden
    "ilk soru donuyor, gerisi normal" oluyor ve kod hatası sanılıyor.

    `module` parametresi zorunlu bir ayrım: Anthropic SDK 1.x `httpx` DEĞİL
    `httpx2` üzerine kurulu ve yabancı bir istemci tipini reddediyor
    (TypeError: Expected an instance of `httpx2.Client`). İki sağlayıcı aynı
    davranışı paylaşsın diye mantık burada tek yerde duruyor, kütüphane farkı
    parametreyle geçiliyor.

    None dönerse çağıran varsayılan davranışı kullanır.
    """
    import importlib

    import config

    if not config.get("network.force_ipv4", False):
        return None

    lib = importlib.import_module(module)
    # local_address="0.0.0.0" soketi IPv4 ailesine bağlar; IPv6 adayları
    # hiç denenmez.
    return lib.HTTPTransport(local_address="0.0.0.0")
