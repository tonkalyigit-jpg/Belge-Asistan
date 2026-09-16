"""Hesaplar, oturumlar ve erişim ayrımı.

Bu dosyanın asıl işi son iki test: "başkasının belgesi görünmesin" kuralı
arayüzde değil ÇEKİRDEKTE tutulmalı. Arayüz filtrelemeyi unutursa kullanıcı
fazladan bir satır görür; boru hattı filtrelemeyi unutursa başkasının
sözleşmesinden cevap üretir ve bunu kimse fark etmez.
"""
from __future__ import annotations

import pytest

from belge.pdf import Chunk
from belge.store import get_store
from core import db
from memory import kullanicilar


def _belge(sha: str, owner_id: int | None, paylasim: str = "ozel") -> int:
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO documents (sha256, filename, title, status, owner_id, paylasim) "
        "VALUES (?,?,?, 'ready', ?, ?)",
        (sha, f"{sha}.pdf", sha, owner_id, paylasim),
    )
    conn.commit()
    return cur.lastrowid


def test_parola_hash_saklaniyor_duz_metin_yok():
    kullanicilar.olustur("ayse", "gizli123")
    row = db.connect().execute("SELECT * FROM users WHERE username = 'ayse'").fetchone()
    assert b"gizli123" not in bytes(row["password_hash"])
    assert kullanicilar.dogrula("ayse", "gizli123") is not None
    assert kullanicilar.dogrula("ayse", "gizli124") is None
    assert kullanicilar.dogrula("yok", "gizli123") is None


def test_kullanici_adi_tekil():
    kullanicilar.olustur("ayse", "a")
    with pytest.raises(ValueError):
        kullanicilar.olustur("ayse", "b")


def test_oturum_acilip_kapaniyor_ve_jeton_duz_saklanmiyor():
    uid = kullanicilar.olustur("ayse", "a")
    jeton = kullanicilar.oturum_ac(uid)
    assert kullanicilar.oturum_sahibi(jeton)["id"] == uid
    saklanan = db.connect().execute("SELECT token_hash FROM sessions").fetchone()["token_hash"]
    assert jeton != saklanan          # veritabanında sha256'sı duruyor
    kullanicilar.oturum_kapat(jeton)
    assert kullanicilar.oturum_sahibi(jeton) is None


def test_parola_degisince_oturumlar_kapaniyor():
    """Parola sıfırlamanın amacı erişimi kesmek; eski çerez çalışmaya devam
    ederse işlem boşa gider."""
    uid = kullanicilar.olustur("ayse", "a")
    jeton = kullanicilar.oturum_ac(uid)
    kullanicilar.parola_degistir(uid, "b")
    assert kullanicilar.oturum_sahibi(jeton) is None


def test_gorulebilir_belgeler_kendisi_ve_ortak_havuz():
    ayse = kullanicilar.olustur("ayse", "a")
    mehmet = kullanicilar.olustur("mehmet", "b")
    benim = _belge("ayse-ozel", ayse)
    onun = _belge("mehmet-ozel", mehmet)
    ortak = _belge("yonetmelik", mehmet, "ortak")

    gorulen = kullanicilar.gorulebilir_ids(ayse)
    assert benim in gorulen and ortak in gorulen
    assert onun not in gorulen
    assert kullanicilar.gorebilir_mi(ayse, onun) is False
    # Ortak havuzdaki belge görünüyor ama SAHİBİ değil: silemez.
    assert kullanicilar.belge_sahibi_mi(ayse, ortak) is False


def test_arama_baskasinin_belgesini_getirmiyor(fake_embedder, fake_llm):
    """Çekirdek testi: izin listesi verilince arama o kümenin dışına çıkmıyor."""
    from core import graph

    ayse = kullanicilar.olustur("ayse", "a")
    mehmet = kullanicilar.olustur("mehmet", "b")
    onunki = _belge("gizli-sozlesme", mehmet)
    store = get_store()
    store.add_document(onunki, "Gizli Sözleşme", "Mehmet'in özel sözleşmesi.",
                       [Chunk(0, "MADDE 5", "gecikme cezası binde üç oranında uygulanır", 2, 2)])

    import json
    fake_llm({
        "classifier": json.dumps({"category": "belge_ici", "difficulty": 2, "language": "tr",
                                  "standalone_question": "", "search_query": "gecikme cezası",
                                  "document_ids": [onunki], "keywords": []}),
        "grade": json.dumps({"grades": [{"idx": 0, "relevant": True, "score": 0.9}]}),
        "generate": "**Kısa cevap:** Binde üç.",
        "hallucination": json.dumps({"grounded": True, "unsupported": [], "bad_citations": []}),
        "sufficiency": json.dumps({"sufficient": True, "missing": [], "hint": ""}),
    })

    s = graph.run("gecikme cezası nedir?", persist=False,
                  izinli_belgeler=kullanicilar.gorulebilir_ids(ayse))
    assert not s.citations, "başkasının belgesi atıflara girdi"
    assert all(h.document_id != onunki for h in s.graded_hits)
