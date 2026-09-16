"""Ön işleme ve görsel okuma."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from belge import goruntu, ocr, pdf
from belge.goruntu import Adimlar


def _metin_sayfasi() -> Image.Image:
    g = Image.new("L", (1200, 1600), 255)
    d = ImageDraw.Draw(g)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
    for i in range(30):
        d.text((80, 80 + i * 48), "Tedarikçi teslim süresine uymazsa gecikme cezası öder", fill=0, font=font)
    return g


def test_egiklik_acisi_bilinen_donmeyi_buluyor():
    sayfa = _metin_sayfasi().rotate(3.0, resample=Image.BICUBIC, fillcolor=255)
    assert abs(goruntu.egiklik_acisi(sayfa) - (-3.0)) <= 0.2


def test_duz_sayfa_dondurulmuyor():
    g, kayit = goruntu.hazirla(_metin_sayfasi(), Adimlar(egiklik=True, kontrast=False))
    assert abs(kayit["egiklik_derece"]) < 0.2
    assert g.size == (1200, 1600)          # 0.2° altında döndürme yok, boyut aynı


def test_binde_isareti_duzeltmesi_yalnizca_kesin_olani_duzeltiyor():
    """42 okumadaki tek hata türü: ‰ -> "%o" / "‰o". "%5" gerçek yüzde olabilir."""
    assert ocr._BINDE.sub("‰", "binde 3'ü (%o3)") == "binde 3'ü (‰3)"
    assert ocr._BINDE.sub("‰", "binde 3'ü (‰o3)") == "binde 3'ü (‰3)"
    assert ocr._BINDE.sub("‰", "yüzde 25 (%25)") == "yüzde 25 (%25)"


def test_ocr_vision_kademesine_goruntuyle_gidiyor(fake_llm, ornek_pdfler):
    providers = fake_llm({"ocr": "```\nMADDE 5 - CEZAİ ŞART\nbinde 5'i (%o5)\n```"})
    sonuc = ocr.oku(pdf.render_page(ornek_pdfler["guney_tarama"], 2, dpi=72), 2)
    cagri = providers["vision"].calls[0]
    assert cagri["images"] and cagri["images"][0][1] == "image/jpeg"
    assert cagri["images"][0][0][:2] == b"\xff\xd8"          # gerçek JPEG
    assert sonuc.text == "MADDE 5 - CEZAİ ŞART\nbinde 5'i (‰5)"   # çit ve ‰ temizlendi


def test_gorsel_desteklemeyen_saglayici_sessizce_metin_gondermiyor():
    """Varsayılan uygulama hata vermeli; görüntüsüz gönderim uydurma metin demek."""
    import pytest

    from llm.base import LLMError, LLMProvider, TierConfig

    class YalnizMetin(LLMProvider):
        def complete(self, system, user, **kw):  # pragma: no cover
            raise AssertionError("görsel çağrı metin yoluna düşmemeli")

    p = YalnizMetin(TierConfig(name="x", provider="x", model="m"))
    with pytest.raises(LLMError, match="görsel"):
        p.complete_vision("s", "u", [(b"x", "image/jpeg")])


def test_openai_compat_goruntuyu_data_uri_olarak_gonderiyor():
    from llm.base import TierConfig
    from llm.providers.openai_compat import OpenAICompatProvider

    tier = TierConfig(name="vision", provider="openai_compat", model="m",
                      base_url="http://localhost:1", api_key_env="YOK_BOYLE_ANAHTAR")
    p = OpenAICompatProvider(tier)
    yuk = p._build_payload("sys", "oku", max_tokens=10, stop=None, temperature=None,
                           stream=False, images=[(b"\xff\xd8abc", "image/jpeg")])
    icerik = yuk["messages"][1]["content"]
    assert icerik[0] == {"type": "text", "text": "oku"}
    assert icerik[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_gemini_bekleme_suresi_govdeden_okunuyor():
    """Gemini süreyi başlıkta değil gövdede veriyor; bakılmayınca 1-2-4 sn bekleyip vazgeçiyordu."""
    import httpx

    from llm.providers.openai_compat import OpenAICompatProvider

    govde = '[{"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}]}}]'
    resp = httpx.Response(429, text=govde)
    assert OpenAICompatProvider._retry_delay(resp, 0) == 17.0
    assert OpenAICompatProvider._retry_delay(httpx.Response(429, text="{}"), 1) == 2.0
