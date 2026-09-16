"""Hibrit vektör deposu: dense (BGE-M3) + BM25, Reciprocal Rank Fusion ile.

İndekslenen birim chunk: bir belge = 1 özet chunk'ı + N gövde chunk'ı. Özet
de indekste, çünkü "hangi belge teslim sürelerinden bahsediyor" gibi geniş
sorular gövdedeki tek bir cümleden çok özetle eşleşiyor.

Vektörler tek bir .npy dosyasında (satır indeksi = chunks.row_id), metadata
SQLite'ta. Birkaç yüz bin chunk'a kadar brute-force cosine milisaniyeler
sürüyor; ayrı bir vektör veritabanı bağımlılığı taşımıyoruz.

Network Asistanı'ndan taşındı; kilit ve anlık görüntü mantığı aynen korundu
(arka planda belge eklenirken aramanın çökmemesi için — orada yaşanmıştı).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

import config
from core import db

from . import embedder
from .pdf import Chunk

_lock = threading.RLock()

# Türkçe işlev sözcükleri. Kısa tutuldu: "madde", "sözleşme" gibi alan
# sözcükleri BİLEREK listede yok — kurumsal belgede ayırt edici olabiliyorlar.
_STOP = {
    "ve", "veya", "ile", "bir", "bu", "şu", "o", "için", "olarak", "da", "de",
    "ki", "mi", "mı", "mu", "mü", "ne", "nedir", "nasıl", "hangi", "gibi",
    "daha", "en", "çok", "her", "tüm", "olan", "olup", "ise", "ya", "hem",
    "the", "a", "an", "of", "and", "or", "to", "in", "is",
}


def _tokenize(text: str) -> list[str]:
    """BM25 için token'lar.

    `casefold` değil Türkçeye özel küçük harf: Python'un `lower()` işlevi "İ"yi
    "i̇" (i + birleşik nokta) yapıyor ve "İZMİR" ile "izmir" farklı token
    oluyordu. Önce Türkçe I/İ eşlemesi, sonra küçük harf.
    """
    text = text.replace("İ", "i").replace("I", "ı").lower()
    out = []
    for raw in text.replace("-", " ").replace("/", " ").split():
        tok = "".join(ch for ch in raw if ch.isalnum())
        if len(tok) > 1 and tok not in _STOP:
            out.append(tok)
    return out


@dataclass
class Hit:
    """Bulunan bir chunk ve ait olduğu belgenin künyesi."""

    row_id: int
    document_id: int
    title: str
    filename: str
    text: str
    section: str = ""
    kind: str = "body"               # 'summary' | 'body'
    ordinal: int = 0
    page_start: int | None = None
    page_end: int | None = None
    dense_score: float = 0.0
    rank_dense: int | None = None
    rank_bm25: int | None = None
    fused_score: float = 0.0

    @property
    def pages(self) -> str:
        if self.kind == "summary" or self.page_start is None:
            return "özet"
        if self.page_start == self.page_end:
            return f"s. {self.page_start}"
        return f"s. {self.page_start}-{self.page_end}"

    @property
    def location(self) -> str:
        """Chunk'ın belge içindeki yeri — atıfta ve puanlamada gösterilir."""
        if self.kind == "summary":
            return "Belge özeti"
        return f"{self.section}, {self.pages}" if self.section else self.pages


