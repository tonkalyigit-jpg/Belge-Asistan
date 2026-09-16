"""Taranmış sayfayı metne çevirme — görsel-dil modeli üzerinden.

Klasik OCR (Tesseract) yerine görsel model, çünkü kötü taramada ve karmaşık
düzende belirgin şekilde daha iyi; ayrıca sisteme ek kurulum gerektirmiyor.
Model `vision` kademesinden geliyor: bugün Gemini, local'e geçişte görsel
destekli bir model — bu dosya değişmiyor.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import config
from llm import registry
from llm.base import LLMResponse

from .goruntu import Adimlar, hazirla

_FENCE = re.compile(r"^```[a-zA-Z]*\n|\n```$")

# Binde işaretinin (‰) bilinen okuma hatası: "%o5". Bu dizi Türkçede hiçbir
# zaman geçerli değil, dolayısıyla düzeltmesi güvenli. ÖLÇÜLDÜ: 42 sayfa
# okumasındaki TEK hata türü bu işaretti ("‰3" -> "%o3", "‰5" -> "%5").
# "%5" biçimi kurtarılamıyor — gerçek bir yüzde de olabilir; o yüzden yalnızca
# kesin olan düzeltiliyor ve geri kalanı prompt'a bırakılıyor. Prompt'a ‰/%
# farkı yazıldıktan sonra üçüncü bir biçim görüldü: "‰o3" (işaret doğru ama
# fazladan o). O da hiçbir zaman geçerli değil.
_BINDE = re.compile(r"(?:%o|‰o)(?=\s?\d)")


@dataclass
class OkumaSonucu:
    text: str
    response: LLMResponse
    on_isleme: dict


def _jpeg(goruntu) -> bytes:
    tampon = io.BytesIO()
    # Gri ton metin için JPEG kalitesi 88 görsel olarak kayıpsız; PNG aynı
    # sayfada 3-4 kat büyük ve istek boyutunu gereksiz şişiriyor.
    goruntu.save(tampon, "JPEG", quality=88)
    return tampon.getvalue()


def oku(goruntu, page_no: int, *, adimlar: Adimlar | None = None) -> OkumaSonucu:
    """Tek bir sayfa görüntüsünü metne çevirir."""
    kayit: dict = {}
    if adimlar is None and config.get("belge.preprocess", True):
        adimlar = Adimlar()
    if adimlar is not None:
        goruntu, kayit = hazirla(goruntu, adimlar)

    resp = registry.tier("vision").complete_vision(
        system=config.prompt("ocr"),
        user=f"Page {page_no}. Transcribe it.",
        images=[(_jpeg(goruntu), "image/jpeg")],
    )
    text = _BINDE.sub("‰", _FENCE.sub("", resp.text.strip()).strip())
    return OkumaSonucu(text=text, response=resp, on_isleme=kayit)
