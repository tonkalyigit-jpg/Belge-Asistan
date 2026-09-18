"""Yüklü belgelerin şekillerini görselden okur ve indekse ekler.

    python scripts/sekil_ekle.py              # eksik olan tüm belgeler
    python scripts/sekil_ekle.py 5 6          # yalnızca bu belgeler
    python scripts/sekil_ekle.py --yeniden 6  # betimlemeleri silip yeniden üret

Şekil betimleme özelliği eklenmeden önce yüklenmiş belgeler için. Vision
modele sayfa başına bir çağrı gidiyor; ÜCRETSİZ KATMANDA dakikada 15 istek
sınırı var, o yüzden çağrılar arasında kısa bir bekleme var.

Taranmış sayfalar atlanıyor: onların metnini zaten aynı model okudu (OCR).

SUNUCU KAPALIYKEN çalıştırın: indeks bellekte tutuluyor ve iki süreç aynı
vektör dosyasına yazarsa satır numaraları çakışır.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from belge import gorsel, ingest  # noqa: E402
from belge import pdf as pdfmod  # noqa: E402
from core import db  # noqa: E402

yeniden = "--yeniden" in sys.argv
secilen = {int(a) for a in sys.argv[1:] if a.isdigit()}
bekleme = float(config.get("limits.gorsel_bekleme_s", 4))

conn = db.connect()
for belge in conn.execute(
    "SELECT id, sha256, title FROM documents WHERE status = 'ready' ORDER BY id"
).fetchall():
    if secilen and belge["id"] not in secilen:
        continue
    yol = ingest._dosya_yolu(belge["sha256"])
    if not yol.exists():
        print(f"  #{belge['id']:<3} dosya yok, atlandı")
        continue
    if yeniden:
        conn.execute("DELETE FROM figures WHERE document_id = ?", (belge["id"],))
        conn.commit()

    var = {r["page_no"] for r in conn.execute(
        "SELECT page_no FROM figures WHERE document_id = ?", (belge["id"],))}
    ocr_sayfalari = {r["page_no"] for r in conn.execute(
        "SELECT page_no FROM pages WHERE document_id = ? AND source = 'ocr'", (belge["id"],))}

    data = yol.read_bytes()
    sayfalar = [no for no in gorsel.sekilli_sayfalar(data)
                if no not in var and no not in ocr_sayfalari]
    if not sayfalar:
        print(f"  #{belge['id']:<3} betimlenecek şekil yok  {belge['title']}")
        continue

    print(f"  #{belge['id']:<3} {len(sayfalar)} sayfa betimleniyor  {belge['title']}")
    dpi = int(config.get("belge.ocr_dpi", 200))
    yazilan = 0
    for i, no in enumerate(sorted(sayfalar), 1):
        metin = gorsel.betimle(pdfmod.render_page(data, no, dpi=dpi), no)
        durum = f"{len(metin)} karakter" if metin else "şekil yok"
        print(f"      s.{no:<3} {durum}")
        if metin:
            conn.execute(
                "INSERT OR REPLACE INTO figures (document_id, page_no, text) VALUES (?,?,?)",
                (belge["id"], no, metin))
            conn.commit()
            yazilan += 1
        if i < len(sayfalar):
            time.sleep(bekleme)

    if yazilan:
        n = ingest.yeniden_parcala(belge["id"])
        print(f"      -> {yazilan} şekil eklendi, belge {n} chunk ile yeniden indekslendi")
