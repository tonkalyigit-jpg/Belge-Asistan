"""PDF'ten sayfa bazlı metin çıkarma ve sayfa numarasını koruyan parçalama.

Network Asistanı'ndaki sürüm tüm sayfaları tek bir metne birleştiriyordu; arXiv
için yeterliydi çünkü atıf makale düzeyindeydi. Kurumsal belgede "sayfa 7,
Madde 5" diyebilmek gerekiyor, bu yüzden her parça hangi sayfalardan geldiğini
taşıyor.

Boru hattı: aç -> sayfa sayfa metin katmanını oku -> hangi sayfanın taranmış
olduğuna karar ver -> (taranmışsa sayfa görüntüsü, OCR ayrı modülde) ->
temizle -> bölüm başlıklarını bul -> parçala.

Taşınanlar (ölçülmüş ya da canlıda patlamış şeylerin düzeltmeleri):
ligatür normalizasyonu, eşleşmemiş surrogate onarımı, kelime sınırında örtüşme,
paragraf -> cümle -> kelime sırasıyla zorla bölme.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import config
from belge import duzen

# PDF metninde sık görülen ligatürler — normalize edilmezse "ﬁyat" ile "fiyat"
# farklı token olur ve BM25 kolu bu kelimeleri hiç eşleştiremez.
_LIGATURES = str.maketrans({
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
})

# Kurumsal Türkçe belgelerde başlık kalıpları:
#   "MADDE 5 - CEZAİ ŞART"  "Madde 12:"  "BİRİNCİ BÖLÜM"  "3.2 Kapsam"
#   "1. AMAÇ"  "GENEL HÜKÜMLER"
_MADDE = re.compile(r"^\s*(madde|md\.)\s*\d+", re.IGNORECASE)
_BOLUM = re.compile(
    r"^\s*((birinci|ikinci|üçüncü|dördüncü|beşinci|altıncı|yedinci|sekizinci|"
    r"dokuzuncu|onuncu)\s+bölüm|bölüm\s+[\divxlc]+|kısım\s+[\divxlc]+)\b",
    re.IGNORECASE,
)
_NUMARALI = re.compile(r"^\s*(\d+(?:\.\d+){0,3})[.)]?\s+(\S.{1,70})$")

# Başlık gibi görünen ama olmayan satırlar
_NOT_A_SECTION = re.compile(
    r"(tablo\s*\d|şekil\s*\d|sayfa\s*\d|http|www\.|@|©|tel:|faks|e-posta)",
    re.IGNORECASE,
)


@dataclass
class Page:
    page_no: int          # 1'den başlar
    text: str
    source: str           # 'text' (metin katmanı) | 'ocr'
    # Yazı tipi ağırlığından tespit edilen başlık satırları (temizlenmiş hâliyle).
    # Yalnızca metin katmanı olan sayfalarda dolu; taranmış sayfada yazı tipi
    # bilgisi yok ve metin kurallarına düşülüyor.
    headings: frozenset = field(default_factory=frozenset)


@dataclass
class Chunk:
    """Tek bir indekslenebilir parça."""

    ordinal: int
    section: str
    text: str
    page_start: int
    page_end: int

    @property
    def pages(self) -> str:
        if self.page_start == self.page_end:
            return f"s. {self.page_start}"
        return f"s. {self.page_start}-{self.page_end}"

    def to_document(self, title: str) -> str:
        """Embed edilecek metin — parçayı kendi başına anlamlı yapar.

        Başlık ve bölüm adı başa yazılıyor: "yüklenici bu tutarı öder" diye
        başlayan bağlamsız bir parça hangi sözleşmeye ait olduğunu söylemiyor.
        """
        header = title if not self.section else f"{title} — {self.section}"
        return f"{header}\n\n{self.text}"


class PdfError(RuntimeError):
    """PDF açılamadı, şifreli ya da bozuk."""


# ------------------------------------------------------------------ okuma


def _open(data: bytes):
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise PdfError("`pypdfium2` kurulu değil (pip install pypdfium2)") from exc
    if not data or data[:4] != b"%PDF":
        raise PdfError("Dosya bir PDF değil")
    try:
        return pdfium.PdfDocument(data)
    except Exception as exc:
        # Şifreli PDF de buraya düşüyor; parolayı bizden istemek yerine
        # kullanıcıya açık bir sebep veriyoruz.
        raise PdfError(f"PDF açılamadı (bozuk ya da şifreli olabilir): {exc}") from exc


def page_count(data: bytes) -> int:
    doc = _open(data)
    try:
        return len(doc)
    finally:
        doc.close()


def text_layer(data: bytes) -> list[str]:
    """Her sayfanın metin katmanı; sayfa başına en iyi çıkarıcı seçilir.

    İKİ ÇIKARICI, SAYFA BAZINDA SEÇİM. pypdfium2 metni PDF'in çizim sırasına
    göre veriyor: iki sütunlu bir veri sayfasında sol ve sağ sütunun satırları
    iç içe geçiyor ("• Weight: 80 lbs chassis only safety" — soldaki ağırlık
    maddesi ile sağdaki güvenlik maddesi aynı satırda). `duzen` modülü
    koordinatlardan sütunları ayırıp düzeltiyor.

    Ama her sayfada daha iyi DEĞİL: ÖLÇÜLDÜ (Elektromanyetik Alanlar ders
    notu) — matematik ağırlıklı sayfalarda pdfplumber, karakter eşlemesi
    olmayan glifleri `(cid:126)` diye basıyor ve alt indisleri satırın sonuna
    atıyor; pypdfium2 aynı sayfada okunur metin veriyor. Bu yüzden karar
    sayfa sayfa veriliyor: düzen çıktısı ancak bozulma işareti taşımıyorsa ve
    metin miktarı ham çıktıya yakınsa kullanılıyor.
    """
    ham = _ham_metin(data)
    try:
        duzenli = duzen.sayfa_metinleri(data)
    except duzen.DuzenYok:
        return ham
    if len(duzenli) != len(ham):
        return ham
    return [d if _duzen_iyi(h, d) else h for h, d in zip(ham, duzenli)]


def _duzen_iyi(ham: str, duzenli: str) -> bool:
    if not duzenli.strip():
        return False
    if "(cid:" in duzenli:          # eşlenemeyen glif: bu sayfada pdfplumber kör
        return False
    if not ham.strip():
        return True
    oran = len(duzenli) / len(ham)
    return 0.8 <= oran <= 1.3       # ne belirgin kayıp ne de tekrar


def _ham_metin(data: bytes) -> list[str]:
    doc = _open(data)
    try:
        out = []
        for i in range(len(doc)):
            page = doc[i]
            try:
                textpage = page.get_textpage()
                try:
                    out.append(textpage.get_text_range() or "")
                finally:
                    textpage.close()
            finally:
                page.close()
        return out
    finally:
        doc.close()


def heading_lines(data: bytes) -> list[set[str]]:
    """Her sayfa için yazı ağırlığı gövdeden belirgin ağır olan satırlar.

    NEDEN AĞIRLIK, BOYUT DEĞİL. ÖLÇÜLDÜ (G9 veri sayfası): pdfium her satırın
    yazı boyunu 1.0 döndürüyor — PDF metni ölçekleme matrisiyle yazıyor ve
    fonksiyon matristen önceki değeri veriyor. Ağırlık ise kusursuz ayırıyor:
    gövde 300, bölüm başlıkları 400 ("Capacities", "Power", virgül içeren
    "Protocols, Interfaces, Interoperability" dahil). Dar kolondaki "Ribbon
    Communication's" gibi büyük harfle başlayan kısa satırlar 300 — metin
    kuralıyla bakılsa başlık sanılırdı.

    Eşik göreli: sayfanın baskın gövde ağırlığı + 100. Kalın için mutlak bir
    eşik (600) bu belgede hiçbir başlığı yakalamıyordu. Ağırlık bilgisi
    olmayan PDF'lerde (pdfium -1 döndürür) boş küme döner ve metin kuralları
    devrede kalır.
    """
    import pypdfium2.raw as raw

    doc = _open(data)
    try:
        out: list[set[str]] = []
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                satirlar: list[tuple[str, list[int]]] = []
                harfler: list[str] = []
                agirliklar: list[int] = []
                for k in range(textpage.count_chars()):
                    kod = raw.FPDFText_GetUnicode(textpage.raw, k)
                    ch = chr(kod) if 0 < kod < 0x110000 else ""
                    if ch in ("\r", "\n"):
                        if harfler:
                            satirlar.append(("".join(harfler), agirliklar))
                        harfler, agirliklar = [], []
                        continue
                    harfler.append(ch)
                    if ch.strip():
                        agirliklar.append(raw.FPDFText_GetFontWeight(textpage.raw, k))
                if harfler:
                    satirlar.append(("".join(harfler), agirliklar))
                out.append(_agir_satirlar(satirlar))
            finally:
                textpage.close()
                page.close()
        return out
    finally:
        doc.close()


def _agir_satirlar(satirlar: list[tuple[str, list[int]]]) -> set[str]:
    tum = [w for _, ws in satirlar for w in ws if w > 0]
    if not tum:
        return set()
    govde = Counter(tum).most_common(1)[0][0]
    bulunan = set()
    for metin, ws in satirlar:
        ws = [w for w in ws if w > 0]
        if not ws or statistics.median(ws) < govde + 100:
            continue
        temiz = " ".join(clean(metin).split())
        harf = sum(ch.isalpha() for ch in temiz)
        if (harf >= 3 and len(temiz.split()) <= 12
                and not temiz.endswith((".", ",", ";", ":"))
                and not temiz.startswith(("•", "-", "*", "–"))):
            bulunan.add(temiz)
    return bulunan


def needs_ocr(text: str) -> bool:
    """Bu sayfanın metin katmanı kullanılabilir mi, yoksa taranmış mı?

    Üç durum OCR'a gidiyor:
      * metin katmanı yok ya da neredeyse boş (klasik tarama)
      * harf oranı düşük: bozuk ToUnicode eşlemesi olan fontlar harf yerine
        sembol üretiyor — metin "var" görünüyor ama okunamaz
      * yerine koyma karakteri (�) yoğun: kodlama bozuk
    """
    min_chars = int(config.get("belge.min_chars_per_page", 60))
    compact = [ch for ch in text if not ch.isspace()]
    letters = sum(1 for ch in compact if ch.isalpha())
    if letters < min_chars:
        return True
    if letters / max(1, len(compact)) < 0.5:
        return True
    if text.count("�") / max(1, len(compact)) > 0.02:
        return True
    return False


def render_page(data: bytes, page_no: int, *, dpi: int | None = None):
    """Sayfanın görüntüsü (PIL.Image). `page_no` 1'den başlar."""
    dpi = dpi or int(config.get("belge.ocr_dpi", 200))
    doc = _open(data)
    try:
        page = doc[page_no - 1]
        try:
            return page.render(scale=dpi / 72).to_pil()
        finally:
            page.close()
    finally:
        doc.close()


