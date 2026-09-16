"""Taranmış sayfa görüntüsü için ön işleme.

Her adım ayrı açılıp kapanabiliyor, çünkü "ön işleme OCR'ı iyileştirir" genel
bir doğru değil — modele ve dile bağlı:

  * Görsel-dil modelleri doğal görüntüyle eğitildi; klasik OCR için şart olan
    ikili hale getirme (siyah-beyaz) onlarda ince çizgileri yok edip sonucu
    kötüleştirebiliyor. Bu yüzden burada hiç yok.
  * Medyan filtre tuz-biber gürültüsünü temizler ama düşük çözünürlükte
    Türkçenin ayırt edici işaretlerini de silebilir: ı/i noktası, ş/ç çengeli,
    ö/ü noktaları. İngilizcede zararsız olan adım Türkçede "şart" -> "sart"
    yapabilir.

Hangi adımların varsayılan açık olacağı scripts/ocr_olc.py ile ölçülerek
belirlenir, tahminle değil.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter, ImageOps


@dataclass(frozen=True)
class Adimlar:
    egiklik: bool = True      # eğik yerleştirilmiş kâğıdı düzelt
    kontrast: bool = True     # soluk toneri belirginleştir
    gurultu: bool = False     # medyan filtre — Türkçe işaretleri silebilir, ölçülecek


def _otsu(dizi: np.ndarray) -> float:
    """Gri ton histogramından mürekkep/kâğıt ayrım eşiği."""
    hist, _ = np.histogram(dizi, bins=256, range=(0, 256))
    toplam = dizi.size
    agirlik = np.cumsum(hist)
    ortalama = np.cumsum(hist * np.arange(256))
    genel = ortalama[-1]
    en_iyi, esik = -1.0, 128.0
    for t in range(1, 255):
        w0 = agirlik[t]
        w1 = toplam - w0
        if w0 == 0 or w1 == 0:
            continue
        m0 = ortalama[t] / w0
        m1 = (genel - ortalama[t]) / w1
        varyans = w0 * w1 * (m0 - m1) ** 2
        if varyans > en_iyi:
            en_iyi, esik = varyans, float(t)
    return esik


def egiklik_acisi(goruntu: Image.Image, *, aralik: float = 6.0) -> float:
    """Metin satırlarının yataydan sapma açısı (derece).

    İzdüşüm profili yöntemi: doğru açıda satırlar yatay olduğu için satır
    toplamları "dolu satır / boş satır aralığı" diye keskinleşir ve varyans
    en yükseğe çıkar. Önce kaba (0.5°), sonra ince (0.1°) tarama.
    """
    kucuk = goruntu.convert("L")
    oran = 900 / max(kucuk.size)
    if oran < 1:
        kucuk = kucuk.resize((int(kucuk.width * oran), int(kucuk.height * oran)))
    dizi = np.asarray(kucuk, dtype=np.float32)
    murekkep = Image.fromarray(((dizi < _otsu(dizi)) * 255).astype(np.uint8))

    def puan(aci: float) -> float:
        dondu = np.asarray(murekkep.rotate(aci, resample=Image.NEAREST, fillcolor=0))
        return float(np.var(dondu.sum(axis=1)))

    kaba = max(np.arange(-aralik, aralik + 1e-9, 0.5), key=puan)
    ince = max(np.arange(kaba - 0.5, kaba + 0.5 + 1e-9, 0.1), key=puan)
    return float(round(ince, 2))


def hazirla(goruntu: Image.Image, adimlar: Adimlar = Adimlar()) -> tuple[Image.Image, dict]:
    """Görüntüyü OCR'a hazırlar. İkinci değer uygulanan işlemlerin kaydı."""
    g = goruntu.convert("L")
    kayit: dict = {}

    if adimlar.egiklik:
        aci = egiklik_acisi(g)
        # 0.2°'nin altı gürültü: düzeltmek yeniden örnekleme bulanıklığı katar,
        # hiçbir şey kazandırmaz.
        if abs(aci) >= 0.2:
            g = g.rotate(aci, resample=Image.BICUBIC, expand=True, fillcolor=255)
        kayit["egiklik_derece"] = aci

    if adimlar.kontrast:
        g = ImageOps.autocontrast(g, cutoff=1)
        kayit["kontrast"] = True

    if adimlar.gurultu:
        g = g.filter(ImageFilter.MedianFilter(3))
        kayit["gurultu"] = True

    return g, kayit
