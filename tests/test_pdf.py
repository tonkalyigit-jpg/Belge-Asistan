"""PDF okuma ve sayfa farkında parçalama."""
from __future__ import annotations

import pytest

from belge import pdf
from belge.pdf import Page
from tests.ornek_metinler import SOZLESME_A, SOZLESME_B


def test_dijital_pdf_metin_katmani_turkce_karakterleri_koruyor(ornek_pdfler):
    sayfalar = pdf.text_layer(ornek_pdfler["guney"])
    assert len(sayfalar) == len(SOZLESME_B["sayfalar"])
    birlesik = " ".join(sayfalar)
    for parca in ("GÜNEY BİLİŞİM", "Şti.", "21.600.000 TL", "‰5", "%25", "yükümlülüğü"):
        assert parca in birlesik, parca
    assert not any(pdf.needs_ocr(s) for s in sayfalar)


def test_taranmis_pdf_ocra_yonleniyor(ornek_pdfler):
    sayfalar = pdf.text_layer(ornek_pdfler["guney_tarama"])
    assert len(sayfalar) == 2
    assert all(pdf.needs_ocr(s) for s in sayfalar)


@pytest.mark.parametrize("metin, beklenen", [
    ("", True),
    ("   \n  ", True),
    ("Sayfa 3", True),                                   # başlık bile yok denecek kadar az
    ("€€€ ### ~~~ ¤¤¤ " * 30, True),                     # bozuk font eşlemesi: harf değil sembol
    ("Tedarikçi teslim süresine uymazsa gecikme cezası uygulanır. " * 3, False),
])
def test_ocr_karari(metin, beklenen):
    assert pdf.needs_ocr(metin) is beklenen


def test_bozuk_kodlama_ocra_gidiyor():
    metin = "Sözleşme bedeli " * 10 + "�" * 20
    assert pdf.needs_ocr(metin)


def test_pdf_olmayan_dosya_acik_hata_veriyor():
    with pytest.raises(pdf.PdfError):
        pdf.page_count(b"merhaba bu bir pdf degil")


@pytest.mark.parametrize("satir, baslik_mi", [
    ("MADDE 5 - CEZAİ ŞART", True),
    ("Madde 12: Fesih", True),
    ("BİRİNCİ BÖLÜM", True),
    ("GENEL HÜKÜMLER", True),          # Türkçe büyük harfler [A-Z] kalıbına uymaz
    ("3.2 Kapsam", True),
    ("Tel: 0212 555 00 00", False),
    ("Tedarikçi, ürünlerin tamamını 45 gün içinde teslim edecektir.", False),
])
def test_bolum_basligi(satir, baslik_mi):
    assert (pdf._heading(satir) is not None) is baslik_mi


def test_temizlik_nfkc_degil_nfc():
    """NFKC tutarları bozuyordu: "m²" -> "m2", "½" -> "1/2"."""
    assert pdf.clean("Alan 120 m² ve oran ½") == "Alan 120 m² ve oran ½"


def test_satir_sonu_tiresi_birlestiriliyor():
    assert "sözleşme" in pdf.clean("Bu sözleş-\nme imzalanmıştır")


def test_madde_basliklari_ayri_chunk_basliyor():
    sayfalar = [Page(i, t, "text") for i, t in enumerate(SOZLESME_A["sayfalar"], 1)]
    chunks = pdf.chunk_pages(sayfalar)
    bolumler = [c.section for c in chunks]
    assert "MADDE 5 - CEZAİ ŞART" in bolumler
    ceza = next(c for c in chunks if c.section == "MADDE 5 - CEZAİ ŞART")
    # Madde 5'in chunk'ı Madde 6'nın hükmünü taşımamalı
    assert "binde 3" in ceza.text and "garanti" not in ceza.text.lower()
    assert ceza.page_start == ceza.page_end == 2


def test_sayfa_sinirini_asan_paragraf_sayfa_araligi_tasiyor():
    uzun = "Bu hüküm iki sayfaya yayılan uzun bir paragrafın parçasıdır. "
    sayfalar = [Page(1, "MADDE 1 - UZUN HÜKÜM\n" + uzun * 8, "text"), Page(2, uzun * 8, "text")]
    chunks = pdf.chunk_pages(sayfalar, max_chars=2400, overlap_chars=0, min_chars=50)
    assert chunks[0].page_start == 1 and chunks[0].page_end == 2
    assert chunks[0].pages == "s. 1-2"


def test_chunk_boyut_tavanini_asmiyor():
    sayfalar = [Page(1, "kelime " * 3000, "text")]
    chunks = pdf.chunk_pages(sayfalar, max_chars=500, overlap_chars=50, min_chars=100)
    assert len(chunks) > 5
    assert max(len(c.text) for c in chunks) <= 500 + 50 + 20


