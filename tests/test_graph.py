"""Boru hattı yolları — gerçek belgeler yüklenmiş, LLM sahte."""
from __future__ import annotations

import json

import pytest

from belge import ingest
from core import graph
from core.state import Category, Route
from tests.ornek_metinler import SOZLESME_A, SOZLESME_B

GROUNDED = json.dumps({"grounded": True, "unsupported": [], "bad_citations": []})
SUFFICIENT = json.dumps({"sufficient": True, "missing": [], "hint": ""})
NOT_SUFFICIENT = json.dumps({"sufficient": False, "missing": ["süre"], "hint": "süreyi yaz"})


def sinif(kategori: str, ids=(), arama="gecikme cezası", zorluk=2, standalone=None, anahtar=()) -> str:
    return json.dumps({"category": kategori, "difficulty": zorluk, "language": "tr",
                       "standalone_question": standalone or "", "search_query": arama,
                       "document_ids": list(ids), "keywords": list(anahtar)})


def notlar(n: int, alakali: bool = True) -> str:
    return json.dumps({"grades": [{"idx": i, "relevant": alakali, "score": 0.9, "why": "x"} for i in range(n)]})


@pytest.fixture
def belgeler(fake_llm, fake_embedder, ornek_pdfler):
    ids = {}
    for ad, belge in [("kuzey", SOZLESME_A), ("guney", SOZLESME_B)]:
        fake_llm({"ozet": f"Başlık: {belge['baslik']}\nTür: sözleşme\n\n{ad} sözleşmesi özeti."})
        ids[ad] = ingest.yukle(ornek_pdfler[ad], belge["dosya"]).document_id
    return ids


def test_kapsam_disi_aramaya_hic_gitmiyor(belgeler, fake_llm):
    providers = fake_llm({"classifier": sinif("kapsam_disi"), "refusal": "Yalnızca belgelerden cevap veriyorum."})
    s = graph.run("Bugün hava nasıl?", persist=False)
    assert s.route == Route.REFUSAL
    assert "retrieve" not in [x["node"] for x in s.steps]


def test_belge_ozeti_model_calistirmadan_donuyor(belgeler, fake_llm):
    providers = fake_llm({"classifier": sinif("belge_ozeti", ids=[belgeler["guney"]])})
    s = graph.run("Güney sözleşmesini özetle", persist=False)
    assert s.route == Route.OZET
    assert "guney sözleşmesi özeti" in s.answer
    # Tek model çağrısı sınıflandırıcı; özet saklıydı.
    toplam = sum(len(p.calls) for p in providers.values())
    assert toplam == 1


def test_belge_ici_cevap_belgeye_ve_sayfaya_bagli(belgeler, fake_llm):
    fake_llm({
        "classifier": sinif("belge_ici", ids=[belgeler["guney"]], arama="gecikme cezası binde"),
        "grade": lambda s, u: notlar(u.count("\n[")+1),
        "generate": "**Kısa cevap:** Binde 5'tir [1].",
        "hallucination": GROUNDED, "sufficiency": SUFFICIENT,
    })
    s = graph.run("Güney sözleşmesinde gecikme cezası ne kadar?", persist=False)
    assert s.route == Route.RAG and s.answer.startswith("**Kısa cevap:**")
    assert s.citations and all(c["document_id"] == belgeler["guney"] for c in s.citations)
    sayfalar = [p["page_start"] for c in s.citations for p in c["passages"] if p["chunk_kind"] == "body"]
    assert sayfalar and all(isinstance(x, int) for x in sayfalar)


def test_belgede_olmayan_soru_uydurulmuyor(belgeler, fake_llm):
    providers = fake_llm({
        "classifier": sinif("belge_ici", arama="mücbir sebep"),
        "grade": lambda s, u: notlar(20, alakali=False),
        "rewrite": [json.dumps({"search_query": "mücbir sebep hükmü", "strategy": "reword"}),
                    json.dumps({"search_query": "mücbir sebepler", "strategy": "broaden"})],
    })
    s = graph.run("Mücbir sebep maddesi var mı?", persist=False)
    assert "bulamadım" in s.answer
    assert "writer" not in providers and "strong" not in providers   # üretim hiç çalışmadı


