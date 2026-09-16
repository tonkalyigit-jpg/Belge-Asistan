"""Yükleme anında belge özeti.

Kullanıcı PDF'i attıktan kısa süre sonra "şunu okudum, içinde şunlar var"
cevabını görüyor. Özet ayrıca indekse de giriyor: geniş sorular gövdedeki tek
bir cümleden çok özetle eşleşiyor.

UZUN BELGE İKİ AŞAMADA özetleniyor. Gemini'nin bağlamı tüm belgeyi tek seferde
alabilir; ama local modele geçişte 8-32K token'lık bir bağlam olacak ve o gün
bu dosyayı yeniden yazmak istemiyoruz. Pencere boyutu `ozet.window_chars`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import config
from llm import registry

from .pdf import Page, clean


@dataclass
class Ozet:
    title: str
    kind: str
    text: str
    responses: list = field(default_factory=list)


def _sayfali_metin(pages: list[Page]) -> str:
    return "\n\n".join(f"=== Sayfa {p.page_no} ===\n{clean(p.text)}" for p in pages if p.text.strip())


def _pencereler(pages: list[Page], limit: int) -> list[list[Page]]:
    """Sayfaları sınırı aşmayan gruplara ayırır; sayfa ORTASINDAN bölmez."""
    gruplar: list[list[Page]] = []
    simdiki: list[Page] = []
    boyut = 0
    for p in pages:
        uzunluk = len(p.text)
        if simdiki and boyut + uzunluk > limit:
            gruplar.append(simdiki)
            simdiki, boyut = [], 0
        simdiki.append(p)
        boyut += uzunluk
    if simdiki:
        gruplar.append(simdiki)
    return gruplar


def _ayikla(metin: str, varsayilan_baslik: str) -> tuple[str, str, str]:
    """Modelin çıktısından başlık, tür ve özet gövdesini ayırır."""
    metin = re.sub(r"^```[a-z]*\n|\n```$", "", metin.strip()).strip()
    baslik, tur = varsayilan_baslik, ""
    govde: list[str] = []
    for satir in metin.split("\n"):
        yalın = satir.strip()
        if yalın.lower().startswith("başlık:") and baslik == varsayilan_baslik:
            baslik = yalın.split(":", 1)[1].strip() or varsayilan_baslik
        elif yalın.lower().startswith("tür:") and not tur:
            tur = yalın.split(":", 1)[1].strip()
        else:
            govde.append(satir)
    return baslik, tur, "\n".join(govde).strip()


def baslik_duzelt(baslik: str) -> str:
    """Tamamen büyük harfli başlığı Türkçe kurallarla başlık yazımına çevirir.

    Model belgenin kendi başlığını aynen alıyor ve kurumsal belgelerde başlık
    çoğu zaman büyük harfle yazılı: aynı listede "Kuzey Lojistik A.Ş. Tedarik
    Sözleşmesi" ile "GÜNEY BİLİŞİM LTD. ŞTİ. TEDARİK SÖZLEŞMESİ" yan yana
    çıkıyordu. Python'un `title()` işlevi Türkçeyi bilmiyor ("BİLİŞİM" ->
    "Bi̇li̇şi̇m"), bu yüzden i/ı eşlemesi elle.

    İçinde nokta olan kısaltmalar ("A.Ş.") olduğu gibi kalıyor; sonda noktası
    olanlar ("LTD.", "ŞTİ.") başlık yazımına dönüyor.
    """
    harfler = [ch for ch in baslik if ch.isalpha()]
    if not harfler or not baslik.isupper():
        return baslik

    def kucuk(metin: str) -> str:
        return metin.replace("I", "ı").replace("İ", "i").lower()

    def buyuk(ch: str) -> str:
        return {"i": "İ", "ı": "I"}.get(ch, ch.upper())

    kelimeler = []
    for kelime in baslik.split():
        if "." in kelime.rstrip("."):          # A.Ş., T.C.
            kelimeler.append(kelime)
        else:
            k = kucuk(kelime)
            kelimeler.append(buyuk(k[0]) + k[1:] if k else k)
    return " ".join(kelimeler)


def ozetle(pages: list[Page], filename: str) -> Ozet:
    """Belgenin başlığını, türünü ve özetini üretir. Hata LLMError olarak yükselir."""
    varsayilan = filename.rsplit(".", 1)[0].replace("_", " ")
    limit = int(config.get("ozet.window_chars", 24000))
    gruplar = _pencereler([p for p in pages if p.text.strip()], limit)
    responses = []

    if len(gruplar) <= 1:
        malzeme = _sayfali_metin(gruplar[0] if gruplar else [])
    else:
        # 1. aşama: her pencere için not. Kullanıcıya gitmiyor, ara ürün.
        notlar = []
        for grup in gruplar:
            resp = registry.tier("writer").complete(
                system=config.prompt("ozet_pencere"),
                user=_sayfali_metin(grup),
            )
            responses.append(resp)
            notlar.append(
                f"=== Sayfa {grup[0].page_no} ===\n"
                f"(sayfa {grup[0].page_no}-{grup[-1].page_no} notları)\n{resp.text.strip()}"
            )
        malzeme = "\n\n".join(notlar)

    resp = registry.tier("strong").complete(system=config.prompt("ozet"), user=malzeme)
    responses.append(resp)
    baslik, tur, govde = _ayikla(resp.text, varsayilan)
    return Ozet(title=baslik_duzelt(baslik), kind=tur, text=govde, responses=responses)
