"""Metin katmanı olan sayfaları yeniden okur, sonra belgeyi yeniden parçalar.

    python scripts/yeniden_oku.py            # tüm hazır belgeler
    python scripts/yeniden_oku.py 5 7        # yalnızca bu belgeler

Metin çıkarma kuralı değiştiğinde (sütun ayrımı, tablo tanıma) çalıştırılır.
`yeniden_parcala.py` yalnızca SAKLI sayfa metninden yeniden böler; bu script
sayfa metnini PDF'ten baştan çıkarır.

TARANMIŞ SAYFALARA DOKUNULMUYOR: onların metni OCR'dan geldi, PDF'te metin
katmanı yok. Yeniden okunsalar boşalırlardı ve OCR'ı tekrarlamak model çağrısı,
kota ve dakikalarca süre demek. Özet de yeniden üretilmiyor.

UYGULAMA KAPALIYKEN çalıştırın: uygulama indeksi bellekte tutuyor ve iki süreç
aynı vektör dosyasına yazarsa satır numaraları çakışır.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from belge import ingest, pdf as pdfmod  # noqa: E402
from core import db  # noqa: E402

secilen = {int(a) for a in sys.argv[1:] if a.isdigit()}

conn = db.connect()
belgeler = conn.execute(
    "SELECT id, sha256, title FROM documents WHERE status = 'ready' ORDER BY id"
).fetchall()

for belge in belgeler:
    if secilen and belge["id"] not in secilen:
        continue
    yol = ingest._dosya_yolu(belge["sha256"])
    if not yol.exists():
        print(f"  #{belge['id']:<3} dosya yok, atlandı  {belge['title']}")
        continue

    sayfalar = pdfmod.text_layer(yol.read_bytes())
    degisen = 0
    for satir in conn.execute(
        "SELECT page_no, source, text FROM pages WHERE document_id = ? ORDER BY page_no",
        (belge["id"],),
    ).fetchall():
        if satir["source"] != "text" or satir["page_no"] > len(sayfalar):
            continue
        yeni = pdfmod.clean(sayfalar[satir["page_no"] - 1])
        if yeni.strip() and yeni != satir["text"]:
            conn.execute("UPDATE pages SET text = ? WHERE document_id = ? AND page_no = ?",
                         (yeni, belge["id"], satir["page_no"]))
            degisen += 1
    conn.commit()

    n = ingest.yeniden_parcala(belge["id"])
    print(f"  #{belge['id']:<3} {degisen:3} sayfa yeniden okundu, {n:3} chunk  {belge['title']}")
