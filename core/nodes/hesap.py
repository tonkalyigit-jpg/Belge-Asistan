"""Cevaptaki hesapları KODLA yeniden yapar.

Sözleşme sorularında ("40 gün gecikirse ceza kaç TL?") cevabın değeri bir
çarpımın sonucu ve dil modelleri çarpımda yanılabiliyor. Üstelik ölçümde model
hesabı hiç yapmayıp girdileri listelemekle kaldı. Prompt artık her adımı
`Hesap:` satırı olarak yazdırıyor; bu modül o satırları yeniden hesaplıyor ve
tutmayan bir sonuç varsa cevap düzeltme ipucuyla yeniden üretiliyor.

Model çıktısı `eval` ile çalıştırılmıyor: yalnızca sayıları ve dört işlemi
tanıyan küçük bir çözümleyici var. Metin veri, kod değil.

Sayı biçimi belirsizliği: "15,840" İngilizcede on beş bin sekiz yüz kırk,
Türkçede on beş virgül seksen dört. Kural: ayırıcıdan sonra TAM ÜÇ basamak
varsa ve tam kısım 0 değilse binlik; aksi hâlde ondalık. Belgelerimizdeki
bütün sayılar ("4.850.000", "15,840", "57,6", "‰3", "0,003") bu kuralla doğru
okunuyor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_SATIR = re.compile(r"^[\s>*_\-•]*hesap\s*[:：]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_TOKEN = re.compile(r"[‰%]?\d[\d.,]*[‰%]?|[×x*·/÷+\-−()]")


@dataclass
class HesapSonucu:
    satir: str
    beklenen: Decimal
    yazilan: Decimal

    @property
    def dogru(self) -> bool:
        """Yazılan sayının HASSASİYETİNDE karşılaştırma.

        Göreli tolerans (%0,5) büyük tutarlarda anlamsızdı: 6.480.000 yerine
        6.500.000 yazılmasını doğru sayıyordu — 20.000 TL fark (testte
        yakalandı). Artık "36,3 kg" yazıldıysa bir ondalığa yuvarlama, "582.000
        TL" yazıldıysa birler basamağı kabul ediliyor.
        """
        basamak = max(0, -self.yazilan.as_tuple().exponent)
        birim = Decimal(1).scaleb(-basamak)
        try:
            return abs(self.beklenen.quantize(birim) - self.yazilan) <= birim
        except Exception:
            return abs(self.beklenen - self.yazilan) <= birim


def sayi(metin: str) -> Decimal:
    """Türkçe ve İngilizce biçimli sayıyı çözer (bkz. modül açıklaması)."""
    t = metin.strip().rstrip(".,")
    if "," in t and "." in t:
        # İkisi de varsa sondaki ondalık ayırıcıdır: 1.234,5 / 1,234.5
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif t.count(".") > 1 or t.count(",") > 1:
        t = t.replace(".", "").replace(",", "")
    elif "." in t or "," in t:
        ayirici = "." if "." in t else ","
        tam, ondalik = t.split(ayirici)
        t = f"{tam}{ondalik}" if len(ondalik) == 3 and tam not in ("", "0") else f"{tam}.{ondalik}"
    return Decimal(t)


def _tokenler(ifade: str) -> list[str]:
    s = ifade.casefold()
    s = re.sub(r"binde\s*", "‰", s)
    s = re.sub(r"yüzde\s*", "%", s)
    s = re.sub(r"(?<=[\d\s])x(?=[\s\d])", "×", s)
    # Birimler ve açıklamalar (TL, gün, ay, "tavan") atılıyor; yalnızca sayı ve işlem kalıyor.
    s = re.sub(r"[^\d.,‰%×*·/÷+\-−()\s]", " ", s)
    # "485.000 TL (tavan)" -> birim atılınca boş "( )" kalıyordu ve çözümleyici
    # satırı "fazla token" diye atlıyordu: tavan kontrolü denetlenmiyordu.
    s = re.sub(r"\(\s*\)", " ", s)
    return _TOKEN.findall(s)


class _Cozumleyici:
    """expr := term (('+'|'-') term)* ; term := factor (('*'|'/') factor)* ;
    factor := '-' factor | sayı | '(' expr ')'"""

    def __init__(self, tokenler: list[str]):
        self.t = tokenler
        self.i = 0

    def _bak(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def _al(self):
        tok = self._bak()
        self.i += 1
        return tok

    def ifade(self) -> Decimal:
        deger = self.terim()
        while self._bak() in ("+", "-", "−"):
            deger = deger + self.terim() if self._al() == "+" else deger - self.terim()
        return deger

    def terim(self) -> Decimal:
        deger = self.carpan()
        while self._bak() in ("×", "x", "*", "·", "/", "÷"):
            islem = self._al()
            sag = self.carpan()
            deger = deger / sag if islem in ("/", "÷") else deger * sag
        return deger

    def carpan(self) -> Decimal:
        tok = self._al()
        if tok in ("-", "−"):
            return -self.carpan()
        if tok == "(":
            deger = self.ifade()
            if self._al() != ")":
                raise ValueError("parantez kapanmadı")
            return deger
        if tok is None or not re.search(r"\d", tok):
            raise ValueError(f"sayı bekleniyordu: {tok!r}")
        bolen = Decimal(1000) if "‰" in tok else Decimal(100) if "%" in tok else Decimal(1)
        return sayi(tok.strip("‰%")) / bolen


def _hesapla(ifade: str) -> tuple[Decimal, int]:
    """(değer, işlem sayısı). Çözülemezse ValueError."""
    tokenler = _tokenler(ifade)
    if not tokenler:
        raise ValueError("boş")
    c = _Cozumleyici(tokenler)
    try:
        deger = c.ifade()
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise ValueError(str(exc)) from exc
    if c.i != len(tokenler):
        raise ValueError("fazla token")
    islem = sum(1 for tok in tokenler if tok in "×x*·/÷+-−")
    return deger, islem


def denetle(cevap: str) -> list[HesapSonucu]:
    """Cevaptaki her `Hesap:` satırını yeniden hesaplar.

    "a × b = c" ve "a × b = c × d = e" zincirleri destekleniyor. Çözülemeyen
    satır (açıklama cümlesi, karşılaştırma) atlanıyor — yanlış alarm, kaçırılan
    bir hatadan daha kötü değil ama cevabı gereksiz yere yeniden ürettirir.
    """
    sonuclar = []
    for eslesme in _SATIR.finditer(cevap.replace("**", "")):
        satir = eslesme.group(1).strip()
        # Sonuçtan sonraki açıklama ("(tavan)", "→ uygulanır") hesaba dahil değil.
        parcalar = [p for p in re.split(r"=", re.split(r"→|=>|;", satir)[0]) if p.strip()]
        if len(parcalar) < 2:
            continue
        try:
            beklenen, islem = _hesapla(parcalar[0])
        except ValueError:
            continue
        if islem == 0:
            continue
        for sag in parcalar[1:]:
            try:
                yazilan, _ = _hesapla(sag)
            except ValueError:
                break
            sonuclar.append(HesapSonucu(satir, beklenen, yazilan))
            beklenen = yazilan if HesapSonucu(satir, beklenen, yazilan).dogru else beklenen
    return sonuclar


def kisa_cevap_eksik(cevap: str, sonuclar: list[HesapSonucu]) -> str:
    """Hesap yapıldıysa sonucunun Kısa cevap'ta yazması gerekiyor.

    ÖLÇÜLDÜ: "T1 ve E1 toplamı kaç?" sorusunda `Hesap: 15.840 + 19.800 = 35.640`
    doğru yazıldı ama Kısa cevap "doğrudan bir toplam verilmemiş" dedi. Yalnızca
    ilk satırı okuyan kişi cevabı hiç görmüyordu. Herhangi bir hesap sonucunun
    geçmesi yeterli sayılıyor — son satır her zaman nihai değer değil (tavan
    aşılmadıysa ceza, tavanın kendisi değil).
    """
    if not sonuclar:
        return ""
    ilk = cevap.replace("**", "").strip().split("\n\n", 1)[0]
    if "kısa cevap" not in ilk.casefold():
        return ""
    yazilanlar = set()
    for tok in _TOKEN.findall(ilk):
        if re.search(r"\d", tok) and tok not in "×x*·/÷+-−()":
            try:
                yazilanlar.add(sayi(tok.strip("‰%")).normalize())
            except (InvalidOperation, ValueError):
                continue
    if any(h.yazilan.normalize() in yazilanlar for h in sonuclar):
        return ""
    return (
        "The Kısa cevap does not state the calculated result. Rewrite it so its first "
        f"sentence gives the final value (one of: {', '.join(_yaz(h.yazilan) for h in sonuclar[:4])})."
    )


def _yaz(d: Decimal) -> str:
    d = d.quantize(Decimal("0.01")).normalize()
    tam, _, ondalik = f"{d:f}".partition(".")
    isaret = "-" if tam.startswith("-") else ""
    tam = tam.lstrip("-")
    gruplu = f"{int(tam):,}".replace(",", ".")
    return f"{isaret}{gruplu}" + (f",{ondalik}" if ondalik else "")


def ipucu(hatalar: list[HesapSonucu]) -> str:
    satirlar = "\n".join(
        f"- \"{h.satir}\": the result is {_yaz(h.beklenen)}, not {_yaz(h.yazilan)}"
        for h in hatalar[:5]
    )
    return (
        "These calculations are wrong (re-computed by software). Correct each "
        "result, and every value, cap check and conclusion that depends on it, "
        f"including the Kısa cevap:\n{satirlar}"
    )
