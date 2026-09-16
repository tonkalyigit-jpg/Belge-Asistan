"""Hibrit arama deposu."""
from __future__ import annotations

from belge.pdf import Chunk
from belge.store import _tokenize, get_store
from core import db


def _belge(baslik: str) -> int:
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO documents (sha256, filename, title, status) VALUES (?,?,?, 'ready')",
        (baslik, f"{baslik}.pdf", baslik),
    )
    conn.commit()
    return cur.lastrowid


def _kur(fake_embedder):
    store = get_store()
    a = _belge("Kuzey sözleşmesi")
    b = _belge("Güney sözleşmesi")
    store.add_document(a, "Kuzey sözleşmesi", "Kuzey ile tedarik sözleşmesi.", [
        Chunk(0, "MADDE 5 - CEZAİ ŞART", "gecikme cezası binde üç oranında uygulanır", 2, 2),
        Chunk(1, "MADDE 6 - GARANTİ", "garanti süresi yirmi dört aydır", 2, 3),
    ])
    store.add_document(b, "Güney sözleşmesi", "Güney ile tedarik sözleşmesi.", [
        Chunk(0, "MADDE 5 - CEZAİ ŞART", "gecikme cezası binde beş oranında uygulanır", 2, 2),
    ])
    return store, a, b


def test_turkce_buyuk_harf_ayni_tokena_iniyor():
    assert _tokenize("İZMİR") == _tokenize("izmir") == ["izmir"]
    assert _tokenize("IZGARA") == ["ızgara"]


def test_arama_sayfa_ve_madde_bilgisi_tasiyor(fake_embedder):
    store, a, _ = _kur(fake_embedder)
    hits = store.search("garanti süresi", document_ids={a})
    garanti = next(h for h in hits if h.section == "MADDE 6 - GARANTİ")
    assert garanti.pages == "s. 2-3"
    assert garanti.location == "MADDE 6 - GARANTİ, s. 2-3"


def test_belge_kapsami_aday_seciminden_once_uygulaniyor(fake_embedder):
    store, a, b = _kur(fake_embedder)
    hits = store.search("gecikme cezası", document_ids={b})
    assert hits and all(h.document_id == b for h in hits)


def test_silinen_belge_aramaya_girmiyor_ve_eslesme_kaymiyor(fake_embedder):
    """Satır silmek vektör matrisinin satır indekslerini kaydırırdı."""
    store, a, b = _kur(fake_embedder)
    store.remove_document(a)
    c = _belge("Batı sözleşmesi")
    store.add_document(c, "Batı sözleşmesi", "", [
        Chunk(0, "MADDE 1", "batı teslim süresi altmış gündür", 1, 1),
    ])
    hits = store.search("teslim süresi gecikme garanti", top_k=20, max_per_document=20)
    assert {h.document_id for h in hits} <= {b, c}
    bati = next(h for h in hits if h.document_id == c)
    assert "altmış gün" in bati.text           # doğru satır, kaymış değil
    assert store.size() == 3                    # güney özet+1, batı 1 (özetsiz); silinen hariç


def test_belge_basina_tavan(fake_embedder):
    store, a, b = _kur(fake_embedder)
    hits = store.search("sözleşme madde", top_k=10, max_per_document=1)
    assert len([h for h in hits if h.document_id == a]) <= 1