class VectorStore:
    """Süreç ömrü boyunca tek örnek olarak kullanılır (bkz. `get_store`)."""

    def __init__(self) -> None:
        self.dim = embedder.dim()
        self._vectors: np.ndarray = np.zeros((0, self.dim), dtype=np.float32)
        self._row_ids: list[int] = []
        self._doc_ids: list[int] = []
        self._alive: np.ndarray = np.zeros(0, dtype=bool)
        self._bm25_docs: list[list[str]] = []
        self._df: dict[str, int] = {}
        self._avg_len: float = 0.0
        self._loaded = False

    # ---------- kalıcılık ----------

    @property
    def _vectors_path(self):
        d = config.abs_path("paths.index_dir")
        d.mkdir(parents=True, exist_ok=True)
        return d / "vectors.npy"

    def load(self) -> "VectorStore":
        with _lock:
            if self._loaded:
                return self
            path = self._vectors_path
            if path.exists():
                arr = np.load(path)
                if arr.ndim == 2 and arr.shape[1] == self.dim:
                    self._vectors = arr.astype(np.float32)
            rows = db.connect().execute(
                """SELECT c.row_id, c.document_id, c.text, c.section, c.deleted, d.title
                   FROM chunks c JOIN documents d ON d.id = c.document_id
                   ORDER BY c.row_id"""
            ).fetchall()
            self._row_ids = [r["row_id"] for r in rows]
            self._doc_ids = [r["document_id"] for r in rows]
            self._alive = np.array([not r["deleted"] for r in rows], dtype=bool)
            self._bm25_docs = [
                _tokenize(f"{r['title'] or ''} {r['section'] or ''} {r['text']}") for r in rows
            ]
            # Vektör dosyası ile metadata sayısı uyuşmuyorsa metadata otoritedir.
            if len(self._vectors) != len(self._row_ids):
                self._vectors = self._vectors[: len(self._row_ids)]
            self._rebuild_bm25_stats()
            self._loaded = True
            return self

    def _persist_vectors(self) -> None:
        np.save(self._vectors_path, self._vectors)

    def _rebuild_bm25_stats(self) -> None:
        df: dict[str, int] = {}
        total = 0
        count = 0
        for doc, alive in zip(self._bm25_docs, self._alive):
            if not alive:
                continue
            count += 1
            total += len(doc)
            for token in set(doc):
                df[token] = df.get(token, 0) + 1
        self._df = df
        self._avg_len = (total / count) if count else 0.0

    # ---------- yazma ----------

    def add_document(
        self, document_id: int, title: str, summary: str, chunks: list[Chunk]
    ) -> int:
        """Bir belgenin özetini ve gövde chunk'larını indeksler."""
        self.load()
        with _lock:
            conn = db.connect()
            already = conn.execute(
                "SELECT COUNT(*) c FROM chunks WHERE document_id = ? AND deleted = 0",
                (document_id,),
            ).fetchone()["c"]
            if already:
                return 0

            documents: list[str] = []
            rows: list[tuple] = []
            if summary.strip():
                documents.append(f"{title}\n\n{summary}")
                rows.append((document_id, 0, "summary", "Belge özeti", None, None, summary))
            for c in chunks:
                documents.append(c.to_document(title))
                rows.append((document_id, c.ordinal + 1, "body", c.section,
                             c.page_start, c.page_end, c.text))
            if not documents:
                return 0

            vectors = embedder.encode(documents, is_query=False)
            next_row = len(self._row_ids)
            for offset, row in enumerate(rows):
                conn.execute(
                    """INSERT INTO chunks
                       (row_id, document_id, ordinal, kind, section, page_start, page_end, text)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (next_row + offset, *row),
                )
            conn.commit()

            # LİSTELER YERİNDE DEĞİŞTİRİLMİYOR, YENİLERİYLE DEĞİŞTİRİLİYOR.
            # `search` kilit almadan çalışıyor ve yalnızca bu nesnelere referans
            # tutuyor. `.extend()` ile büyütülselerdi arama, uzunlukları birbirini
            # tutmayan diziler görebilirdi ve `dense[idx]` sınır dışına çıkıp
            # çökerdi. Yeni nesneyle değiştirmek tutarlı bir anlık görüntü
            # garanti ediyor (kopyala-yaz).
            n = len(rows)
            self._row_ids = self._row_ids + list(range(next_row, next_row + n))
            self._doc_ids = self._doc_ids + [document_id] * n
            self._alive = np.concatenate([self._alive, np.ones(n, dtype=bool)])
            self._bm25_docs = self._bm25_docs + [_tokenize(d) for d in documents]
            self._vectors = (
                vectors if self._vectors.size == 0 else np.vstack([self._vectors, vectors])
            )
            self._persist_vectors()
            self._rebuild_bm25_stats()
            return n

    def remove_document(self, document_id: int) -> int:
        """Belgenin chunk'larını aramadan çıkarır.

        Satırlar fiziksel olarak silinmiyor: vektör matrisinde satır indeksi
        row_id'ye eşit ve ortadan bir satır silmek sonrakilerin hepsini
        kaydırırdı. `deleted` bayrağı yeterli — silinen belge aramaya girmez.
        """
        self.load()
        with _lock:
            conn = db.connect()
            cur = conn.execute(
                "UPDATE chunks SET deleted = 1 WHERE document_id = ? AND deleted = 0",
                (document_id,),
            )
            # Silinen satırın sırası negatife çekiliyor: UNIQUE(document_id,
            # ordinal, kind) silinmiş satırlarda da geçerli ve aynı belge yeniden
            # parçalandığında yeni chunk'lar eski sıra numaralarıyla çakışıyordu.
            #
            # Hedef aralık -(1.000.000 + row_id): hem row_id 0'da sıfır kalmıyor
            # (-row_id ilk belgenin özet chunk'ında 0 kalıp çakışmıştı) hem de
            # mevcut hiçbir değerle kesişmiyor. SQLite benzersizliği SATIR SATIR
            # denetliyor; -row_id'den -(row_id+1)'e geçiş her satırı komşusunun o
            # anki numarasına taşıyıp çakıştı. Tekrar çalıştırmak aynı değeri verir.
            conn.execute(
                """UPDATE chunks SET ordinal = -(1000000 + row_id)
                   WHERE document_id = ? AND deleted = 1 AND ordinal > -1000000""",
                (document_id,),
            )
            conn.commit()
            alive = self._alive.copy()
            for i, doc in enumerate(self._doc_ids):
                if doc == document_id:
                    alive[i] = False
            self._alive = alive
            self._rebuild_bm25_stats()
            return cur.rowcount

    # ---------- okuma ----------

    def document_chunks(self, document_ids: list[int]) -> list[Hit]:
        """Belgelerin tüm gövde chunk'ları, belge içi sırayla (arama yok)."""
        if not document_ids:
            return []
        rows = db.connect().execute(
            """SELECT c.row_id, c.document_id, c.ordinal, c.kind, c.section,
                      c.page_start, c.page_end, c.text, d.title, d.filename
               FROM chunks c JOIN documents d ON d.id = c.document_id
               WHERE c.deleted = 0 AND c.kind = 'body' AND c.document_id IN (%s)
               ORDER BY c.document_id, c.ordinal""" % ",".join("?" * len(document_ids)),
            document_ids,
        ).fetchall()
        return [
            Hit(row_id=r["row_id"], document_id=r["document_id"],
                title=r["title"] or r["filename"], filename=r["filename"], text=r["text"],
                section=r["section"] or "", kind=r["kind"], ordinal=r["ordinal"],
                page_start=r["page_start"], page_end=r["page_end"], dense_score=1.0,
                fused_score=1.0)
            for r in rows
        ]

    def vektorler(self, row_ids: list[int]) -> np.ndarray:
        """Saklı chunk vektörleri. Yeniden gömme yok: indekste zaten duruyorlar."""
        self.load()
        with _lock:
            sira = {rid: i for i, rid in enumerate(self._row_ids)}
            secili = [sira[r] for r in row_ids if r in sira and sira[r] < len(self._vectors)]
            if not secili:
                return np.zeros((0, self.dim), dtype=np.float32)
            return self._vectors[secili].copy()

    def komsular(self, istekler: list[tuple[int, int]]) -> list[Hit]:
        """Verilen (belge, ordinal) çiftlerindeki chunk'lar.

        Bir bölümün anlatısı chunk sınırında kesiliyor: ispatın ilk adımı bir
        parçada, devamı diğerinde. Arama yalnızca anahtar kelimeyi taşıyan
        parçayı getiriyor ve model yarım bir anlatı okuyor. Komşuları
        çağırmak yerel ve ücretsiz.
        """
        if not istekler:
            return []
        kosul = " OR ".join(["(c.document_id = ? AND c.ordinal = ?)"] * len(istekler))
        degerler = [x for cift in istekler for x in cift]
        rows = db.connect().execute(
            f"""SELECT c.row_id, c.document_id, c.ordinal, c.kind, c.section,
                       c.page_start, c.page_end, c.text, d.title, d.filename
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.deleted = 0 AND c.kind = 'body' AND ({kosul})
                ORDER BY c.document_id, c.ordinal""",
            degerler,
        ).fetchall()
        return [
            Hit(row_id=r["row_id"], document_id=r["document_id"],
                title=r["title"] or r["filename"], filename=r["filename"], text=r["text"],
                section=r["section"] or "", kind=r["kind"], ordinal=r["ordinal"],
                page_start=r["page_start"], page_end=r["page_end"], dense_score=0.0,
                fused_score=0.0)
            for r in rows
        ]

    def size(self) -> int:
        """Aramaya giren chunk sayısı."""
        self.load()
        return int(self._alive.sum())

    def _bm25_scores(self, query: str, *, docs, df, avg_len, alive,
                     k1: float = 1.5, b: float = 0.75) -> np.ndarray:
        tokens = _tokenize(query)
        n = len(docs)
        scores = np.zeros(n, dtype=np.float32)
        live = int(alive.sum())
        if not tokens or live == 0 or avg_len == 0:
            return scores
        for token in set(tokens):
            doc_freq = df.get(token, 0)
            if doc_freq == 0:
                continue
            idf = np.log(1 + (live - doc_freq + 0.5) / (doc_freq + 0.5))
            for i, doc in enumerate(docs):
                if not alive[i]:
                    continue
                tf = doc.count(token)
                if tf:
                    dl = len(doc)
                    scores[i] += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_len))
        return scores

    def search(
        self,
        query_text: str,
        *,
        query_vector: np.ndarray | None = None,
        top_k: int | None = None,
        candidate_k: int | None = None,
        mode: str = "hybrid",
        max_per_document: int | None = None,
        document_ids: set[int] | None = None,
        kinds: tuple[str, ...] | None = None,
    ) -> list[Hit]:
        """Dense + BM25 adaylarını RRF ile birleştirip ilk `top_k` chunk'ı döner.

        `document_ids` aramayı belirli belgelerle sınırlıyor — çapraz belge
        sentezinde her belge kendi içinde ayrı aranıyor. Filtre aday seçiminden
        ÖNCE uygulanıyor: sonradan elemek top_k'yı başka belgelerle doldurup
        o belgeyi hiç temsil etmeyebilirdi.
        """
        self.load()

        # TUTARLI ANLIK GÖRÜNTÜ — bkz. add_document'teki yorum.
        with _lock:
            row_ids = self._row_ids
            doc_ids = self._doc_ids
            alive = self._alive
            vectors = self._vectors
            bm25_docs, bm25_df, bm25_avg = self._bm25_docs, self._df, self._avg_len

        if not row_ids:
            return []

        top_k = top_k or int(config.get("retrieval.top_k", 8))
        candidate_k = candidate_k or int(config.get("retrieval.candidate_k", 20))
        rrf_k = int(config.get("retrieval.rrf_k", 60))
        if max_per_document is None:
            max_per_document = int(config.get("retrieval.max_chunks_per_document", 3))

        if query_vector is None:
            query_vector = embedder.encode_one(query_text, is_query=True)

        n = len(vectors)
        allowed = alive[:n].copy()
        if document_ids is not None:
            allowed &= np.array([d in document_ids for d in doc_ids[:n]], dtype=bool)
        if kinds is not None:
            # kinds filtresi için chunk türünü DB'den okumak yerine özet
            # chunk'ının ordinal 0 olduğunu biliyoruz; ama doğruluk için DB.
            kind_rows = {
                r["row_id"]: r["kind"]
                for r in db.connect().execute("SELECT row_id, kind FROM chunks")
            }
            allowed &= np.array([kind_rows.get(r) in kinds for r in row_ids[:n]], dtype=bool)
        if not allowed.any():
            return []

        dense = embedder.cosine(query_vector, vectors)
        dense = np.where(allowed, dense, -np.inf)

        # Aday havuzu top_k'nın birkaç katı olmalı: belge başına sınır
        # uygulanınca eleme oluyor, havuz dar kalırsa top_k dolmuyor.
        take = min(max(candidate_k, top_k * 4), int(allowed.sum()))
        dense_idx = np.argsort(-dense)[:take] if mode in ("hybrid", "dense") else []

        if mode in ("hybrid", "bm25"):
            bm25 = self._bm25_scores(
                query_text, docs=bm25_docs, df=bm25_df, avg_len=bm25_avg, alive=allowed
            )
            bm25_idx = [i for i in np.argsort(-bm25)[:take] if bm25[i] > 0]
        else:
            bm25_idx = []

        # Reciprocal Rank Fusion: iki listedeki SIRA bilgisini birleştirir,
        # birbiriyle kıyaslanamaz skorları normalize etme derdi kalmaz.
        fused: dict[int, float] = {}
        rank_dense: dict[int, int] = {}
        rank_bm25: dict[int, int] = {}
        for rank, idx in enumerate(dense_idx):
            fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (rrf_k + rank + 1)
            rank_dense[int(idx)] = rank + 1
        for rank, idx in enumerate(bm25_idx):
            fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (rrf_k + rank + 1)
            rank_bm25[int(idx)] = rank + 1

        ordered = sorted(fused.items(), key=lambda kv: -kv[1])
        if not ordered:
            return []

        rows = _fetch_chunk_rows([row_ids[i] for i, _ in ordered])

        hits: list[Hit] = []
        per_doc: dict[int, int] = {}
        for matrix_idx, score in ordered:
            row = rows.get(row_ids[matrix_idx])
            if row is None:
                continue
            seen = per_doc.get(row["document_id"], 0)
            if max_per_document and seen >= max_per_document:
                continue
            per_doc[row["document_id"]] = seen + 1
            hits.append(
                Hit(
                    row_id=row["row_id"],
                    document_id=row["document_id"],
                    title=row["title"] or row["filename"],
                    filename=row["filename"],
                    text=row["text"],
                    section=row["section"] or "",
                    kind=row["kind"],
                    ordinal=row["ordinal"],
                    page_start=row["page_start"],
                    page_end=row["page_end"],
                    dense_score=float(dense[matrix_idx]),
                    rank_dense=rank_dense.get(matrix_idx),
                    rank_bm25=rank_bm25.get(matrix_idx),
                    fused_score=float(score),
                )
            )
            if len(hits) >= top_k:
                break
        return hits


def _fetch_chunk_rows(row_ids: list[int]) -> dict:
    if not row_ids:
        return {}
    return {
        r["row_id"]: r
        for r in db.connect().execute(
            """SELECT c.row_id, c.document_id, c.ordinal, c.kind, c.section,
                      c.page_start, c.page_end, c.text, d.title, d.filename
               FROM chunks c JOIN documents d ON d.id = c.document_id
               WHERE c.row_id IN (%s)""" % ",".join("?" * len(row_ids)),
            row_ids,
        )
    }


_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = VectorStore().load()
    return _store


def reset() -> None:
    """Testlerde config/DB değiştikten sonra deponun yeniden kurulması için."""
    global _store
    _store = None