def test_capraz_soruda_belgeler_sigiyorsa_tamami_okunuyor(belgeler, fake_llm):
    """Kısmi okuma gizlilik ihlalini kaçırıp "belgede yok" demişti."""
    gorulen = {}

    def uret(system, user):
        gorulen["user"] = user
        return "| a | b |\n\nDetay [1][2].\n\n**Kısa cevap:** Güney aykırı."

    providers = fake_llm({
        "classifier": sinif("capraz_belge", zorluk=4),
        "capraz": uret, "hallucination": GROUNDED, "sufficiency": SUFFICIENT,
    })
    s = graph.run("Hangi sözleşme yönetmeliğe aykırı?", persist=False)
    assert s.full_context
    assert "grade" not in [x["node"] for x in s.steps]
    assert "COMPLETE text" in gorulen["user"]
    # iki belgenin de gizlilik maddesi bağlamda — aramaya bırakılsaydı gelmeyebilirdi
    assert gorulen["user"].count("GİZLİLİK") >= 2
    assert s.answer.startswith("**Kısa cevap:** Güney aykırı.")


def test_siniflandiricinin_uydurdugu_belge_kimligi_atiliyor(belgeler, fake_llm):
    fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["guney"], 999]),
              "grade": lambda s, u: notlar(20), "generate": "Cevap [1].",
              "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Güney sözleşmesinde ceza?", persist=False)
    assert s.document_ids == [belgeler["guney"]]


def test_yetersiz_cevap_dusuk_guvenli_isaretleniyor(belgeler, fake_llm):
    fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["kuzey"]]),
              "grade": lambda s, u: notlar(20), "generate": "Eksik cevap [1].",
              "hallucination": GROUNDED, "sufficiency": NOT_SUFFICIENT})
    s = graph.run("Kuzey sözleşmesinde teslim süresi?", persist=False)
    assert s.sufficient is False and s.low_confidence


def test_hic_belge_yokken_yonlendiriyor(fake_llm, fake_embedder):
    fake_llm({"classifier": sinif("belge_ici")})
    s = graph.run("Ceza ne kadar?", persist=False)
    assert "yüklenmiş bir belge yok" in s.answer


def test_silinen_belge_capraz_okumaya_girmiyor(belgeler, fake_llm):
    """Silinen bir sözleşme karşılaştırmada hâlâ okunmamalı (KVKK talebiyle silinmiş olabilir)."""
    gorulen = {}

    def uret(system, user):
        gorulen["user"] = user
        return "**Kısa cevap:** x"

    ingest.sil(belgeler["kuzey"])
    fake_llm({"classifier": sinif("capraz_belge", ids=[belgeler["kuzey"], belgeler["guney"]], zorluk=4),
              "capraz": uret, "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Sözleşmeleri karşılaştır", persist=False)
    assert "KUZEY" not in gorulen["user"].upper() and "Kuzey" not in gorulen["user"]
    assert s.document_ids == [belgeler["guney"]]


def test_tek_belgeye_sorulan_soruda_uc_chunk_tavani_uygulanmiyor(belgeler, fake_llm):
    """Veri sayfasında "DS0 kapasiteleri" sorusu 3 chunk'la sınırlı kalıp cevabı
    yalnızca özetten çıkarmıştı: tavan, arama zaten tek belgeyle sınırlıyken de
    uygulanıyordu."""
    fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["kuzey"]], arama="madde"),
              "grade": lambda s, u: notlar(20), "generate": "x [1].",
              "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Kuzey sözleşmesinin maddeleri neler?", persist=False)
    assert len(s.hits) > 3
    assert all(h.document_id == belgeler["kuzey"] for h in s.hits)


# --- belgeye odaklanma -----------------------------------------------------------

def test_odakli_soruda_arama_yalnizca_o_belgede(belgeler, fake_llm):
    """Sınıflandırıcı başka belge adı yakalasa da odak kazanır."""
    fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["kuzey"]]),
              "grade": lambda s, u: notlar(20), "generate": "x [1].",
              "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Kuzey sözleşmesinde ceza?", persist=False, scope_document_ids=[belgeler["guney"]])
    assert s.document_ids == [belgeler["guney"]]
    assert s.hits and all(h.document_id == belgeler["guney"] for h in s.hits)


