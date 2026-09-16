"""Tüm hazır belgeleri saklı sayfa metninden yeniden parçalar.

    python scripts/yeniden_parcala.py

Parçalama kuralı değiştikten sonra çalıştırılır. OCR ve özet tekrarlanmıyor,
model çağrısı yok. UYGULAMA KAPALIYKEN çalıştırın: uygulama indeksi bellekte
tutuyor ve iki süreç aynı vektör dosyasına yazarsa satır numaraları çakışır.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from belge import ingest  # noqa: E402
from core import db  # noqa: E402

for r in db.connect().execute("SELECT id, title FROM documents WHERE status = 'ready' ORDER BY id").fetchall():
    n = ingest.yeniden_parcala(r["id"])
    print(f"  #{r['id']:<3} {n:3} chunk  {r['title']}")