def test_ortusme_bolum_sinirini_gecmiyor():
    """Madde 5'in son cümlesi Madde 6'nın chunk'ına örtüşme olarak taşınmamalı."""
    metin = ("MADDE 5 - CEZA\n" + "Ceza oranı binde üçtür. " * 40 +
             "\nMADDE 6 - GARANTİ\n" + "Garanti süresi yirmi dört aydır. " * 40)
    chunks = pdf.chunk_pages([Page(1, metin, "text")], max_chars=600, overlap_chars=200, min_chars=50)
    garanti = [c for c in chunks if c.section == "MADDE 6 - GARANTİ"]
    assert garanti and "Ceza oranı" not in garanti[0].text


# --- veri sayfası yapısı -------------------------------------------------------
#
# İlk gerçek belge (bir üreticinin 3 sayfalık veri sayfası) Türkçe sözleşmeye
# göre yazılmış başlık kurallarını ters çalıştırdı: gerçek başlıkları kaçırdı,
# kısaltma listesini ve ölçü satırını başlık sanıp listeleri ortasından böldü.
# Aşağıdaki PDF o yapının küçük bir kopyası.

def _veri_sayfasi() -> bytes:
    from fpdf import FPDF

    d = FPDF(format="A4")
    d.add_font("A", "", "/System/Library/Fonts/Supplemental/Arial.ttf")
    d.add_font("A", "B", "/System/Library/Fonts/Supplemental/Arial Bold.ttf")
    govde = "The gateway supports trunking and access applications in carrier networks. " * 3
    sayfalar = [
        [("B", "Converged Media Gateway"), ("", "Data Sheet"),
         ("B", "Expansive Interoperability"), ("", govde),
         ("", "When coupled with"), ("", "Ribbon Communication's"), ("", "call controllers,"),
         ("", govde)],
        [("B", "Converged Media Gateway"), ("", "2 Data Sheet"),
         ("B", "Protocols, Interfaces, Interoperability"),
         ("", "MFR2, PRI, NFAS, TBCT, MF CAS, V5.2, SS7"), ("", govde),
         ("B", "Capacities"), ("", "• DS0s: T1 - 15,840; E1 - 19,800;"),
         ("", "OC3 - 96,768; STM-1 - 90,720"), ("", "• IP: 4 - 8 Gigabit Ethernet")],
        [("B", "Converged Media Gateway"), ("", "3 Data Sheet"),
         ("B", "Dimensions"), ("", "• HxWxD: 15U, 26.25 x 17.38 x 18.50;"),
         ("", "66.68 x 44.15 x 47.00 cm"), ("", "• Weight: 80 lbs chassis only"),
         ("B", "Power"), ("", "• Power Consumption: 3,000 watts maximum")],
    ]
    for satirlar in sayfalar:
        d.add_page()
        for stil, metin in satirlar:
            d.set_font("A", stil, 11 if stil else 10)
            d.multi_cell(0, 6, metin, new_x="LMARGIN", new_y="NEXT")
    return bytes(d.output())


def _veri_sayfasi_chunklari():
    veri = _veri_sayfasi()
    agir = pdf.heading_lines(veri)
    sayfalar = [Page(i, t, "text", frozenset(agir[i - 1])) for i, t in enumerate(pdf.text_layer(veri), 1)]
    return agir, pdf.chunk_pages(sayfalar, max_chars=2400, overlap_chars=300, min_chars=50)


def test_kalin_basliklar_yazi_agirligindan_bulunuyor():
    agir, _ = _veri_sayfasi_chunklari()
    assert "Capacities" in agir[1] and "Power" in agir[2]
    assert "Protocols, Interfaces, Interoperability" in agir[1]      # virgüllü ama başlık
    assert "Ribbon Communication's" not in agir[0]                   # dar kolon satırı, gövde


def test_kisaltma_listesi_ve_olcu_satiri_baslik_sayilmiyor():
    _, chunks = _veri_sayfasi_chunklari()
    bolumler = {c.section for c in chunks}
    # Kısa bölümler komşusuyla birleşince adları "Power · Compliances" gibi
    # birleşik görünebiliyor; aranan, adın bir yerde GEÇMESİ.
    tum = " || ".join(bolumler)
    for ad in ("Capacities", "Dimensions", "Power"):
        assert ad in tum, ad
    for yanlis in ("MFR2, PRI, NFAS, TBCT, MF CAS, V5.2, SS7", "OC3 - 96,768; STM-1 - 90,720",
                   "66.68 x 44.15 x 47.00 cm", "2 Data Sheet", "Ribbon Communication's"):
        assert yanlis not in tum, yanlis