def test_tek_belgeye_odakliyken_karsilastirma_belge_ici_oluyor(belgeler, fake_llm):
    fake_llm({"classifier": sinif("capraz_belge", zorluk=4),
              "grade": lambda s, u: notlar(20), "generate": "x [1].",
              "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Yönetmelikle karşılaştır", persist=False, scope_document_ids=[belgeler["kuzey"]])
    assert s.category == Category.BELGE_ICI
    assert s.hits and all(h.document_id == belgeler["kuzey"] for h in s.hits)


def test_odakliyken_bulunamayan_soru_odagi_hatirlatiyor(belgeler, fake_llm, monkeypatch):
    # Arama yolunu sınıyor; küçük belgede tam okuma devreye girmesin.
    monkeypatch.setitem(__import__("config").load()["retrieval"], "single_full_context_chars", 0)
    fake_llm({"classifier": sinif("belge_ici", arama="mücbir sebep"),
              "grade": lambda s, u: notlar(20, alakali=False),
              "rewrite": json.dumps({"search_query": "mücbir", "strategy": "broaden"})})
    s = graph.run("Mücbir sebep var mı?", persist=False, scope_document_ids=[belgeler["kuzey"]])
    assert "yalnızca" in s.answer and "odağı kaldırıp" in s.answer


def test_silinmis_belgeye_odak_dusuyor(belgeler, fake_llm):
    ingest.sil(belgeler["kuzey"])
    fake_llm({"classifier": sinif("belge_ici"), "grade": lambda s, u: notlar(20),
              "generate": "x [1].", "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Ceza?", persist=False, scope_document_ids=[belgeler["kuzey"]])
    assert s.scope_document_ids == []
    assert any("odak kaldırıldı" in w for w in s.warnings)


# --- hesap, servis hatası, yardımcı "bulamadım" -----------------------------------------

def test_yanlis_hesap_ipucuyla_yeniden_uretiliyor(belgeler, fake_llm):
    taslaklar = ["**Kısa cevap:** 58.200 TL.\nHesap: 4.850.000 TL × ‰3 × 40 = 58.200 TL",
                 "**Kısa cevap:** 485.000 TL.\nHesap: 4.850.000 TL × ‰3 × 40 = 582.000 TL\n"
                 "Hesap: 4.850.000 TL × %10 = 485.000 TL (tavan)"]
    gorulen = []

    def uret(system, user):
        gorulen.append(user)
        return taslaklar.pop(0)

    fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["kuzey"]]),
              "grade": lambda s, u: notlar(20), "generate": uret,
              "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Kuzey'de 40 gün gecikmenin cezası kaç TL?", persist=False)
    assert s.regens == 1 and "582.000" in gorulen[1]      # ipucu doğru sonucu söyledi
    assert s.answer.startswith("**Kısa cevap:** 485.000") and not s.low_confidence


def test_servis_hatasi_denetime_sokulmuyor(belgeler, fake_llm):
    from llm.base import LLMError

    providers = fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["kuzey"]]),
                          "grade": lambda s, u: notlar(20),
                          "generate": LLMError("HTTP 429: You exceeded your current quota"),
                          "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Kuzey'de ceza?", persist=False)
    assert "kota" in s.answer and s.low_confidence and s.regens == 0
    denetim = [c for p in providers.values() for c in p.calls
               if c["system"].startswith(__import__("config").prompt("hallucination"))
               or c["system"].startswith(__import__("config").prompt("sufficiency"))]
    assert denetim == []


def test_bulunamayan_terim_baska_belgede_geciyorsa_soyleniyor(belgeler, fake_llm, monkeypatch):
    monkeypatch.setitem(__import__("config").load()["retrieval"], "single_full_context_chars", 0)
    """"G9'u AWS'ye kurabilir miyim?" — AWS G9'da yok, başka belgede var."""
    fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["kuzey"]], arama="Gebze depo",
                                  anahtar=["antalya", "mahkeme"]),
              "grade": lambda s, u: notlar(20, alakali=False),
              "rewrite": json.dumps({"search_query": "Antalya", "strategy": "broaden"})})
    s = graph.run("Kuzey sözleşmesinde Antalya mahkemeleri yetkili mi?", persist=False)
    # Aranan belge adıyla söyleniyor; "Antalya" Kuzey'de yok, Güney'de var.
    # Küçük harfli genel kelimeler ("mahkemeleri") ipucuna girmiyor.
    assert "Kuzey Lojistik" in s.answer.split("\n")[0]
    assert "**Antalya**" in s.answer and "Güney" in s.answer
    assert "mahkeme" not in s.answer.split("\n\n", 1)[1].casefold()


