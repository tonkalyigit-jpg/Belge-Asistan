"""Sayfa düzenini koruyan metin çıkarma: sütunlar ve tablolar.

pypdfium2'nin `get_text_range()` çağrısı metni PDF'in içindeki ÇİZİM SIRASINA
göre veriyor. Tek sütunlu bir sözleşmede bu doğru sonucu üretiyor; iki sütunlu
bir veri sayfasında üretmiyor. ÖLÇÜLDÜ (G9 Converged Media Gateway, s. 3):

    • HxWxD: 15U, 26.25" x 17.38" x 18.50"; • NEBS Level 3 per GR-63-CORE, ...
    66.68 x 44.15 x 47.00 cm               • NEBS Level 3 per GR-1089-CORE, ...

Soldaki "Dimensions" listesiyle sağdaki "Compliances" listesi satır satır iç
içe geçiyor. Bu metni okuyan model, şasi ölçüsünü NEBS uyumluluğuyla aynı
cümlede görüyor; chunk sınırları da yanlış yere düşüyor.

Burada sayfa önce kolonlara ayrılıyor (kelime koordinatlarından), her kolon
kendi içinde yukarıdan aşağı okunuyor. Çizgili tablolar ise markdown tabloya
çevriliyor: "| Port | Kapasite |" satırı, aynı verinin düz metne serpilmiş
hâlinden hem modele hem BM25'e daha çok şey söylüyor.

pdfplumber (MIT) kullanılıyor, PyMuPDF (AGPL) DEĞİL: kurumsal bir üründe
AGPL'in bulaşıcı lisans şartları sorun olur.
"""
from __future__ import annotations

# Sütun ayrımı için bir kolonun sayfanın en az bu kadarını kaplaması gerekiyor.
# Daha küçük bir pay, kenar notu ya da sayfa numarası sütunu demek.
_MIN_KOLON_PAYI = 0.15
# Kolonlar arasında olması gereken en küçük boşluk (punto cinsinden ~2 karakter).
_MIN_BOSLUK = 12.0
# Bir tablonun tablo sayılması için en az bu kadar satır ve sütun.
_MIN_TABLO_SATIR, _MIN_TABLO_SUTUN = 2, 2


class DuzenYok(Exception):
    """pdfplumber yok ya da sayfa okunamadı — çağıran eski yola düşsün."""