def test_liste_ortasindan_bolunmuyor():
    _, chunks = _veri_sayfasi_chunklari()
    # Bölüm adı birleşmiş olabilir ("Dimensions · Power"); aranan, listenin
    # ikiye bölünmemiş olması — iki uç da AYNI parçada.
    kapasite = next(c for c in chunks if "Capacities" in c.section)
    assert "T1 - 15,840" in kapasite.text and "STM-1 - 90,720" in kapasite.text
    boyut = next(c for c in chunks if "Dimensions" in c.section)
    assert "26.25" in boyut.text and "66.68 x 44.15" in boyut.text


def test_tekrarlayan_ust_bilgi_ayiklaniyor():
    """Birimlere doğrudan bakılıyor, chunk'lara değil.

    İlk sürüm chunk metnine bakıyordu ve temizlik kapatıldığında da geçti:
    üst bilgi tek başına 34 karakterlik bir chunk oluşturuyor, onu da 40
    karakterden kısa chunk'ları atan ayrı bir süzgeç siliyordu. Test başka
    bir mekanizmayı ölçüyordu.
    """
    veri = _veri_sayfasi()
    agir = pdf.heading_lines(veri)
    sayfalar = [Page(i, t, "text", frozenset(agir[i - 1])) for i, t in enumerate(pdf.text_layer(veri), 1)]
    birimler = [metin for metin, _, _ in pdf._units(sayfalar, 2400)]
    assert "Converged Media Gateway" not in birimler
    assert not any(b.endswith("Data Sheet") for b in birimler)


def test_sayfa_basi_madde_satirlari_ust_bilgi_sanilmiyor():
    """Rakamlar atılınca "MADDE 4" ile "MADDE 7" aynı satır sayılıp silinirdi."""
    sayfalar = [Page(1, "MADDE 4\nTeslim süresi otuz gündür. " * 5, "text"),
                Page(2, "MADDE 7\nGizlilik beş yıl sürer. " * 5, "text")]
    chunks = pdf.chunk_pages(sayfalar, max_chars=2400, overlap_chars=0, min_chars=10)
    assert {"MADDE 4", "MADDE 7"} <= {c.section for c in chunks}


def test_gorunmez_satir_sonu_tiresi_kelimeyi_birlestiriyor():
    """pypdfium2 satır sonu tiresini U+FFFE döndürüyor: "plat￾\nforms"."""
    assert pdf.clean("media processing plat￾\nforms and net￾work") == "media processing platforms and network"
    assert pdf.clean("yumuşak­\ntire") == "yumuşaktire"


def test_kalin_sema_etiketleri_indeksten_dusmuyor():
    """Ribbon IMS s. 3: kalın şema etiketleri art arda başlık sanılıp gövdesiz
    bölümler açtı, 40 karakter süzgeci de hepsini sildi. "POTS" indekste yoktu."""
    from fpdf import FPDF

    d = FPDF(format="A4")
    d.add_font("A", "", "/System/Library/Fonts/Supplemental/Arial.ttf")
    d.add_font("A", "B", "/System/Library/Fonts/Supplemental/Arial Bold.ttf")
    d.add_page()
    # Gövde metni baskın olmalı: ağırlık eşiği sayfanın BASKIN ağırlığına göre
    # kuruluyor ve sayfanın çoğu kalın olunca hiçbir satır başlık sayılmıyor.
    govde = "MNOs can deploy the voice core as CNFs in containers using Kubernetes in any cloud. " * 4
    for stil, metin in [("", govde), ("B", "Rapid Deployment"), ("", "via VNFs or CNFs in Kubernetes for operators."),
                        ("B", "Multi-Generation"), ("B", "Wireless Voice"), ("B", "2G/3G VoLTE"),
                        ("B", "Fixed Voice"), ("B", "POTS"), ("B", "VoBB")]:
        d.set_font("A", stil, 11 if stil else 10)
        d.multi_cell(0, 6, metin, new_x="LMARGIN", new_y="NEXT")
    veri = bytes(d.output())
    agir = pdf.heading_lines(veri)
    sayfalar = [Page(1, pdf.text_layer(veri)[0], "text", frozenset(agir[0]))]
    chunks = pdf.chunk_pages(sayfalar)
    metin = " ".join(c.text for c in chunks)
    for etiket in ("POTS", "VoBB", "2G/3G VoLTE", "Wireless Voice"):
        assert etiket in metin, etiket
    assert "POTS" not in {c.section for c in chunks}
    # Etiketler modele etiket olduğu söylenerek veriliyor (bkz. _etiket_dizileri)
    assert "[şema/kutu etiketleri: Multi-Generation · Wireless Voice · 2G/3G VoLTE · Fixed Voice · POTS · VoBB]" in metin