def test_adi_gecen_kucuk_belge_tamamen_okunuyor(belgeler, fake_llm):
    """Ceza oranı Madde 5'te, bedel Madde 3'te: arama bedeli eleyince hesap yapılamadı."""
    gorulen = {}

    def uret(system, user):
        gorulen["user"] = user
        return "**Kısa cevap:** 5.400.000 TL.\nHesap: 21.600.000 TL × %25 = 5.400.000 TL"

    providers = fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["guney"]]),
                          "generate": uret, "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Güney'de 60 gün gecikmenin cezası?", persist=False)
    assert s.full_context and "grade" not in [x["node"] for x in s.steps]
    assert "21.600.000" in gorulen["user"] and "CEZAİ ŞART" in gorulen["user"]
    assert "COMPLETE text" in gorulen["user"]


def test_hesap_sonucu_kisa_cevapta_yoksa_yeniden_yazdiriliyor(belgeler, fake_llm):
    taslaklar = ["**Kısa cevap:** Toplam doğrudan verilmemiş.\n\nHesap: 15.840 + 19.800 = 35.640",
                 "**Kısa cevap:** Toplam 35.640'tır.\n\nHesap: 15.840 + 19.800 = 35.640"]
    gorulen = []

    def uret(system, user):
        gorulen.append(user)
        return taslaklar.pop(0)

    fake_llm({"classifier": sinif("belge_ici", ids=[belgeler["kuzey"]]), "generate": uret,
              "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Toplamı kaç?", persist=False)
    assert s.regens == 1 and "35.640" in gorulen[1]
    assert s.answer.startswith("**Kısa cevap:** Toplam 35.640")


def test_tam_okumada_da_baska_belge_ipucu_ve_odak_notu(belgeler, fake_llm):
    fake_llm({"classifier": sinif("belge_ici"), "generate": "**Kısa cevap:** Bu belgede geçmiyor.",
              "hallucination": GROUNDED, "sufficiency": SUFFICIENT})
    s = graph.run("Antalya mahkemeleri yetkili mi?", persist=False,
                  scope_document_ids=[belgeler["kuzey"]])
    assert s.full_context
    assert "odaklanılan belgede" in s.answer and "**Antalya**" in s.answer and "Güney" in s.answer


def test_baglam_butcesi_kucuk_chunklarda_genisliyor():
    """Sabit chunk sayısı sabit bağlam demek değil — bütçe karakterle ölçülüyor.

    EMAT ders notunda ölçüldü: 8 chunk (ortalama 585 karakter) 52 sayfalık
    belgenin %9'unu bağlama alıyordu ve "daha detaylı anlat" dendiğinde modelin
    elinde yeni bir şey kalmıyordu.
    """
    import config
    from belge.store import Hit
    from core.nodes.retrieve import _butceye_gore

    butce = int(config.get("retrieval.context_chars", 12000))
    top_k = int(config.get("retrieval.top_k", 8))

    def hits(n, uzunluk):
        return [Hit(row_id=i, document_id=1, title="B", filename="b.pdf",
                    text="x" * uzunluk) for i in range(n)]

    kucuk = _butceye_gore(hits(40, 300), en_az=top_k)
    assert len(kucuk) > top_k
    assert sum(len(h.text) for h in kucuk) >= butce

    # Büyük chunk'lı belgede taban korunuyor: bütçe ilk chunk'ta dolsa bile
    # eski davranışın altına inilmiyor.
    buyuk = _butceye_gore(hits(40, 4000), en_az=top_k)
    assert len(buyuk) == top_k


def test_puanlayici_takip_sorusunun_cozulmus_halini_goruyor(belgeler, fake_llm):
    """"daha detaylı anlat" cümlesine bakarak alaka kararı verilemez.

    ÖLÇÜLDÜ (EMAT ders notu): ham takip sorusuyla puanlayıcı aynı soruda 24
    chunk'ın kâh 9'unu kâh 3'ünü alakalı buldu; bağlam koşudan koşuya değişti.
    """
    gorulen = []

    def puanla(system, user):
        gorulen.append(user)
        return json.dumps({"grades": [{"idx": 0, "relevant": True, "score": 0.9}]})

    fake_llm({
        "classifier": sinif("belge_ici", standalone="Kuzey sözleşmesinde gecikme cezası nedir?"),
        "grade": puanla,
        "generate": "**Kısa cevap:** Binde 3.",
        "hallucination": GROUNDED, "sufficiency": SUFFICIENT,
    })
    graph.run("daha detaylı anlat", persist=False,
              history=[{"role": "user", "content": "gecikme cezası ne?"}])
    assert gorulen, "puanlayıcı hiç çağrılmadı"
    assert "gecikme cezası" in gorulen[0]
    assert "daha detaylı anlat" not in gorulen[0]
