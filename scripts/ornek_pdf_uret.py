"""Sentetik belgelerden üç biçimde PDF üretir: dijital, temiz tarama, kötü tarama.

    python scripts/ornek_pdf_uret.py

Metnin aslı tests/ornek_metinler.py'de. Aynı metnin üç biçimi olduğu için OCR
doğruluğu karakter karakter ölçülebiliyor — gerçek evrakta bu imkân yok.

Taramalar DETERMİNİSTİK (sabit tohum): aynı bozulma her koşuda aynı çıkmalı,
yoksa ön işlemenin etkisi ile rastlantıyı ayırmak mümkün olmaz.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

from tests.ornek_metinler import TUM_BELGELER  # noqa: E402

CIKTI = KOK / "tests" / "ornekler"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def dijital(belge: dict) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font("Arial", "", FONT)
    pdf.add_font("Arial", "B", FONT)
    for sayfa in belge["sayfalar"]:
        pdf.add_page()
        pdf.set_margins(22, 22, 22)
        for paragraf in sayfa.split("\n"):
            if not paragraf.strip():
                pdf.ln(3)
                continue
            baslik = paragraf.isupper() or paragraf.startswith("MADDE")
            pdf.set_font("Arial", "B" if baslik else "", 11 if baslik else 10.5)
            pdf.multi_cell(0, 5.6, paragraf)
            pdf.ln(1.2)
    return bytes(pdf.output())


def _sayfalar(pdf_bytes: bytes, dpi: int) -> list[Image.Image]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return [doc[i].render(scale=dpi / 72).to_pil().convert("L") for i in range(len(doc))]
    finally:
        doc.close()


def _pdf_yap(goruntuler: list[Image.Image], dpi: int, *, jpeg_kalite: int) -> bytes:
    # Görüntüler JPEG olarak gömülüyor: gerçek tarayıcılar da böyle yapıyor ve
    # sıkıştırma artefaktları kötü taramanın bir parçası.
    sayfalar = []
    for g in goruntuler:
        tampon = io.BytesIO()
        g.save(tampon, "JPEG", quality=jpeg_kalite)
        sayfalar.append(Image.open(io.BytesIO(tampon.getvalue())))
    cikti = io.BytesIO()
    sayfalar[0].save(cikti, "PDF", save_all=True, append_images=sayfalar[1:], resolution=dpi)
    return cikti.getvalue()


def temiz_tarama(pdf_bytes: bytes) -> bytes:
    return _pdf_yap(_sayfalar(pdf_bytes, 200), 200, jpeg_kalite=90)


def kotu_tarama(pdf_bytes: bytes, tohum: int) -> bytes:
    rng = np.random.default_rng(tohum)
    bozuk = []
    for i, g in enumerate(_sayfalar(pdf_bytes, 150)):
        aci = (2.2 + 0.6 * i) * (1 if i % 2 == 0 else -1)     # eğik yerleştirilmiş kâğıt
        g = g.rotate(aci, resample=Image.BICUBIC, fillcolor=236)
        # Çözünürlük kaybı: düşük dpi'de taranıp büyütülmüş gibi
        w, h = g.size
        g = g.resize((int(w * 0.62), int(h * 0.62)), Image.BILINEAR).resize((w, h), Image.BILINEAR)
        g = g.filter(ImageFilter.GaussianBlur(0.7))
        g = ImageEnhance.Contrast(g).enhance(0.55)              # soluk toner
        g = ImageEnhance.Brightness(g).enhance(0.93)            # sararmış kâğıt
        dizi = np.asarray(g, dtype=np.float32)
        dizi += rng.normal(0, 16, dizi.shape)                   # tarayıcı gürültüsü
        # Kir lekeleri
        for _ in range(40):
            y, x = rng.integers(0, h), rng.integers(0, w)
            r = int(rng.integers(1, 4))
            dizi[max(0, y - r):y + r, max(0, x - r):x + r] -= 90
        g = Image.fromarray(np.clip(dizi, 0, 255).astype(np.uint8))
        bozuk.append(g)
    return _pdf_yap(bozuk, 150, jpeg_kalite=38)


def main() -> None:
    CIKTI.mkdir(parents=True, exist_ok=True)
    for n, belge in enumerate(TUM_BELGELER):
        ad = Path(belge["dosya"]).stem
        d = dijital(belge)
        dosyalar = {
            f"{ad}.pdf": d,
            f"{ad}_tarama.pdf": temiz_tarama(d),
            f"{ad}_kotu_tarama.pdf": kotu_tarama(d, tohum=100 + n),
        }
        for dosya, veri in dosyalar.items():
            (CIKTI / dosya).write_bytes(veri)
            print(f"  {dosya:48} {len(veri) / 1024:7.0f} KB")


if __name__ == "__main__":
    main()
