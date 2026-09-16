"""Arayüz sözlüğünün yapısal tuzakları.

Arayüz React'e taşındı ama test kalmalı: Streamlit sürümünde canlıda
`KeyError: 'helpful_up'` patlaması yaşandı — sözlüğün bir dilinde olup
diğerinde olmayan anahtar. TypeScript derleyicisi bunu yakalamıyor, çünkü
anahtarlar `T[dil]` üzerinden okunuyor ve iki nesne de `Record<string, string>`.

Testler kaynağı METİN olarak okuyor: Node çalıştırmak (npm, derleme) Python
test koşusuna bağımlılık eklerdi; oysa ölçmek istediğimiz şey iki sözlüğün
anahtar kümesi.
"""
from __future__ import annotations

import re
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
SOZLUK = KOK / "web" / "src" / "sozluk.ts"
KAYNAK = KOK / "web" / "src"


def _sozluk(dil: str) -> set[str]:
    metin = SOZLUK.read_text()
    bas = metin.index(f"  {dil}: {{")
    son = metin.index("\n  },", bas)
    return set(re.findall(r"^\s{4}(\w+):", metin[bas:son], re.MULTILINE))


def test_iki_dil_ayni_anahtarlari_tasiyor():
    tr, en = _sozluk("tr"), _sozluk("en")
    assert tr == en, f"tr-en farkı: {tr ^ en}"


def test_kullanilan_her_anahtar_tanimli():
    """`t.foo` yazılıp sözlükte olmayan anahtar ekranda `undefined` basıyor."""
    tanimli = _sozluk("tr")
    kullanilan: set[str] = set()
    for dosya in KAYNAK.rglob("*.tsx"):
        kullanilan |= set(re.findall(r"\bt\.(\w+)", dosya.read_text()))
    eksik = kullanilan - tanimli
    assert not eksik, f"sözlükte olmayan anahtarlar: {sorted(eksik)}"


def test_arayuzde_emoji_ikon_yok():
    """İkonlar Material Symbols; emoji her platformda farklı çiziliyor.

    Dingbat'ler (✓, ›, ▸) KAPSAM DIŞI: bunlar renkli emoji değil, metin
    kayıtlarıyla aynı kalemle çizilen tipografik işaretler ve Streamlit
    sürümünde de kullanılıyorlardı. Aranan şey ekranda renkli çıkan,
    platformdan platforma değişen emoji.
    """
    emoji = re.compile("[\U0001F300-\U0001FAFF\u23E9-\u23FA\u2600-\u26FF]")
    for dosya in list(KAYNAK.rglob("*.tsx")) + [SOZLUK]:
        bulunan = emoji.findall(dosya.read_text())
        assert not bulunan, f"{dosya.name}: emoji {bulunan[:3]}"
