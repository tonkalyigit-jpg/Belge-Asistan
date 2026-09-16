#!/usr/bin/env python3
"""Önbelleği / geri bildirimi / izleri temizler. İndekse dokunmaz.

    python scripts/reset_memory.py --feedback    # yalnızca oylar + few-shot havuzu
    python scripts/reset_memory.py --all         # üçü birden
    python scripts/reset_memory.py --stats       # ne var, sil demeden bak

Geri bildirim döngüsünü denerken lazım oluyor: bir soruya 👎 + düzeltme
verdikten sonra sınıflandırıcı o düzeltmeyi kalıcı olarak öğreniyor, ve
deneme amaçlı verilmiş bir düzeltme sonraki testleri yanıltıyor.

Yüklenen belgeler (documents tablosu + vektörler) BU SCRIPT TARAFINDAN
SİLİNMEZ; belge silmek arayüzden ya da belge.ingest.sil ile yapılır, çünkü
metnin ve PDF dosyasının da diskten kaldırılması gerekiyor.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db  # noqa: E402

TABLES = {
    "feedback": "oylar + few-shot düzeltme havuzu",
    "traces": "sorgu çalışma izleri",
}


def counts() -> dict[str, int]:
    conn = db.connect()
    out = {}
    for table in list(TABLES) + ["documents"]:
        out[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Bellek tablolarını temizle")
    parser.add_argument("--feedback", action="store_true")
    parser.add_argument("--traces", action="store_true")
    parser.add_argument("--all", action="store_true", help="üçünü birden")
    parser.add_argument("--stats", action="store_true", help="sadece göster")
    args = parser.parse_args()

    before = counts()
    print("Mevcut durum:")
    for table, label in TABLES.items():
        print(f"  {table:10} {before[table]:6}  {label}")
    print(f"  {'documents':10} {before['documents']:6}  yüklenen belgeler (korunur)")

    if args.stats:
        return 0

    targets = [t for t in TABLES if args.all or getattr(args, t)]
    if not targets:
        print("\nHiçbir şey seçilmedi. --feedback / --traces / --all kullan.")
        return 1

    conn = db.connect()
    print()
    for table in targets:
        conn.execute(f"DELETE FROM {table}")
        print(f"  ✓ {table} temizlendi ({before[table]} kayıt silindi)")
    conn.commit()
    conn.execute("VACUUM")

    print(f"\nBelgeler korundu: {before['documents']} belge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
