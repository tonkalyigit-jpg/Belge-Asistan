"""Yapılandırma yükleyici — settings.yaml tek kaynak."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = ROOT / "config" / "settings.yaml"
_ENV_PATH = ROOT / ".env"

_cache: dict[str, Any] | None = None


def _load_dotenv() -> None:
    """Proje kökündeki .env dosyasını ortama yükler.

    Gerçek ortam değişkenleri .env'i EZER — kabuğunda `export GROQ_API_KEY=...`
    yaptıysan o kazanır, dosyadaki eski değer sessizce devralmaz.

    Küçük bir elle yazılmış ayrıştırıcı; `python-dotenv` bağımlılığı eklemeye
    değmeyecek kadar basit bir iş.
    """
    if not _ENV_PATH.exists():
        return
    for raw_line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()

        if value[:1] in ("'", '"'):
            # Tırnaklı: kapanış tırnağına kadar al, sonrasını (yorum) at.
            quote = value[0]
            closing = value.find(quote, 1)
            value = value[1:closing] if closing != -1 else value[1:]
        elif " #" in value:
            # Tırnaksız: satır sonu yorumunu at.
            value = value.split(" #", 1)[0].strip()

        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def load(reload: bool = False) -> dict[str, Any]:
    """settings.yaml'ı okur ve süreç boyunca bellekte tutar."""
    global _cache
    if _cache is None or reload:
        with open(_SETTINGS_PATH, encoding="utf-8") as fh:
            _cache = yaml.safe_load(fh)
    return _cache


def get(path: str, default: Any = None) -> Any:
    """Noktalı yol ile ayar okur: get("limits.max_rewrites")."""
    node: Any = load()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def abs_path(setting_path: str) -> Path:
    """paths.* altındaki göreli yolu proje köküne göre mutlaklaştırır."""
    rel = get(setting_path)
    if rel is None:
        raise KeyError(f"Ayar bulunamadı: {setting_path}")
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


_prompt_cache: dict[str, str] = {}


def prompt(name: str) -> str:
    """config/prompts/<name>.md okur.

    Prompt'lar tek dilde (İngilizce) tutulur ve çıktı dili çalışma anında
    talimatla verilir. Dil başına ayrı sistem prompt'u tutmak prompt-cache
    prefix'ini ikiye böler ve isabet oranını yarıya düşürürdü.
    """
    if name not in _prompt_cache:
        p = ROOT / "config" / "prompts" / f"{name}.md"
        if not p.exists():
            raise FileNotFoundError(f"Prompt bulunamadı: {p}")
        _prompt_cache[name] = p.read_text(encoding="utf-8")
    return _prompt_cache[name]


LANG_NAMES = {"tr": "Turkish", "en": "English"}

def lang_instruction(lang: str) -> str:
    """Çıktı dilini zorlayan talimat bloğu.

    Network Asistanı'ndaki ağ terimleri sözlüğü burada yok: orada İngilizce
    literatürden Türkçe cevap yazılıyordu ve modeller terimleri kelime kelime
    çeviriyordu. Burada kaynak belge zaten Türkçe; doğru terim belgenin
    kendisinde yazıyor. Tek kural onu değiştirmemek.
    """
    if lang != "tr":
        return (
            f"Write your entire response in {LANG_NAMES.get(lang, 'English')}. "
            "Keep document titles, names, numbers, dates and quoted clauses exactly "
            "as they appear in the documents."
        )
    return (
        "Write your entire response in Turkish.\n"
        "Keep these exactly as they appear in the documents — never paraphrase or "
        "re-spell them: proper names, company and institution names, document titles, "
        "article/clause numbers (Madde 5, 3.2), dates, amounts and currencies, "
        "reference and contract numbers.\n"
        "Use the document's own terminology rather than synonyms."
    )


def env(var_name: str, default: str | None = None) -> str | None:
    return os.environ.get(var_name, default)