# ------------------------------------------------------------ temizleme


def _repair_surrogates(text: str) -> str:
    """Eşleşmemiş UTF-16 surrogate'larını onarır veya atar.

    PDF çıkarımı bazı fontlarda yarım surrogate üretebiliyor. Python bunu
    string olarak tutuyor ama UTF-8'e çeviremiyor ve tokenizer
    `TextEncodeInput` hatasıyla reddediyor — Network Asistanı'nda tek bir
    belge 2 saatlik ingest'i çökertmişti.
    """
    if not any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        return text
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


# Metni bozan ama görünmeyen kontrol karakterleri (satır sonu ve sekme hariç)
_CONTROL = {chr(c) for c in range(32)} - {"\n", "\t", "\r"} | {"\x00"}


# Satır sonunda kelimeyi bölen görünmez tire işaretleri. U+00AD yumuşak tire;
# U+FFFE ise pypdfium2'nin aynı yeri döndürme biçimi. ÖLÇÜLDÜ (G9 veri
# sayfası): 12 kelime "plat\ufffeforms", "net\ufffework" diye bölünmüş geliyordu
# — kaynak panelinde tuhaf bir işaret olarak görünüyor ve embedding'e iki
# yarım kelime giriyordu.
_GORUNMEZ_TIRE = "\u00ad\ufffe"
_TIRE_BIRLESTIR = re.compile(rf"(\w)[{_GORUNMEZ_TIRE}][ \t]*\n?[ \t]*(\w)")


