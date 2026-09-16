"""Belge yükleme hattı — gerçek PDF, sahte LLM ve embedding."""
from __future__ import annotations

from belge import ingest
from belge.store import get_store
from core import db
from llm.base import LLMError

OZET = "Başlık: Güney Tedarik Sözleşmesi\nTür: sözleşme\n\nBir tedarik sözleşmesi.\n\n**Öne çıkanlar**\n- Bedel 21.600.000 TL (s. 1)"


def test_dijital_pdf_ocrsiz_indeksleniyor(fake_llm, fake_embedder, ornek_pdfler):
    providers = fake_llm({"ozet": OZET})
    s = ingest.yukle(ornek_pdfler["guney"], "guney.pdf")
    assert s.status == "ready", s.error
    assert s.ocr_pages == 0 and "vision" not in providers
    assert s.title == "Güney Tedarik Sözleşmesi" and s.kind == "sözleşme"
    assert s.chunks > 1 and get_store().size() == s.chunks
    kayit = db.connect().execute("SELECT status, summary FROM documents WHERE id=?", (s.document_id,)).fetchone()
    assert kayit["status"] == "ready" and kayit["summary"].startswith("Tür: sözleşme")


def test_taranmis_pdf_her_sayfa_icin_ocr(fake_llm, fake_embedder, ornek_pdfler):
    providers = fake_llm({"ozet": OZET, "ocr": ["MADDE 1 - TARAFLAR\nAlıcı ve tedarikçi arasında imzalanmıştır. " * 3,
                                                 "MADDE 5 - CEZAİ ŞART\nGecikme cezası binde 5'tir. " * 3]})
    s = ingest.yukle(ornek_pdfler["guney_tarama"], "guney_tarama.pdf")
    assert s.status == "ready", s.error
    assert s.ocr_pages == 2 and len(providers["vision"].calls) == 2
    kaynaklar = [r["source"] for r in db.connect().execute(
        "SELECT source FROM pages WHERE document_id=? ORDER BY page_no", (s.document_id,))]
    assert kaynaklar == ["ocr", "ocr"]


def test_ayni_icerik_farkli_adla_ikinci_kez_islenmiyor(fake_llm, fake_embedder, ornek_pdfler):
    fake_llm({"ozet": OZET})
    ilk = ingest.yukle(ornek_pdfler["guney"], "sozlesme_son.pdf")
    ikinci = ingest.yukle(ornek_pdfler["guney"], "sozlesme_son_v2.pdf")
    assert ikinci.status == "duplicate" and ikinci.document_id == ilk.document_id
    assert get_store().size() == ilk.chunks


def test_tek_sayfanin_ocr_hatasi_belgeyi_dusurmuyor(fake_llm, fake_embedder, ornek_pdfler):
    fake_llm({"ozet": OZET, "ocr": [LLMError("429 kota"), "MADDE 5 - CEZAİ ŞART\nGecikme cezası binde 5'tir. " * 3]})
    s = ingest.yukle(ornek_pdfler["guney_tarama"], "guney_tarama.pdf")
    assert s.status == "ready"
    assert any("Sayfa 1 okunamadı" in w for w in s.warnings)


def test_hicbir_sayfadan_metin_cikmazsa_belge_basarisiz(fake_llm, fake_embedder, ornek_pdfler):
    fake_llm({"ozet": OZET, "ocr": LLMError("servis yok")})
    s = ingest.yukle(ornek_pdfler["guney_tarama"], "guney_tarama.pdf")
    assert s.status == "failed" and "metin" in s.error
    assert get_store().size() == 0


def test_ozet_uretilemezse_belge_yine_aranabilir(fake_llm, fake_embedder, ornek_pdfler):
    fake_llm({"ozet": LLMError("kota")})
    s = ingest.yukle(ornek_pdfler["guney"], "guney_sozlesmesi.pdf")
    assert s.status == "ready" and s.chunks > 0
    assert s.title == "guney sozlesmesi"
    assert any("Özet üretilemedi" in w for w in s.warnings)


def test_silinen_belgenin_metni_ve_dosyasi_kalmiyor(fake_llm, fake_embedder, ornek_pdfler, isolated_storage):
    fake_llm({"ozet": OZET})
    s = ingest.yukle(ornek_pdfler["guney"], "guney.pdf")
    assert list((isolated_storage / "belgeler").iterdir())
    assert ingest.sil(s.document_id)
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) c FROM pages WHERE document_id=?", (s.document_id,)).fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM chunks WHERE document_id=? AND text != ''", (s.document_id,)).fetchone()["c"] == 0
    assert not list((isolated_storage / "belgeler").iterdir())
    assert get_store().size() == 0
    assert ingest.listele() == []


def test_boyut_siniri(fake_llm):
    import config
    eski = config.load()["belge"]["max_upload_mb"]
    config.load()["belge"]["max_upload_mb"] = 0.00001
    try:
        s = ingest.yukle(b"%PDF-1.4 " + b"x" * 100, "buyuk.pdf")
    finally:
        config.load()["belge"]["max_upload_mb"] = eski
    assert s.status == "failed" and "üst sınır" in s.error


def test_buyuk_harfli_baslik_turkce_kurallarla_duzeltiliyor():
    from belge.ozet import baslik_duzelt
    assert baslik_duzelt("GÜNEY BİLİŞİM LTD. ŞTİ. TEDARİK SÖZLEŞMESİ") == "Güney Bilişim Ltd. Şti. Tedarik Sözleşmesi"
    assert baslik_duzelt("KUZEY LOJİSTİK A.Ş. TEDARİK SÖZLEŞMESİ") == "Kuzey Lojistik A.Ş. Tedarik Sözleşmesi"
    assert baslik_duzelt("IŞIK VE İZMİR") == "Işık Ve İzmir"
    # zaten karışık yazımlı başlığa dokunulmuyor
    assert baslik_duzelt("Satın Alma Yönetmeliği") == "Satın Alma Yönetmeliği"


def test_yeniden_parcalama_ocr_ve_ozet_tekrarlamiyor(fake_llm, fake_embedder, ornek_pdfler):
    providers = fake_llm({"ozet": OZET})
    s = ingest.yukle(ornek_pdfler["guney"], "guney.pdf")
    cagri = sum(len(p.calls) for p in providers.values())
    n = ingest.yeniden_parcala(s.document_id)
    assert n == s.chunks
    assert sum(len(p.calls) for p in providers.values()) == cagri       # model çağrısı yok
    assert get_store().size() == s.chunks                                # eskiler aramada değil
    hits = get_store().search("gecikme cezası", document_ids={s.document_id})
    assert hits and all(h.text for h in hits)


def test_yeniden_parcalama_tekrar_tekrar_calisabiliyor(fake_llm, fake_embedder, ornek_pdfler):
    """İlk sürüm iki kez üst üste çalışınca UNIQUE kısıtına takıldı ve belgeyi
    parçasız bıraktı: silinmiş satırların sıra numaraları çakışıyordu."""
    fake_llm({"ozet": OZET})
    s = ingest.yukle(ornek_pdfler["guney"], "guney.pdf")      # row_id 0'dan başlıyor
    for _ in range(3):
        assert ingest.yeniden_parcala(s.document_id) == s.chunks
    assert get_store().size() == s.chunks
