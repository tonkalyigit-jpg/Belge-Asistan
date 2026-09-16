"""Üretim yardımcıları: kısa cevabın yeri ve atıf temizliği."""
from __future__ import annotations

from core.nodes.generate import _atifsiz, _kisa_cevap_basa

def test_sonda_yazilan_kisa_cevap_basa_tasiniyor():
    assert _kisa_cevap_basa("| t |\n\nDetay.\n\n**Kısa cevap:** Sonuç.") == "**Kısa cevap:** Sonuç.\n\n| t |\n\nDetay."


def test_iki_kisa_cevap_varsa_kanittan_sonraki_kaliyor():
    """Arayüzde "Kısa cevap" iki kez göründü; baştaki kanıttan önce yazılmıştı."""
    cikti = _kisa_cevap_basa("**Kısa cevap:** eski.\n\n| t |\n\n**Kısa cevap:** yeni.")
    assert cikti.count("Kısa cevap") == 1 and cikti.startswith("**Kısa cevap:** yeni.")


def test_ciplak_br_etiketi_temizleniyor():
    assert "<br>" not in _kisa_cevap_basa("| a<br>b |\n\n**Kısa cevap:** x")


def test_atif_numaralari_cevaptan_siliniyor():
    """Kaynak numarası metinde durmuyor; nereden geldiği cümlede yazıyor."""
    assert _atifsiz("Ceza 485.000 TL'dir [1].") == "Ceza 485.000 TL'dir."
    assert _atifsiz("MADDE 5 [1][3] uyarınca %25 [2] tavanı var.") == \
        "MADDE 5 uyarınca %25 tavanı var."
    assert _atifsiz("Uygun ([1]).") == "Uygun."
    assert _atifsiz("| %25 [2] | 3 yıl [1] |") == "| %25 | 3 yıl |"
    # Satır sonu korunuyor: yutulduğunda iki madde tek satıra biniyordu.
    assert _atifsiz("Toplam 35.640 [1]\nİkinci satır [2]") == "Toplam 35.640\nİkinci satır"
    # Markdown bağlantısı ve şema etiketi köşeli parantez taşıyor ama atıf değil.
    assert _atifsiz("[link](http://x) ve [şema/kutu etiketleri: A · B]") == \
        "[link](http://x) ve [şema/kutu etiketleri: A · B]"
