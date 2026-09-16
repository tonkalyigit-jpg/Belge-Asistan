"""OCR doğruluğunu ve ön işlemenin etkisini ölçer.

    python scripts/ocr_olc.py            # tüm belgeler, tüm varyantlar
    python scripts/ocr_olc.py --hizli    # yalnızca kötü tarama, iki varyant

Önce `python scripts/ornek_pdf_uret.py` ile örnekler üretilmiş olmalı.

Metnin aslı bilindiği için (tests/ornek_metinler.py) karakter karakter
kıyaslanabiliyor. Üç metrik:

  CER   karakter hata oranı — düzenleme mesafesi / asıl metin uzunluğu
  WER   kelime hata oranı — "sözleşme" yerine "sözlesme" tek kelime hatası
  TRK   Türkçe işaret kaybı — ç ğ ı İ ö ş ü'nün kaçı kayboldu ya da başka
        harfe döndü. OCR'ın Türkçede en çok yanıldığı yer; genel CER bunu
        düşük gösterip gizleyebiliyor.

Boşluk farkları sayılmıyor: model satırları paragrafa birleştiriyor, bu hata
değil düzen farkı.

LLM çağrısı yapar (vision kademesi). Ücretsiz katmanda dakikada 15 istek.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

from belge import ocr, pdf  # noqa: E402
from belge.goruntu import Adimlar  # noqa: E402
from tests.ornek_metinler import TUM_BELGELER  # noqa: E402

TURKCE = "çğıİöşüÇĞÖŞÜ"

VARYANTLAR = {
    "ham": None,
    "eğiklik": Adimlar(egiklik=True, kontrast=False, gurultu=False),
    "eğiklik+kontrast": Adimlar(egiklik=True, kontrast=True, gurultu=False),
    "+gürültü (medyan)": Adimlar(egiklik=True, kontrast=True, gurultu=True),
}


def _duz(metin: str) -> str:
    return " ".join(metin.split())


def mesafe(a, b) -> int:
    """Levenshtein düzenleme mesafesi (iki satırlık DP)."""
    if len(a) < len(b):
        a, b = b, a
    onceki = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        simdiki = [i]
        for j, cb in enumerate(b, 1):
            simdiki.append(min(onceki[j] + 1, simdiki[j - 1] + 1, onceki[j - 1] + (ca != cb)))
        onceki = simdiki
    return onceki[-1]


def metrikler(asil: str, okunan: str) -> dict:
    a, o = _duz(asil), _duz(okunan)
    kayip = sum(max(0, a.count(h) - o.count(h)) for h in TURKCE)
    toplam = sum(a.count(h) for h in TURKCE)
    return {
        "cer": mesafe(a, o) / max(1, len(a)),
        "wer": mesafe(a.split(), o.split()) / max(1, len(a.split())),
        "trk": kayip / max(1, toplam),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hizli", action="store_true")
    args = ap.parse_args()

    turler = ["_kotu_tarama"] if args.hizli else ["_tarama", "_kotu_tarama"]
    varyantlar = (
        {k: VARYANTLAR[k] for k in ("ham", "eğiklik+kontrast")} if args.hizli else VARYANTLAR
    )

    # Metin katmanı: dijital PDF'te OCR yok, taban çizgisi olarak ölçülüyor.
    print("dijital PDF (metin katmanı, LLM yok)")
    for belge in TUM_BELGELER:
        veri = (KOK / "tests/ornekler" / belge["dosya"]).read_bytes()
        katman = pdf.text_layer(veri)
        m = [metrikler(a, o) for a, o in zip(belge["sayfalar"], katman)]
        print(f"  {Path(belge['dosya']).stem:34} CER {sum(x['cer'] for x in m)/len(m):6.2%}")
    print()

    sonuc: dict[tuple[str, str], list[dict]] = {}
    sureler: list[float] = []
    for tur in turler:
        for ad, adimlar in varyantlar.items():
            if tur == "_tarama" and ad not in ("ham", "eğiklik+kontrast"):
                continue      # temiz taramada eğiklik/gürültü yok, iki varyant yeter
            for belge in TUM_BELGELER:
                stem = Path(belge["dosya"]).stem
                veri = (KOK / "tests/ornekler" / f"{stem}{tur}.pdf").read_bytes()
                for s, asil in enumerate(belge["sayfalar"], 1):
                    t0 = time.perf_counter()
                    okunan = ocr.oku(pdf.render_page(veri, s), s, adimlar=adimlar or Adimlar(False, False, False))
                    sureler.append(time.perf_counter() - t0)
                    m = metrikler(asil, okunan.text)
                    sonuc.setdefault((tur, ad), []).append(m)
                    print(f"  {tur:13} {ad:18} {stem[:22]:22} s.{s}  CER {m['cer']:6.2%}  "
                          f"WER {m['wer']:6.2%}  TRK {m['trk']:6.2%}  {sureler[-1]:4.1f}s",
                          flush=True)

    print()
    print(f"{'tür':14} {'ön işleme':20} {'n':>3} {'CER':>8} {'WER':>8} {'TRK':>8}")
    print("-" * 66)
    for (tur, ad), m in sonuc.items():
        n = len(m)
        print(f"{tur.strip('_'):14} {ad:20} {n:3d} "
              f"{sum(x['cer'] for x in m)/n:8.2%} {sum(x['wer'] for x in m)/n:8.2%} "
              f"{sum(x['trk'] for x in m)/n:8.2%}")
    sureler.sort()
    print(f"\nsayfa başına süre: ortanca {sureler[len(sureler)//2]:.1f} sn, en yavaş {sureler[-1]:.1f} sn")


if __name__ == "__main__":
    main()
