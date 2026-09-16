"""Belge yükleme hattı.

    PDF baytları
      -> doğrula (boyut, sayfa sayısı, gerçekten PDF mi)
      -> içerik özeti (sha256) — aynı dosya ikinci kez işlenmiyor
      -> sayfa sayfa metin katmanı
      -> metin katmanı yetersiz sayfalar: görüntü -> ön işleme -> OCR
      -> sayfa bazlı chunk'lar
      -> belge özeti (başlık + tür + öne çıkanlar)
      -> indeks (özet chunk'ı + gövde chunk'ları)

HATA POLİTİKASI: tek bir sayfanın OCR'ı başarısız olursa belge düşmüyor; o
sayfa boş kalıyor ve uyarı kaydediliyor. Hiçbir sayfadan metin çıkmazsa belge
`failed` oluyor. Özet üretilemezse belge yine indeksleniyor — özet bir kolaylık,
belgenin aranabilir olması onun ön koşulu değil.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Callable

import config
from core import db
from llm.base import LLMError

from . import ocr, ozet
from . import pdf as pdfmod
from .store import get_store


@dataclass
class YuklemeSonucu:
    document_id: int | None
    filename: str
    status: str                     # ready | failed | duplicate
    title: str = ""
    kind: str = ""
    summary: str = ""
    page_count: int = 0
    ocr_pages: int = 0
    chunks: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    error: str = ""


Ilerleme = Callable[[str, int, int], None]   # (aşama, şimdiki, toplam)


def _dosya_yolu(sha: str):
    d = config.abs_path("paths.belgeler_dir")
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sha}.pdf"


def pdf_bytes(document_id: int) -> bytes | None:
    """Yüklenmiş belgenin kendisi — arayüzde sayfa görüntüsü için."""
    row = db.connect().execute(
        "SELECT sha256 FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if row is None:
        return None
    path = _dosya_yolu(row["sha256"])
    return path.read_bytes() if path.exists() else None


def yukle(data: bytes, filename: str, *, on_progress: Ilerleme | None = None,
          owner_id: int | None = None, paylasim: str = "ozel") -> YuklemeSonucu:
    started = time.perf_counter()
    bildir = on_progress or (lambda *_: None)
    sonuc = YuklemeSonucu(document_id=None, filename=filename, status="failed")

    # --- doğrulama -------------------------------------------------------
    max_mb = float(config.get("belge.max_upload_mb", 100))
    if len(data) > max_mb * 1024 * 1024:
        sonuc.error = f"Dosya {len(data) / 1048576:.1f} MB — üst sınır {max_mb:.0f} MB"
        return sonuc
    try:
        sayfa_sayisi = pdfmod.page_count(data)
    except pdfmod.PdfError as exc:
        sonuc.error = str(exc)
        return sonuc
    max_pages = int(config.get("belge.max_pages", 300))
    if sayfa_sayisi == 0:
        sonuc.error = "PDF'te hiç sayfa yok"
        return sonuc
    if sayfa_sayisi > max_pages:
        sonuc.error = f"{sayfa_sayisi} sayfa — üst sınır {max_pages}"
        return sonuc

    conn = db.connect()
    sha = hashlib.sha256(data).hexdigest()

    # --- tekrar yükleme --------------------------------------------------
    # TEKRAR KONTROLÜ KULLANICI BAZINDA. sha256 tekil olduğu için aynı PDF'i
    # ikinci bir kullanıcı yüklediğinde "zaten yüklü" deyip BAŞKASININ
    # belgesine bağlıyorduk — kullanıcı belgesini göremiyor, üstelik başka
    # birinin kaydına erişmiş oluyordu. Artık tekrar yalnızca aynı sahip için.
    var = conn.execute(
        """SELECT id, title, status, page_count, ocr_pages, summary FROM documents
           WHERE sha256 = ? AND (owner_id IS ? OR owner_id = ?)""",
        (sha, owner_id, owner_id),
    ).fetchone()
    if var is not None and var["status"] in ("ready", "processing"):
        sonuc.document_id = var["id"]
        sonuc.status = "duplicate"
        sonuc.title = var["title"] or filename
        sonuc.summary = var["summary"] or ""
        sonuc.page_count = var["page_count"]
        sonuc.ocr_pages = var["ocr_pages"]
        sonuc.warnings.append("Bu dosya daha önce yüklenmiş, yeniden işlenmedi")
        return sonuc
    if var is None:
        # Aynı içerik BAŞKA bir sahipte olabilir; sha256 sütunu tekil olduğu
        # için kendi kaydımıza sahip ekli bir anahtar yazıyoruz.
        baska = conn.execute("SELECT 1 FROM documents WHERE sha256 = ?", (sha,)).fetchone()
        if baska:
            sha = f"{sha}:u{owner_id}"
    if var is not None:
        # Daha önce başarısız olmuş ya da silinmiş: temiz bir kayıtla yeniden dene.
        conn.execute("UPDATE documents SET sha256 = ? WHERE id = ?", (f"{sha}:eski:{var['id']}", var["id"]))
        conn.commit()

    cur = conn.execute(
        "INSERT INTO documents (sha256, filename, page_count, status, owner_id, paylasim) "
        "VALUES (?,?,?, 'processing', ?, ?)",
        (sha, filename, sayfa_sayisi, owner_id, paylasim),
    )
    conn.commit()
    doc_id = cur.lastrowid
    sonuc.document_id = doc_id
    sonuc.page_count = sayfa_sayisi
    _dosya_yolu(sha).write_bytes(data)

    try:
        # --- sayfalar ----------------------------------------------------
        katman = pdfmod.text_layer(data)
        ocr_gerekli = [i for i, t in enumerate(katman, 1) if pdfmod.needs_ocr(t)]
        try:
            agir = pdfmod.heading_lines(data)
        except Exception as exc:   # başlık tespiti bir iyileştirme; belgeyi düşürmemeli
            agir = [set() for _ in katman]
            sonuc.warnings.append(f"Yazı tipinden başlık tespiti yapılamadı: {exc}")
        sayfalar: list[pdfmod.Page] = []
        for no, metin in enumerate(katman, 1):
            if no not in ocr_gerekli:
                sayfalar.append(pdfmod.Page(no, metin, "text", frozenset(agir[no - 1])))
                continue
            bildir("ocr", ocr_gerekli.index(no) + 1, len(ocr_gerekli))
            try:
                okunan = ocr.oku(pdfmod.render_page(data, no), no)
                sayfalar.append(pdfmod.Page(no, okunan.text, "ocr"))
                if "[okunamadı]" in okunan.text:
                    sonuc.warnings.append(
                        f"Sayfa {no}: {okunan.text.count('[okunamadı]')} yer okunamadı"
                    )
            except LLMError as exc:
                sayfalar.append(pdfmod.Page(no, "", "ocr"))
                sonuc.warnings.append(f"Sayfa {no} okunamadı: {exc}")

        sonuc.ocr_pages = len(ocr_gerekli)
        conn.executemany(
            "INSERT INTO pages (document_id, page_no, source, text) VALUES (?,?,?,?)",
            [(doc_id, p.page_no, p.source, p.text) for p in sayfalar],
        )
        conn.commit()

        if not any(p.text.strip() for p in sayfalar):
            raise RuntimeError("Hiçbir sayfadan metin çıkarılamadı")

        # --- özet ----------------------------------------------------------
        bildir("ozet", 1, 1)
        try:
            o = ozet.ozetle(sayfalar, filename)
            sonuc.title, sonuc.kind, sonuc.summary = o.title, o.kind, o.text
        except LLMError as exc:
            sonuc.title = filename.rsplit(".", 1)[0].replace("_", " ")
            sonuc.warnings.append(f"Özet üretilemedi, belge özetsiz indekslendi: {exc}")

        # --- indeks --------------------------------------------------------
        bildir("indeks", 1, 1)
        parcalar = pdfmod.chunk_pages(sayfalar)
        sonuc.chunks = get_store().add_document(doc_id, sonuc.title, sonuc.summary, parcalar)

        conn.execute(
            """UPDATE documents SET status = 'ready', title = ?, summary = ?,
                   ocr_pages = ?, error = NULL WHERE id = ?""",
            (sonuc.title, _ozet_kaydi(sonuc), sonuc.ocr_pages, doc_id),
        )
        conn.commit()
        sonuc.status = "ready"
    except Exception as exc:  # belge yarım kalmasın: nedenini yazıp işaretle
        sonuc.status = "failed"
        sonuc.error = f"{type(exc).__name__}: {exc}"
        conn.execute(
            "UPDATE documents SET status = 'failed', error = ? WHERE id = ?",
            (sonuc.error[:500], doc_id),
        )
        conn.commit()
    finally:
        sonuc.seconds = time.perf_counter() - started
    return sonuc


def _ozet_kaydi(sonuc: YuklemeSonucu) -> str:
    return f"Tür: {sonuc.kind}\n\n{sonuc.summary}" if sonuc.kind else sonuc.summary


def sil(document_id: int) -> bool:
    """Belgeyi aramadan ve diskten kaldırır.

    `documents` satırı SİLİNMİYOR, durumu 'deleted' oluyor: indeks chunk'ları
    satır indeksiyle vektör matrisine bağlı ve satırı silmek o eşlemeyi
    bozardı. Metin (sayfalar) ve PDF dosyası ise gerçekten siliniyor — silinen
    bir belgenin içeriği diskte kalmamalı.
    """
    conn = db.connect()
    row = conn.execute("SELECT sha256, status FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None or row["status"] == "deleted":
        return False
    get_store().remove_document(document_id)
    conn.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
    conn.execute(
        "UPDATE chunks SET text = '' WHERE document_id = ?", (document_id,)
    )
    conn.execute(
        "UPDATE documents SET status = 'deleted', summary = NULL WHERE id = ?", (document_id,)
    )
    conn.commit()
    path = _dosya_yolu(row["sha256"])
    if path.exists():
        path.unlink()
    return True


def yeniden_parcala(document_id: int) -> int:
    """Belgeyi SAKLI sayfa metninden yeniden parçalayıp indeksler.

    Parçalama kuralları değiştiğinde belgeyi silip yeniden yüklemek OCR'ı ve
    özeti baştan yaptırır: model çağrısı, kota ve süre. Sayfa metinleri zaten
    veritabanında; yeniden gömme de yerel. Yazı ağırlığından başlık tespiti için
    saklı PDF dosyası kullanılıyor.
    """
    conn = db.connect()
    belge = conn.execute(
        "SELECT sha256, title, filename, summary FROM documents WHERE id = ? AND status = 'ready'",
        (document_id,),
    ).fetchone()
    if belge is None:
        return 0
    rows = conn.execute(
        "SELECT page_no, source, text FROM pages WHERE document_id = ? ORDER BY page_no",
        (document_id,),
    ).fetchall()
    yol = _dosya_yolu(belge["sha256"])
    agir = pdfmod.heading_lines(yol.read_bytes()) if yol.exists() else []
    sayfalar = [
        pdfmod.Page(r["page_no"], r["text"], r["source"],
                    frozenset(agir[r["page_no"] - 1]) if r["source"] == "text" and len(agir) >= r["page_no"] else frozenset())
        for r in rows
    ]
    ozet = belge["summary"] or ""
    if ozet.startswith("Tür:"):
        ozet = ozet.split("\n", 1)[1].strip() if "\n" in ozet else ""
    store = get_store()
    store.remove_document(document_id)
    return store.add_document(document_id, belge["title"] or belge["filename"], ozet,
                              pdfmod.chunk_pages(sayfalar))


def listele(owner_id: int | None = None, *, hepsi: bool = False) -> list[dict]:
    """Belgeler. `owner_id` verilince yalnızca o kullanıcınınkiler + ortak havuz.

    `hepsi=True` yalnızca bakım scriptleri için (yeniden parçalama, ölçüm).
    Arayüz her zaman bir sahip vererek çağırıyor.
    """
    alanlar = ("id, filename, title, page_count, ocr_pages, status, error, "
               "summary, added_at, owner_id, paylasim")
    conn = db.connect()
    if owner_id is None or hepsi:
        rows = conn.execute(
            f"SELECT {alanlar} FROM documents WHERE status != 'deleted' ORDER BY id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT {alanlar} FROM documents
                WHERE status != 'deleted' AND (owner_id = ? OR paylasim = 'ortak')
                ORDER BY id DESC""",
            (owner_id,),
        ).fetchall()
    return [dict(r) for r in rows]