def test_parcalama_metin_kaybetmiyor():
    """Özellik: her birim metni en az bir chunk'ta bulunmalı."""
    sayfalar = [Page(1, "MADDE 1\nA\nMADDE 2\nB\nGENEL\nKISA SATIR\nSON", "text")]
    birimler = [t for t, _, _ in pdf._units(sayfalar, 2400)]
    metin = " ".join(c.text for c in pdf.chunk_pages(sayfalar, min_chars=200))
    assert all(b in metin for b in birimler)


def test_uc_satirdan_kisa_baslik_dizisi_etiket_sayilmiyor():
    """İki satıra sarmış gerçek bir başlık etiket dizisi değil."""
    sayfalar = [Page(1, "BİRİNCİ BÖLÜM\nAmaç, Kapsam ve Tanımlar\nMADDE 1 - AMAÇ\nBu yönetmeliğin amacı şeffaflıktır.", "text")]
    birimler = [t for t, _, _ in pdf._units(sayfalar, 2400)]
    assert not any(b.startswith("[şema") for b in birimler)


def test_kisa_bolumler_komsusuyla_birlesiyor():
    """Her başlık ayrı parça olunca ders notu 41 karakterlik parçalara bölünüyordu.

    ÖLÇÜLDÜ (Elektromanyetik Alanlar, 52 sayfa): 89 parçanın 18'i 200
    karakterin altındaydı; "8 parça getir" kuralı belgenin %9'unu okumak
    demekti. Kısa parçalar artık komşularıyla birleşiyor, dolu maddeler
    birbirine karışmıyor.
    """
    # Gövdeler 40 karakterlik "kırıntı" süzgecinin üstünde ama 200'ün altında:
    # ölçtüğümüz şey KISA BÖLÜM birleşmesi, kırıntı birleşmesi değil.
    sayfalar = [
        pdf.Page(1, "1.1 Giriş\nBu bölüm belgenin amacını kısaca anlatan bir paragraf içerir.\n"
                    "1.2 Kapsam\nBu bölüm belgenin hangi konuları kapsadığını açıklar.\n"
                    "1.3 Tanımlar\nBu bölüm belgede geçen terimlerin tanımlarını verir.",
                 "text", frozenset()),
    ]
    chunks = pdf.chunk_pages(sayfalar, max_chars=2400, overlap_chars=0, min_chars=200)
    assert len(chunks) == 1
    assert all(len(c.text) >= 40 for c in chunks)
    # Birleşen parçanın bölüm adı iki başlığı da söylüyor.
    assert "1.1 Giriş" in chunks[0].section and "1.2 Kapsam" in chunks[0].section
    assert "tanımlarını verir" in chunks[0].text


def test_dolu_maddeler_birlesmiyor():
    """Sözleşmedeki davranış değişmemeli: MADDE 5 ile MADDE 6 ayrı parça."""
    govde = "Bu madde yeterince uzun bir metin içeriyor. " * 8
    sayfalar = [
        pdf.Page(1, f"MADDE 5 - CEZAİ ŞART\n{govde}\nMADDE 6 - FESİH\n{govde}", "text", frozenset()),
    ]
    chunks = pdf.chunk_pages(sayfalar, max_chars=2400, overlap_chars=0, min_chars=200)
    assert len(chunks) == 2
    assert chunks[0].section.startswith("MADDE 5") and chunks[1].section.startswith("MADDE 6")


def test_bolum_adi_uc_addan_fazlasini_kisaltiyor():
    assert pdf._bolum_adi("A", "B") == "A · B"
    assert pdf._bolum_adi("A · B · C", "D").endswith("…")


def test_sekil_tespiti_metin_sayfasini_secmiyor(ornek_pdfler):
    """Düz metin sayfası şekil sayılmamalı: her sayfaya vision çağrısı,
    kotayı yakar ve sayfa metninin kopyasını üretir."""
    from belge import gorsel

    sayfalar = gorsel.sekilli_sayfalar(ornek_pdfler["kuzey"])
    # Sentetik sözleşmede çizim yok; bir sayfa bile seçilmemeli.
    assert sayfalar == [], sayfalar


def test_sekil_tespiti_ust_sinira_uyuyor(ornek_pdfler):
    from belge import gorsel

    assert len(gorsel.sekilli_sayfalar(ornek_pdfler["kuzey_tarama"], en_fazla=1)) <= 1
