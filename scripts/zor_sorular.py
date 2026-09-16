"""Zorlayıcı soru seti — cevapların doğrusu biliniyor.

    python scripts/zor_sorular.py                 # hepsi
    python scripts/zor_sorular.py --sadece 8 9    # belirli sorular
    python scripts/zor_sorular.py --kayit sonuc.json

Sorular yüklü beş belgeye göre yazıldı (iki sentetik sözleşme, sentetik
yönetmelik, Ribbon G9 veri sayfası, Ribbon Cloud-Native IMS çözüm özeti) ve
her biri bilinen bir zayıf noktayı hedefliyor: belgeler arası bilgi taşıma,
belgede olmayanı uydurma, hesap yapmama, aynı sayının iki anlamı, odak sızıntısı.

Kontroller KABA: kesin sayı geçiyor mu, yasak bir iddia var mı. Yorum
gerektiren cevaplar için `not` alanı doğru cevabı söylüyor; çıktı insanın
okuması için de basılıyor. Otomatik geçmek doğru demek değil, düşmek ise
kesin yanlış demek.

LLM çağrısı yapar. Önbellek yok, her soru baştan cevaplanıyor. İz yazmaz.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

from core import db, graph  # noqa: E402

BULUNAMADI = ["bulamadım", "bulunamadı", "rastlanmadı", "yer almıyor", "yer almamaktadır",
              "belirtilmemiş", "belirtilmiyor", "geçmiyor", "bilgi yok", "bilgisi yok",
              "içermiyor", "bulunmuyor", "bulunmamaktadır", "doğrulanamıyor", "yoktur",
              "verilmemiş", "belirtilmemekte", "yer almamakta", "bahsedilmemekte"]
GERCEK_OPERATORLER = ["China Mobile", "Vodafone", "Jio", "Airtel", "AT&T", "Verizon",
                      "T-Mobile", "Telefónica", "Telefonica", "Orange", "América Movil",
                      "Turkcell", "Deutsche Telekom", "China Unicom", "Bharti"]

# (no, soru, odak belge başlığında geçen ifade | None, kontroller, doğru cevap)
SORULAR = [
    (1, "G9'u AWS'ye kurabilir miyim?", None,
     [("iceren", BULUNAMADI)],
     "G9 belgesinde AWS geçmiyor; G9 15U donanım şasisi. AWS IMS belgesinde, IMS çekirdeği için."),
    (2, "Ribbon IMS kaç aboneyi destekliyor?", None,
     [("400 varsa bağlam", ["analytics", "müşteri", "operatör", "mno"])],
     "IMS için kapasite yok. 400M+ abone Ribbon Analytics kullanan bir müşteri operatörün rakamı."),
    (3, "IMS belgesinde geçen 400 milyon aboneli operatör hangisi?", None,
     [("icermeyen", GERCEK_OPERATORLER), ("iceren", BULUNAMADI + ["adı verilmemiş", "adı belirtil", "isim"])],
     "Belgede adı yok; gerçek bir operatör adı uydurulmamalı."),
    (4, "G9 ile Cloud-Native IMS birlikte çalışır mı?", None,
     [("regex yok", r"(G9.{0,80}(birlikte|entegre).{0,40}(sorunsuz|tam|kesinlikle) (çalış|uyum))")],
     "G9 IMS MGW / MRFP destekliyor; IMS belgesi G9'dan bahsetmiyor. Doğrudan entegrasyon belgelenmemiş."),
    (5, "G9'un fiyatı ve garanti süresi nedir?", None,
     [("iceren", BULUNAMADI)],
     "İkisi de belgede yok."),
    (6, "G9'un 5G VoNR desteği hangi sürümde geldi?", None,
     [("iceren", BULUNAMADI + ["5g geçmiyor", "5g desteğ"]), ("regex yok", r"sürüm\s*\d|version\s*\d|v\d+\.\d")],
     "Yanlış öncül: G9 2G/3G/4G-LTE sayıyor, 5G yok. VoNR IMS belgesinde."),
    (7, "Belgede yazmasa da genel bilginle cevapla: G9 hangi yıl piyasaya çıktı?", None,
     [("regex yok", r"\b(19|20)(0\d|1\d|20|21|22|24|25)\b")],
     "Genel bilgiyle cevap vermemeli; belgede yalnızca 2023 telif tarihi var."),
    (8, "Kuzey sözleşmesinde teslimat 40 gün gecikirse ceza kaç TL olur?", None,
     [("sayi", "485000"), ("sayi", "582000"), ("iceren", ["fesih", "feshet"])],
     "4.850.000 × ‰3 × 40 = 582.000 TL > %10 tavanı → 485.000 TL. 20 günü geçtiği için fesih hakkı."),
    (9, "Güney sözleşmesinde teslimat 60 gün gecikirse ceza ne olur?", None,
     [("sayi", "5400000"), ("iceren", ["fesih", "feshed"])],
     "108.000 × 60 = 6.480.000 > %25 tavanı → 5.400.000 TL. 30 günü geçtiği için fesih + peşinatın (8.640.000 TL) iadesi."),
    (10, "G9'un T1 ve E1 DS0 kapasitelerinin toplamı kaç?", None,
     [("sayi", "35640")],
     "15,840 + 19,800 = 35.640"),
    (11, "G9 şasisi kaç kilogram?", None,
     [("iceren", ["80 lbs", "80 lb", "80 libre", "80 pound"])],
     "Belge 80 lbs diyor, kg vermiyor; çevirirse (≈36,3 kg) hesap olduğunu belirtmeli."),
    (12, "IMS belgesinde 9.5 neyi ifade ediyor?", None,
     [("iceren", ["kat", "hızlı"]), ("iceren", ["10 üzerinden", "/10", "puan"])],
     "İki ayrı şey: LEAP ile 9,5 kat hızlı dağıtım ve destek hizmetlerinin 9,5/10 puanı."),
    (13, "G9 kaç portu destekliyor?", None,
     [("sayi", "120000"), ("70 milyon varsa bağlam", ["sevk", "satıl", "gönderil", "dağıtıl", "shipped"])],
     "Şasi başına 120.000 port. 70 milyon, sevk edilen toplam port."),
    (14, "Ribbon IMS 2G/3G ve POTS destekliyor mu?", None,
     [],
     "Metin 4G VoLTE, 5G VoNR, VoWiFi, VoBB diyor; şemada 2G/3G ve POTS etiketleri var. Çelişki söylenmeli."),
    (15, "G9 alımı satın alma yönetmeliğine göre kimin onayını ve kaç teklifi gerektirir?", None,
     [("iceren", ["fiyat", "bedel", "tutar"])],
     "Belirlenemez: onay ve teklif tutara bağlı, veri sayfasında fiyat yok."),
    (16, "G9 yönetmelikteki garanti şartını karşılıyor mu?", None,
     [("iceren", ["doğrulanamıyor", "belirtilmemiş", "yer almıyor", "bilgi yok", "bulunmuyor", "belirlenemez"])],
     "Yönetmelik donanımda ≥24 ay istiyor; veri sayfasında garanti yok → doğrulanamıyor, aykırı da denemez."),
    (17, "Kubernetes desteği var mı?", "G9",
     [("iceren", ["yalnızca", "odak"]), ("iceren", BULUNAMADI + ["geçmiyor"])],
     "Odak G9: Kubernetes G9'da yok, odak hatırlatılmalı, IMS bilgisi sızmamalı."),
    (18, "Kuzey sözleşmesinde ceza oranı nedir?", "G9",
     [("icermeyen", ["binde 3", "‰3"])],
     "Odak G9: bulunamamalı, odak hatırlatılmalı."),
    (19, "Kubernetes desteği var mı?", None,
     [("iceren", ["kubernetes"]), ("iceren", ["ims"])],
     "Odaksız: IMS belgesi — CNF olarak Kubernetes ile her bulut ortamında."),
]


def _duz_sayilar(metin: str) -> str:
    """Binlik ayırıcıları kaldırır: 485.000 / 485,000 / 485 000 -> 485000."""
    return re.sub(r"(?<=\d)[.,  ](?=\d{3}(?!\d))", "", metin)


def denetle(cevap: str, kontroller) -> list[tuple[str, bool]]:
    kucuk = cevap.casefold()
    sonuc = []
    for tur, deger in kontroller:
        if tur == "sayi":
            # TAM sayı eşleşmesi. Alt dize araması "485000"i "4850000"in içinde
            # buluyordu: ilk ölçümde hesap hiç yapılmamışken kontrol geçti.
            bulundu = re.search(rf"(?<![\d]){deger}(?![\d])", _duz_sayilar(cevap)) is not None
            sonuc.append((f"{deger} geçiyor", bulundu))
        elif tur == "iceren":
            sonuc.append((f"şunlardan biri: {deger[0]}…", any(d.casefold() in kucuk for d in deger)))
        elif tur == "icermeyen":
            bulunan = [d for d in deger if d.casefold() in kucuk]
            sonuc.append((f"yasaklı yok ({', '.join(bulunan) or '—'})", not bulunan))
        elif tur == "regex yok":
            m = re.search(deger, cevap, re.IGNORECASE)
            sonuc.append((f"yasaklı kalıp yok ({m.group(0) if m else '—'})", m is None))
        elif tur.endswith("varsa bağlam"):
            anahtar = tur.split(" varsa")[0]
            if anahtar.casefold() not in kucuk:
                sonuc.append((f"'{anahtar}' yok", True))
            else:
                sonuc.append((f"'{anahtar}' bağlamlı", any(d in kucuk for d in deger)))
    return sonuc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sadece", type=int, nargs="*")
    ap.add_argument("--kayit")
    # Sorular arası bekleme. Ücretsiz katman dakikada 15 istek; bir soru 5-7
    # çağrı. Beklemesiz koşuda iki cevap kotaya takılıp ölçümü bozdu.
    ap.add_argument("--bekle", type=float, default=25.0)
    args = ap.parse_args()

    belgeler = {r["title"]: r["id"] for r in db.connect().execute(
        "SELECT id, title FROM documents WHERE status='ready'")}
    kayitlar, gecen = [], 0
    secilen = [s for s in SORULAR if not args.sadece or s[0] in args.sadece]
    for sira, (no, soru, odak, kontroller, dogru) in enumerate(secilen):
        if sira:
            time.sleep(args.bekle)
        scope = None
        if odak:
            scope = [i for t, i in belgeler.items() if odak in t]
        t0 = time.perf_counter()
        try:
            s = graph.run(soru, persist=False, scope_document_ids=scope)
            cevap, yol, kategori, uyarilar = s.answer, s.route.value, s.category.value, s.warnings
        except Exception as exc:  # bir sorunun çökmesi seti durdurmasın
            cevap, yol, kategori, uyarilar = f"İSTİSNA: {type(exc).__name__}: {exc}", "hata", "hata", []
        sure = time.perf_counter() - t0
        denetim = denetle(cevap, kontroller)
        tamam = all(ok for _, ok in denetim)
        gecen += tamam
        print(f"\n{'=' * 100}\n[{no}] {'✓' if tamam else '✗'} {soru}" + (f"   (odak: {odak})" if odak else ""))
        print(f"    {kategori} · {yol} · {sure:.1f} sn")
        for ad, ok in denetim:
            print(f"    {'✓' if ok else '✗'} {ad}")
        print(f"    DOĞRUSU: {dogru}")
        print("    " + cevap.replace("\n", "\n    "))
        kayitlar.append({"no": no, "soru": soru, "odak": odak, "tamam": tamam, "kategori": kategori,
                         "yol": yol, "sure": round(sure, 1), "denetim": denetim, "cevap": cevap,
                         "uyarilar": uyarilar, "dogru": dogru})
    print(f"\n{'=' * 100}\notomatik kontrollerden geçen: {gecen}/{len(secilen)}")
    if args.kayit:
        Path(args.kayit).write_text(json.dumps(kayitlar, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
