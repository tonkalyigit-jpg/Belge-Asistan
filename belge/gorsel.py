"""Sayfadaki şekilleri bulup vision modele betimletir.

NEDEN: metin çıkarımı yalnızca YAZIYI görüyor. Bir mimari şemadaki oklar, bir
grafikteki eğri, bir çizimdeki eksenler metin katmanında yok — ve gömülü bir
görselin İÇİNDEKİ yazı da yok, çünkü OCR yalnızca metin katmanı boş olan
sayfalarda çalışıyor. ÖLÇÜLDÜ: "IMS mimari şemasında hangi bileşenler var?"
sorusuna sistem yalnızca etiket metinlerini sayabildi ("2G/3G, VoLTE…"),
neyin neye bağlandığını söyleyemedi.

NASIL: şekil içeren sayfa render edilip modele gönderiliyor; model hem
görseldeki yazıları hem yapıyı (bileşenler, bağlantılar, eksenler, değerler)
yazıyor. Çıkan metin AYRI bir chunk olarak indeksleniyor ve "Şekil (s. N)"
diye etiketleniyor — belgenin kendi cümlesi değil, modelin görselden okuduğu
şey; cevapta bu ayrım korunmalı.

MALİYET: sayfa başına bir vision çağrısı, YALNIZCA yükleme sırasında ve
yalnızca şekil taşıyan sayfalarda. Betimlemeler veritabanında saklanıyor;
yeniden parçalama bunları tekrar üretmiyor.
"""
from __future__ import annotations

import io

import config
from llm import registry
from llm.base import LLMError

# Sayfanın şekil taşıdığına karar veren eşikler. ÖLÇÜLDÜ (yüklü belgeler):
#   - ürün fotoğrafı/kapak görseli: sayfanın %37-54'ü
#   - mimari şema (vektör çizim): 104 çizgi + 408 eğri
#   - matematik şekli: 85 çizgi
#   - düz metin sayfası: 0-10 çizgi, görsel yok
_MIN_GORSEL_ALAN = 0.08       # gömülü görsel sayfanın bu kadarını kaplıyorsa
_MIN_CIZIM = 25               # çizgi + eğri + dikdörtgen toplamı


def sekilli_sayfalar(data: bytes, *, en_fazla: int | None = None) -> list[int]:
    """Şekil taşıyan sayfaların numaraları, güçlü sinyalden zayıfa.

    Sıra önemli: bütçe dolduğunda en zayıf sinyalli sayfa dışarıda kalsın.
    """
    try:
        import pdfplumber
    except ImportError:              # pragma: no cover - kurulumda var
        return []

    en_fazla = en_fazla if en_fazla is not None else int(
        config.get("belge.max_gorsel_sayfa", 20))
    puanlar: list[tuple[float, int]] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for no, sayfa in enumerate(pdf.pages, 1):
                alan = (sayfa.width or 1) * (sayfa.height or 1)
                gorsel = sum((im["x1"] - im["x0"]) * (im["bottom"] - im["top"])
                             for im in sayfa.images) / alan
                cizim = len(sayfa.lines) + len(sayfa.curves) + len(sayfa.rects)
                if gorsel >= _MIN_GORSEL_ALAN or cizim >= _MIN_CIZIM:
                    # Puan iki sinyali tek ölçeğe getiriyor; kıyas için yeterli.
                    puanlar.append((gorsel + cizim / 200, no))
    except Exception:
        return []

    puanlar.sort(reverse=True)
    return [no for _, no in puanlar[:en_fazla]]


def betimle(goruntu, sayfa_no: int) -> str:
    """Sayfadaki şekli betimler. Model çağrısı başarısızsa boş metin."""
    tampon = io.BytesIO()
    goruntu.convert("RGB").save(tampon, format="JPEG", quality=88, optimize=True)
    try:
        resp = registry.tier("vision").complete_vision(
            system=config.prompt("gorsel"),
            user=f"Sayfa {sayfa_no}. Bu sayfadaki şekli/görseli betimle.",
            # (veri, mime) çifti: sağlayıcı data URI'yi böyle kuruyor.
            images=[(tampon.getvalue(), "image/jpeg")],
        )
    except LLMError:
        return ""
    metin = (resp.text or "").strip()
    # Model "şekil yok" derse chunk üretmiyoruz: boş bir kayıt aramada yer
    # kaplar ve cevapta "belgede şu var" diye görünür.
    if not metin or metin.upper().startswith("ŞEKİL YOK"):
        return ""
    return metin
