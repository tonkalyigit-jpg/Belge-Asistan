"""Üretim yardımcıları ve arayüzün yapısal tuzakları."""
from __future__ import annotations

import ast
from pathlib import Path

from core.nodes.generate import _kisa_cevap_basa

APP = Path(__file__).resolve().parent.parent / "app.py"


def test_sonda_yazilan_kisa_cevap_basa_tasiniyor():
    assert _kisa_cevap_basa("| t |\n\nDetay.\n\n**Kısa cevap:** Sonuç.") == "**Kısa cevap:** Sonuç.\n\n| t |\n\nDetay."


def test_iki_kisa_cevap_varsa_kanittan_sonraki_kaliyor():
    """Arayüzde "Kısa cevap" iki kez göründü; baştaki kanıttan önce yazılmıştı."""
    cikti = _kisa_cevap_basa("**Kısa cevap:** eski.\n\n| t |\n\n**Kısa cevap:** yeni.")
    assert cikti.count("Kısa cevap") == 1 and cikti.startswith("**Kısa cevap:** yeni.")


def test_ciplak_br_etiketi_temizleniyor():
    assert "<br>" not in _kisa_cevap_basa("| a<br>b |\n\n**Kısa cevap:** x")


def _T():
    for node in ast.parse(APP.read_text()).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "T":
            return ast.literal_eval(node.value)
    raise AssertionError("T sözlüğü yok")


def test_arayuz_sozlukleri_iki_dilde_birebir_ve_kullanilan_her_anahtar_tanimli():
    """Geçen projede canlıda patladı: KeyError: 'helpful_up'."""
    T = _T()
    assert set(T["tr"]) == set(T["en"])
    tree = ast.parse(APP.read_text())
    kullanilan = {
        n.slice.value for n in ast.walk(tree)
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
        and n.value.id in ("t", "tc", "tb") and isinstance(n.slice, ast.Constant)
    }
    assert not kullanilan - set(T["tr"])


def test_hicbir_fonksiyon_html_modulunu_yerel_degiskenle_golgelemiyor():
    """`html = …` yazılan fonksiyonda `html.escape` UnboundLocalError verir.

    render_badges'te yaşanacaktı; yazılırken yakalandı.
    """
    for fn in ast.walk(ast.parse(APP.read_text())):
        if not isinstance(fn, ast.FunctionDef):
            continue
        atanan = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name)}
        if "html" in atanan:
            kullanim = [n for n in ast.walk(fn) if isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name) and n.value.id == "html"]
            assert not kullanim, f"{fn.name}: html hem yerel değişken hem modül"


def test_turkce_buyuk_harf():
    src = APP.read_text()
    ns: dict = {}
    exec(src[src.index("def buyuk_harf"):src.index("def label(")], ns)
    assert ns["buyuk_harf"]("Kibar ret") == "KİBAR RET"
    assert ns["buyuk_harf"]("Güney Bilişim Ltd. Şti.") == "GÜNEY BİLİŞİM LTD. ŞTİ."
    assert ns["buyuk_harf"]("file", "en") == "FILE"


def test_atif_numaralari_cevaptan_siliniyor():
    """Kaynak numarası metinde durmuyor; nereden geldiği cümlede yazıyor."""
    from core.nodes.generate import _atifsiz

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