def sayfa_metinleri(data: bytes) -> list[str]:
    """Her sayfa için düzen korunmuş metin. Hata hâlinde `DuzenYok` fırlatır."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - kurulumda var
        raise DuzenYok("pdfplumber kurulu değil") from exc

    import io

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return [_sayfa(sayfa) for sayfa in pdf.pages]
    except DuzenYok:
        raise
    except Exception as exc:
        raise DuzenYok(str(exc)) from exc


def _sayfa(sayfa) -> str:
    """Bir sayfanın metni: önce tablolar, kalan metin sütun sırasıyla."""
    tablolar = _tablolar(sayfa)
    govde = _govde(sayfa, [t["bbox"] for t in tablolar])
    parcalar = [govde] if govde else []
    parcalar += [t["markdown"] for t in tablolar]
    return "\n\n".join(p for p in parcalar if p.strip())


def _tablolar(sayfa) -> list[dict]:
    """Çizgili tabloları markdown'a çevirir.

    YALNIZCA ÇİZGİLİ tablolar. pdfplumber'ın "text" stratejisi hizalanmış her
    şeyi tablo sanıyor: G9 veri sayfasının iki sütunlu madde listesi 45 satır
    7 sütunluk bir "tablo" olarak çıktı ve içeriği paramparça etti. Çizgi
    aramak, gerçek tabloyu yakalayıp yanlış pozitif üretmiyor.
    """
    out = []
    try:
        bulunan = sayfa.find_tables()
    except Exception:
        return out
    for tablo in bulunan:
        try:
            satirlar = tablo.extract()
        except Exception:
            continue
        satirlar = [[(h or "").replace("\n", " ").strip() for h in satir] for satir in satirlar]
        satirlar = [s for s in satirlar if any(h for h in s)]
        if len(satirlar) < _MIN_TABLO_SATIR:
            continue
        sutun = max(len(s) for s in satirlar)
        if sutun < _MIN_TABLO_SUTUN:
            continue
        out.append({"bbox": tablo.bbox, "markdown": _markdown(satirlar, sutun)})
    return out


def _markdown(satirlar: list[list[str]], sutun: int) -> str:
    def satir_yaz(hucreler: list[str]) -> str:
        dolu = [(h or "").replace("|", "\\|") for h in hucreler]
        dolu += [""] * (sutun - len(dolu))
        return "| " + " | ".join(dolu) + " |"

    bas, gerisi = satirlar[0], satirlar[1:]
    ayrac = "| " + " | ".join(["---"] * sutun) + " |"
    return "\n".join([satir_yaz(bas), ayrac] + [satir_yaz(s) for s in gerisi])


def _govde(sayfa, tablo_bboxlari: list[tuple]) -> str:
    """Tablo alanları dışındaki metin, sütun sırasıyla."""
    kelimeler = sayfa.extract_words(keep_blank_chars=False, use_text_flow=False)
    kelimeler = [k for k in kelimeler if not _tabloda(k, tablo_bboxlari)]
    if not kelimeler:
        return ""

    sinir = _kolon_siniri(kelimeler, sayfa.width)
    if sinir is None:
        return _oku(kelimeler)
    return _iki_kolon(kelimeler, sinir)


def _tabloda(kelime: dict, bboxlar: list[tuple]) -> bool:
    x = (kelime["x0"] + kelime["x1"]) / 2
    y = (kelime["top"] + kelime["bottom"]) / 2
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in bboxlar)


def _kolon_siniri(kelimeler: list[dict], genislik: float) -> float | None:
    """İki sütunlu sayfada kolonlar arasındaki x koordinatı; yoksa None.

    Ölçüt: adayı KESEN kelime (x0 < aday < x1) neredeyse hiç olmayacak, iki
    yanda da yeterince kelime kalacak ve aradaki boşluk gerçekten açık olacak.

    "Neredeyse": sıfır kesen aranınca sayfanın altındaki tam genişlikteki
    telif satırı ("… All Rights Reserved.") her adayı düşürüyordu ve iki
    sütunlu veri sayfası tek sütun sanılıp satırları iç içe geçiyordu
    (ÖLÇÜLDÜ, G9 s. 3: "• Weight: 80 lbs chassis only safety"). Başlık ve
    altbilgi gibi tam genişlikteki birkaç satır kuralın istisnası; aşağıda
    kendi bantlarında ayrıca ele alınıyorlar.
    """
    if len(kelimeler) < 20:
        return None
    tolerans = max(2, int(len(kelimeler) * 0.02))
    toplam = len(kelimeler)
    en_iyi, en_iyi_kesen, en_iyi_bosluk = None, None, 0.0
    adim = max(2.0, genislik / 150)
    x = genislik * 0.30
    while x <= genislik * 0.70:
        kesen = sum(1 for k in kelimeler if k["x0"] < x < k["x1"])
        if kesen <= tolerans:
            sol = sum(1 for k in kelimeler if k["x1"] <= x)
            sag = sum(1 for k in kelimeler if k["x0"] >= x)
            if min(sol, sag) / toplam >= _MIN_KOLON_PAYI:
                sol_kenar = max((k["x1"] for k in kelimeler if k["x1"] <= x), default=0.0)
                sag_kenar = min((k["x0"] for k in kelimeler if k["x0"] >= x), default=genislik)
                bosluk = sag_kenar - sol_kenar
                if bosluk >= _MIN_BOSLUK and bosluk > en_iyi_bosluk:
                    en_iyi, en_iyi_kesen, en_iyi_bosluk = x, kesen, bosluk
        x += adim
    return en_iyi


def _iki_kolon(kelimeler: list[dict], sinir: float) -> str:
    """Sol kolon, sağ kolon; tam genişlikteki satırlar kendi yerlerinde.

    Sayfa, sınırı kesen kelimelerin (başlık, altbilgi, tam genişlikte ara
    başlık) y konumlarına göre bantlara ayrılıyor. Her bant kendi içinde
    "önce sol kolon, sonra sağ kolon" olarak okunuyor. Bant mantığı olmasa
    sayfanın ortasındaki tam genişlikte bir başlık ya en başa ya en sona
    düşer ve hangi sütuna ait olduğu kaybolurdu.
    """
    kesenler = [k for k in kelimeler if k["x0"] < sinir < k["x1"]]
    sinirlar = sorted({round(k["top"], 1) for k in kesenler})

    parcalar: list[str] = []
    ust = float("-inf")
    for alt in sinirlar + [float("inf")]:
        bant = [k for k in kelimeler if ust < round(k["top"], 1) < alt
                and not (k["x0"] < sinir < k["x1"])]
        sol = [k for k in bant if k["x1"] <= sinir]
        sag = [k for k in bant if k["x0"] >= sinir]
        for kolon in (sol, sag):
            metin = _oku(kolon)
            if metin:
                parcalar.append(metin)
        if alt != float("inf"):
            tam = [k for k in kesenler if round(k["top"], 1) == alt]
            # Tam genişlikteki satırın kendi hizasındaki diğer kelimeler de
            # aynı satırın parçası (kesen kelime satırın yalnızca bir kısmı).
            tam += [k for k in kelimeler if round(k["top"], 1) == alt
                    and not (k["x0"] < sinir < k["x1"])]
            parcalar.append(_oku(tam))
        ust = alt
    return "\n".join(p for p in parcalar if p.strip())


def _oku(kelimeler: list[dict], *, satir_toleransi: float = 3.0) -> str:
    """Kelimeleri satırlara toplayıp yukarıdan aşağı, soldan sağa okur."""
    if not kelimeler:
        return ""
    sirali = sorted(kelimeler, key=lambda k: (round(k["top"], 1), k["x0"]))
    satirlar: list[list[dict]] = [[sirali[0]]]
    for kelime in sirali[1:]:
        if abs(kelime["top"] - satirlar[-1][0]["top"]) <= satir_toleransi:
            satirlar[-1].append(kelime)
        else:
            satirlar.append([kelime])
    return "\n".join(
        " ".join(k["text"] for k in sorted(satir, key=lambda k: k["x0"]))
        for satir in satirlar
    )
