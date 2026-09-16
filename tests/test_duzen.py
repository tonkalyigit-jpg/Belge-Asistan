"""Sayfa düzeni: iki sütunlu sayfa ve tam genişlikteki satırlar.

Gerçek PDF yerine kelime koordinatları kuruluyor: test edilen şey pdfplumber'ın
metin çıkarması değil, BİZİM kolon kararımız. Koordinatlar G9 veri sayfasının
3. sayfasından ölçüldü (612 punto genişlik, kolon boşluğu ~294-306).
"""
from belge import duzen


def kelime(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 10}


def iki_kolonlu_sayfa():
    """Sol kolon 60-290, sağ kolon 320-560, altta tam genişlikte telif satırı."""
    kelimeler = []
    for i in range(10):
        kelimeler += [kelime(f"sol{i}", 60, 290, 100 + i * 12),
                      kelime(f"sag{i}", 320, 560, 100 + i * 12)]
    kelimeler.append(kelime("Copyright-Ribbon-All-Rights-Reserved", 60, 560, 400))
    return kelimeler


def test_iki_kolon_ayriliyor_ve_kolonlar_kendi_icinde_okunuyor():
    kelimeler = iki_kolonlu_sayfa()
    sinir = duzen._kolon_siniri(kelimeler, 612)
    assert sinir is not None and 290 < sinir < 320

    metin = duzen._iki_kolon(kelimeler, sinir)
    satirlar = metin.split("\n")
    # Sol kolon bitmeden sağ kolon başlamıyor: eski çıkarıcı bunları satır
    # satır iç içe basıyordu ("• Weight: 80 lbs chassis only safety").
    assert satirlar[:3] == ["sol0", "sol1", "sol2"]
    assert "sag0" in satirlar[10]
    assert satirlar.index("sol9") < satirlar.index("sag0")


def test_tam_genislikteki_satir_kolon_kararini_bozmuyor():
    """Altbilgi her aday çizgiyi kesiyor; yine de sayfa iki sütun sayılmalı."""
    kelimeler = iki_kolonlu_sayfa()
    assert duzen._kolon_siniri(kelimeler, 612) is not None
    metin = duzen._iki_kolon(kelimeler, duzen._kolon_siniri(kelimeler, 612))
    assert metin.strip().endswith("Copyright-Ribbon-All-Rights-Reserved")


def test_tek_sutunlu_sayfa_bolunmuyor():
    kelimeler = [kelime(f"satir{i}", 60, 550, 100 + i * 12) for i in range(20)]
    assert duzen._kolon_siniri(kelimeler, 612) is None


def test_markdown_tablosu_basliksiz_hucreleri_dolduruyor():
    satirlar = [["Port", "Kapasite"], ["T1", "15.840"], ["E1"]]
    md = duzen._markdown(satirlar, 2)
    assert md.splitlines()[0] == "| Port | Kapasite |"
    assert md.splitlines()[1] == "| --- | --- |"
    assert md.splitlines()[-1] == "| E1 |  |"
