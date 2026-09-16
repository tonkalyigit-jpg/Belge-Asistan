"""Hesap denetimi — cevaptaki `Hesap:` satırları yazılımla yeniden yapılıyor."""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.nodes import hesap


@pytest.mark.parametrize("metin, deger", [
    ("4.850.000", "4850000"), ("21.600.000", "21600000"),
    ("15,840", "15840"),                  # İngilizce binlik (G9 veri sayfası)
    ("57,6", "57.6"), ("57.6", "57.6"),   # iki dilde ondalık
    ("0,003", "0.003"),                   # tam kısım 0 -> ondalık, binlik değil
    ("1.234,5", "1234.5"), ("1,234.5", "1234.5"),
])
def test_sayi_bicimleri(metin, deger):
    assert hesap.sayi(metin) == Decimal(deger)


def test_dogru_hesaplar_gecer():
    cevap = """**Kısa cevap:** 485.000 TL.
Hesap: 4.850.000 TL × ‰3 × 40 gün = 582.000 TL
**Hesap:** 4.850.000 TL × %10 = 485.000 TL (tavan)
Hesap: 21.600.000 TL × binde 5 × 60 = 6.480.000 TL
Hesap: 15,840 + 19,800 = 35,640
Hesap: 57,6 − 40,8 = 16,8 V"""
    sonuclar = hesap.denetle(cevap)
    assert len(sonuclar) == 5 and all(h.dogru for h in sonuclar)


def test_yanlis_carpim_yakalaniyor():
    [h] = hesap.denetle("Hesap: 4.850.000 × 0,003 × 40 = 58.200 TL")
    assert not h.dogru and h.beklenen == Decimal("582000")
    assert "582.000" in hesap.ipucu([h])


def test_binde_yuzde_karisikligi_yakalaniyor():
    """‰5 yerine %5 ile hesaplamak on kat fark."""
    [h] = hesap.denetle("Hesap: 21.600.000 TL × ‰5 × 10 = 10.800.000 TL")
    assert not h.dogru and h.beklenen == Decimal("1080000")


def test_hesap_olmayan_satirlar_atlaniyor():
    assert hesap.denetle("Hesap: 582.000 TL > 485.000 TL → tavan uygulanır\nHesap: toplam 485.000 TL") == []


def test_zincir_hesap():
    sonuclar = hesap.denetle("Hesap: 108.000 × 60 = 6.480.000 = 6.500.000")
    assert [h.dogru for h in sonuclar] == [True, False]