def clean(text: str) -> str:
    """Ligatürleri açar, bozuk kodlamayı onarır, boşluğu sıkıştırır."""
    text = _repair_surrogates(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TIRE_BIRLESTIR.sub(r"\1\2", text)
    text = text.translate({ord(ch): None for ch in _GORUNMEZ_TIRE})
    text = "".join(ch for ch in text if ch not in _CONTROL)
    # NFC, NFKC değil: NFKC Türkçede güvenli ama "½", "²" gibi karakterleri
    # "1/2", "2"ye açıyor ve tutarları bozuyor ("m²" -> "m2").
    text = unicodedata.normalize("NFC", text.translate(_LIGATURES))
    # Satır sonunda tire ile bölünmüş kelimeleri birleştir: "sözleş-\nme"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


# ------------------------------------------------------------ parçalama


def _heading(line: str) -> str | None:
    """Satır bir bölüm başlığıysa normalize edilmiş adını döner."""
    stripped = line.strip()
    if not (2 < len(stripped) < 90) or _NOT_A_SECTION.search(stripped):
        return None
    if stripped.endswith((",", ";")):
        return None
    words = stripped.split()
    if _MADDE.match(stripped) or _BOLUM.match(stripped):
        return " ".join(words)
    if len(words) > 10:
        return None
    numarali = _NUMARALI.match(stripped)
    if numarali and not stripped.endswith("."):
        # "3.2 Kapsam" başlık; "66.68 x 44.15 x 47.00 cm" değil. Numaradan
        # sonraki kısım harfle başlamalı ve rakam ağırlıklı olmamalı —
        # veri sayfasında ölçü satırı böyle başlık sanılıp boyut listesini
        # ikiye bölmüştü.
        kalan = numarali.group(2)
        rakam = sum(ch.isdigit() for ch in kalan) / max(1, len(kalan))
        if kalan[:1].isalpha() and rakam < 0.15:
            return " ".join(words)
        return None
    # Tamamı büyük harf kısa satır: "GENEL HÜKÜMLER". str.isupper() Türkçe
    # büyük harfleri (Ç Ğ İ Ö Ş Ü) doğru tanıyor; [A-Z] kalıbı tanımıyordu.
    #
    # Ama kısaltma listeleri de tamamen büyük harf: "MFR2, PRI, NFAS, TBCT"
    # ve "OC3 - 96,768; STM-1 - 90,720" veri sayfasında başlık sanılıp
    # kapasite listesini ortasından bölmüştü. Liste noktalaması ya da rakam
    # ağırlığı olan satır başlık değil.
    letters = [ch for ch in stripped if ch.isalpha()]
    rakam = sum(ch.isdigit() for ch in stripped) / max(1, len(stripped))
    if (len(letters) >= 4 and stripped.isupper() and len(words) <= 8
            and not any(ch in stripped for ch in ",;/") and rakam < 0.2):
        return " ".join(words)
    return None


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Kelime sınırında zorla böler — boyut tavanının son garantisi.

    PDF metninde cümle sonu noktası olmayan uzun bloklar sık (tablolar, madde
    listeleri). Bu güvenlik ağı olmadan böyle bir blok tek parça kalıyor ve
    embedding modelinin token sınırını aşıp sessizce kesiliyordu.
    """
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for word in text.split():
        if size + len(word) + 1 > max_chars and buf:
            pieces.append(" ".join(buf))
            buf, size = [], 0
        buf.append(word)
        size += len(word) + 1
    if buf:
        pieces.append(" ".join(buf))
    return pieces


def _overlap_tail(text: str, overlap_chars: int) -> str:
    """Örtüşme parçasını kelime sınırına yaslar (yarım kelime gürültü)."""
    if overlap_chars <= 0:
        return ""
    if len(text) <= overlap_chars:
        return text
    tail = text[-overlap_chars:]
    space = tail.find(" ")
    return tail[space + 1:] if space != -1 else tail


_SAYFA_NO = re.compile(r"^\W*(sayfa|page)?\s*\d{1,4}(\s*(/|of|-)\s*\d{1,4})?\W*$", re.IGNORECASE)
_KENAR = 3   # sayfanın başından ve sonundan bakılan satır sayısı


def _kenar_anahtari(satir: str) -> str:
    """Üst/alt bilgi karşılaştırması için satırın sayfa numarasız hâli.

    "2 Data Sheet" ile "3 Data Sheet" aynı anahtara inmeli. MADDE/BÖLÜM
    satırları muaf: sayfa başındaki "MADDE 4" ile "MADDE 7" rakamları atılınca
    aynı satır sayılıp silinirdi.
    """
    if _MADDE.match(satir) or _BOLUM.match(satir):
        return ""
    # Üst/alt bilgi kısa olur. Uzun satır sayılırsa iki sayfada aynı geçen bir
    # gövde paragrafı "tekrarlayan üst bilgi" sanılıp siliniyordu (testte
    # yakalandı).
    if len(satir) > 80 or len(satir.split()) > 10:
        return ""
    k = re.sub(r"^\s*(sayfa|page)?\s*\d{1,4}\s+|\s+\d{1,4}\s*$", "", satir, flags=re.IGNORECASE)
    k = " ".join(k.casefold().split())
    return k if sum(ch.isalpha() for ch in k) >= 3 else ""


def _tekrarlayan_kenarlar(pages: list[Page]) -> set[str]:
    """Birden çok sayfanın üstünde ya da altında tekrar eden satırlar.

    Sayfa üst bilgisi ("Converged Media Gateway", "2 Data Sheet") her
    sayfada tekrar ediyor; bırakılınca chunk'lara gürültü, numaralı olanı da
    bölüm başlığı olarak giriyordu. Yalnızca KENAR satırlarına bakılıyor:
    aynı ifade gövdede geçerse dokunulmuyor.
    """
    dolu = [p for p in pages if p.text.strip()]
    if len(dolu) < 2:
        return set()
    sayac: Counter = Counter()
    for p in dolu:
        satirlar = [ln.strip() for ln in clean(p.text).split("\n") if ln.strip()]
        kenar = satirlar[:_KENAR] + satirlar[-_KENAR:]
        sayac.update({a for a in (_kenar_anahtari(ln) for ln in kenar) if a})
    esik = max(2, len(dolu) // 2)
    return {a for a, n in sayac.items() if n >= esik}


def _units(pages: list[Page], max_chars: int) -> list[tuple[str, int, str | None]]:
    """Sayfaları (metin, sayfa_no, başlık) birimlerine ayırır.

    Sayfa numarası BİRİM düzeyinde taşınıyor: bir parça iki sayfanın sınırını
    aşabilir (paragraf sonraki sayfada devam eder) ve parçanın hangi sayfaları
    kapsadığı ancak böyle bilinebilir.
    """
    out: list[tuple[str, int, str | None]] = []
    tekrar = _tekrarlayan_kenarlar(pages)
    for page in pages:
        satirlar = [ln for ln in clean(page.text).split("\n") if ln.strip()]
        for sira, line in enumerate(satirlar):
            kenarda = sira < _KENAR or sira >= len(satirlar) - _KENAR
            if kenarda and (_SAYFA_NO.match(line) or _kenar_anahtari(line) in tekrar):
                continue
            yalin = " ".join(line.split())
            heading = yalin if yalin in page.headings else _heading(line)
            if heading is not None:
                out.append((line, page.page_no, heading))
                continue
            if len(line) <= max_chars:
                out.append((line, page.page_no, None))
                continue
            for sentence in re.split(r"(?<=[.!?])\s+", line):
                if len(sentence) <= max_chars:
                    out.append((sentence, page.page_no, None))
                else:
                    out.extend((p, page.page_no, None) for p in _hard_split(sentence, max_chars))
    return _etiket_dizileri(out)


_ETIKET_DIZISI = 3        # art arda en az bu kadar gövdesiz kısa başlık
_ETIKET_UZUNLUK = 40


def _etiket_dizileri(units: list[tuple[str, int, str | None]]) -> list[tuple[str, int, str | None]]:
    """Art arda gelen kısa "başlık" dizilerini şema/kutu etiketi olarak işaretler.

    ÖLÇÜLDÜ (Ribbon IMS, s. 3): şemadaki kalın etiketler ("Multi-Generation",
    "Wireless Voice", "2G/3G VoLTE", "Fixed Voice", "POTS") belgenin tamamı
    okunduğunda bile model tarafından açık bir destek beyanı sanıldı ve
    "IMS 2G/3G ve POTS'u destekliyor" dendi — oysa düz metin yalnızca 4G VoLTE,
    5G VoNR, VoWiFi ve VoBB sayıyor. Model hangi metnin etiket olduğunu
    bilmiyordu; parçalama biliyordu ama bu bilgiyi atıyordu.

    Aynı sayfada art arda en az üç kısa başlık satırı, arada gövde metni yoksa
    tek bir işaretli birime dönüşüyor. Yan kutu başlıkları (sayfa düzeninden
    ayrılıp metnin sonuna düşen "Why choose / Ribbon's IMS / solution?") da bu
    kalıba uyuyor ve artık sahte bir "solution?" bölümü açmıyor.
    """
    out: list[tuple[str, int, str | None]] = []
    i = 0
    while i < len(units):
        j = i
        while (j < len(units) and units[j][2] is not None and len(units[j][0]) <= _ETIKET_UZUNLUK
               and units[j][1] == units[i][1]):
            j += 1
        if j - i >= _ETIKET_DIZISI:
            etiketler = " · ".join(" ".join(u[0].split()) for u in units[i:j])
            out.append((f"[şema/kutu etiketleri: {etiketler}]", units[i][1], None))
            i = j
        else:
            out.append(units[i])
            i += 1
    return out


def chunk_pages(
    pages: list[Page],
    *,
    max_chars: int | None = None,
    overlap_chars: int | None = None,
    min_chars: int | None = None,
) -> list[Chunk]:
    """Sayfaları sayfa aralığını bilen parçalara ayırır.

    Bölüm sınırları korunuyor: yeni bir başlık yeni bir parça başlatıyor, çünkü
    "Madde 5 - Cezai Şart" ile "Madde 6 - Fesih" aynı parçaya girerse hangi
    hükmün hangi maddeye ait olduğu kayboluyor. Başlık da parçanın ilk satırı
    olarak kalıyor — "yüzde on" ifadesinin hangi maddede geçtiği metnin içinde
    görünsün.
    """
    max_chars = max_chars or int(config.get("chunking.chunk_chars", 2400))
    overlap_chars = (
        overlap_chars
        if overlap_chars is not None
        else int(config.get("chunking.chunk_overlap_chars", 300))
    )
    min_chars = min_chars or int(config.get("chunking.min_chunk_chars", 200))

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_pages: list[int] = []
    size = 0
    section = ""

    def flush(*, carry_overlap: bool) -> None:
        nonlocal buf, buf_pages, size
        text = " ".join(buf).strip()
        if not text:
            buf, buf_pages, size = [], [], 0
            return
        if len(text) < min_chars and chunks and chunks[-1].section == section:
            # Kırıntı parçayı AYNI bölümdeki öncekine ekle; farklı bölüme
            # eklemek tam da korumaya çalıştığımız sınırı bozardı.
            prev = chunks[-1]
            chunks[-1] = Chunk(prev.ordinal, prev.section, f"{prev.text} {text}",
                               prev.page_start, max(prev.page_end, buf_pages[-1]))
        else:
            chunks.append(Chunk(len(chunks), section, text, min(buf_pages), max(buf_pages)))
        if carry_overlap:
            tail = _overlap_tail(text, overlap_chars)
            buf = [tail] if tail else []
            buf_pages = [buf_pages[-1]] if tail else []
            size = len(tail)
        else:
            buf, buf_pages, size = [], [], 0

    units = _units(pages, max_chars)
    for text, page_no, heading in units:
        if heading is not None:
            flush(carry_overlap=False)   # bölüm sınırında örtüşme taşınmıyor
            section = heading
        elif size + len(text) > max_chars and buf:
            flush(carry_overlap=True)
        buf.append(text)
        buf_pages.append(page_no)
        size += len(text) + 1
    flush(carry_overlap=False)

    # KÜÇÜK CHUNK ATILMIYOR, KOMŞUSUNA EKLENİYOR.
    #
    # Eski süzgeç 40 karakterden kısa chunk'ları siliyordu. Tek başına anlamsız
    # kırıntılar için doğru görünüyordu ama gövdesiz başlık zinciriyle
    # birleşince şema içeriğinin tamamını indeksten düşürdü: model sonra
    # "POTS ifadesine hiç rastlanmadı" dedi, oysa belgede yazıyordu. Metin
    # kaybetmek, biraz karışık bir bölüm adından her zaman daha kötü.
    birlesik: list[Chunk] = []
    bekleyen = ""
    bekleyen_sayfa: int | None = None
    for c in chunks:
        if len(c.text) <= 40:
            if birlesik:
                p = birlesik[-1]
                birlesik[-1] = Chunk(p.ordinal, p.section, f"{p.text} {c.text}",
                                     p.page_start, max(p.page_end, c.page_end))
            else:
                bekleyen = f"{bekleyen} {c.text}".strip()
                bekleyen_sayfa = c.page_start if bekleyen_sayfa is None else bekleyen_sayfa
            continue
        if bekleyen:
            c = Chunk(c.ordinal, c.section, f"{bekleyen} {c.text}",
                      min(bekleyen_sayfa, c.page_start), c.page_end)
            bekleyen, bekleyen_sayfa = "", None
        birlesik.append(c)
    if bekleyen and not birlesik:
        birlesik.append(Chunk(0, "", bekleyen, bekleyen_sayfa or 1, bekleyen_sayfa or 1))
    birlesik = _kisa_bolumleri_birlestir(birlesik, min_chars, max_chars)
    return [Chunk(i, c.section, c.text, c.page_start, c.page_end) for i, c in enumerate(birlesik)]


def _bolum_adi(*adlar: str) -> str:
    """Birleşen parçaların bölüm adları. Kaynak panelinde "Power · Compliances"
    diye görünüyor: parça iki başlığın metnini de taşıyorsa ikisini de söylemek
    doğru. Zincirleme birleşmede ad şişmesin diye en fazla üç ad tutuluyor."""
    parcalar: list[str] = []
    for ad in adlar:
        for tek in (ad or "").split(" · "):
            tek = tek.strip(" …")
            if tek and tek not in parcalar:
                parcalar.append(tek)
    if len(parcalar) > 3:
        return " · ".join(parcalar[:3]) + " …"
    return " · ".join(parcalar)


def _kisa_bolumleri_birlestir(chunks: list[Chunk], min_chars: int, max_chars: int) -> list[Chunk]:
    """Kısa bölümleri komşularıyla birleştirir.

    Parçalama bölüm sınırında kesiyor: yeni başlık yeni parça. Sözleşmede
    doğru — MADDE 5 ile MADDE 6 karışmamalı. Ama her belge sözleşme gibi
    yazılmıyor: ÖLÇÜLDÜ (Elektromanyetik Alanlar ders notu, 52 sayfa) 89
    parçanın ortalaması 585 karakter, 18 tanesi 200'ün altında, bazıları
    yalnızca bir başlık satırı (41 karakter). Böyle bir belgede "8 parça
    getir" kuralı belgenin %9'unu okumak anlamına geliyordu.

    Birleştirme yalnızca KISA parçalar için ve üst sınırı aşmadan yapılıyor:
    dolu bir madde asla bir başkasıyla birleşmiyor, yani sözleşmelerdeki
    davranış değişmiyor. Bölüm adı ilk parçanınki kalıyor; birleşen parçanın
    başlığı zaten metnin ilk satırı olarak içeride duruyor.
    """
    out: list[Chunk] = []
    for c in chunks:
        if out:
            onceki = out[-1]  # noqa: F841 - okunurluk için ad verildi
            kisa = len(onceki.text) < min_chars or len(c.text) < min_chars
            sigar = len(onceki.text) + len(c.text) + 1 <= max_chars
            if kisa and sigar:
                out[-1] = Chunk(
                    onceki.ordinal,
                    _bolum_adi(onceki.section, c.section),
                    f"{onceki.text} {c.text}",
                    min(onceki.page_start, c.page_start),
                    max(onceki.page_end, c.page_end),
                )
                continue
        out.append(c)
    return out
