#!/usr/bin/env python3
"""Sağlayıcı bağlantısını ve model adlarını doğrular.

    python scripts/check_provider.py              # settings.yaml'daki kademeleri dene
    python scripts/check_provider.py --list       # sağlayıcının sunduğu modelleri listele
    python scripts/check_provider.py --list llama # sadece adında 'llama' geçenler

Neden gerekli: aynı model her sağlayıcıda FARKLI adla duruyor.

    Groq        llama-3.1-8b-instant
    DeepInfra   meta-llama/Meta-Llama-3.1-8B-Instruct
    Together    meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo
    OpenRouter  meta-llama/llama-3.1-8b-instruct

Yanlış ad 404 döner ve hata mesajı çoğu zaman açıklayıcı değildir. Bu script
sağlayıcıya sorup doğru adı sana söyler.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import config  # noqa: E402
from llm import registry  # noqa: E402
from llm.base import LLMError  # noqa: E402


def list_models(needle: str | None) -> int:
    """Sağlayıcının /models ucundan gerçek model adlarını çeker."""
    seen: set[str] = set()
    exit_code = 0

    for tier_name in config.get("tiers", {}):
        cfg = registry.tier_config(tier_name)
        if cfg.provider != "openai_compat" or not cfg.base_url:
            continue
        if cfg.base_url in seen:
            continue
        seen.add(cfg.base_url)

        key = config.env(cfg.api_key_env) if cfg.api_key_env else None
        headers = {"Authorization": f"Bearer {key}"} if key else {}

        print(f"\n{cfg.base_url}")
        if cfg.api_key_env and not key:
            print(f"  ! {cfg.api_key_env} tanımlı değil (.env dosyanı kontrol et)")

        try:
            resp = httpx.get(f"{cfg.base_url.rstrip('/')}/models", headers=headers, timeout=30)
            resp.raise_for_status()
            ids = sorted(m["id"] for m in resp.json().get("data", []))
        except Exception as exc:
            print(f"  ✗ listelenemedi: {exc}")
            exit_code = 1
            continue

        if needle:
            ids = [i for i in ids if needle.lower() in i.lower()]
        print(f"  {len(ids)} model:")
        for model_id in ids:
            print(f"    {model_id}")

    return exit_code


KNOWN_KEYS = {
    "GROQ_API_KEY": "https://console.groq.com",
    "DEEPINFRA_API_KEY": "https://deepinfra.com",
    "OPENROUTER_API_KEY": "https://openrouter.ai/keys",
    "TOGETHER_API_KEY": "https://api.together.ai/settings/api-keys",
    "ANTHROPIC_API_KEY": "https://console.anthropic.com",
}


def _diagnose_missing_key(expected: str) -> None:
    """Anahtar yok — ama neden? En sık üç sebebi ayırt et."""
    env_file = config.ROOT / ".env"

    if not env_file.exists():
        print(f"    .env dosyası yok. Oluştur:")
        print(f"      cp .env.example .env")
        print(f"    sonra içine yaz:  {expected}=<anahtarın>")
        return

    # .env var; içinde başka bir sağlayıcının anahtarı mı duruyor?
    present = {
        line.split("=", 1)[0].strip().removeprefix("export ").strip()
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if "=" in line
        and not line.strip().startswith("#")
        and line.split("=", 1)[1].strip()
    }

    if expected in present:
        print(f"    .env içinde {expected} var ama değeri boş görünüyor.")
        return

    filled = present & set(KNOWN_KEYS)
    if filled:
        other = sorted(filled)[0]
        print(f"    .env içinde {other} dolu ama settings.yaml {expected} istiyor.")
        print(f"    İki seçenek:")
        print(f"      a) settings.yaml'daki kademeyi {other}'a ait sağlayıcıya çevir")
        print(f"         (config/settings.yaml içindeki yorumlu profillere bak)")
        print(f"      b) .env'e {expected}=<anahtar> satırını ekle")
    else:
        print(f"    .env var ama {expected} satırı dolu değil.")
        url = KNOWN_KEYS.get(expected)
        if url:
            print(f"    Anahtarı buradan al: {url}")


def check_tiers() -> int:
    """Her kademeye tek cümlelik gerçek bir çağrı atar."""
    exit_code = 0

    for tier_name in config.get("tiers", {}):
        cfg = registry.tier_config(tier_name)
        print(f"\n[{tier_name}] {cfg.provider} · {cfg.model}")

        if cfg.api_key_env and not config.env(cfg.api_key_env):
            print(f"  ✗ {cfg.api_key_env} bulunamadı")
            _diagnose_missing_key(cfg.api_key_env)
            exit_code = 1
            continue

        try:
            provider = registry.tier(tier_name)
        except LLMError as exc:
            print(f"  ✗ kurulamadı: {exc}")
            exit_code = 1
            continue

        # 1) Düz metin çalışıyor mu?
        try:
            resp = provider.complete(
                system="You are a test endpoint. Reply with exactly: OK",
                user="Reply with OK.",
                max_tokens=16,
            )
        except LLMError as exc:
            print(f"  ✗ çağrı başarısız: {exc}")
            if "404" in str(exc):
                print(f"    -> model adı bu sağlayıcıda farklı olabilir;")
                print(f"       `python scripts/check_provider.py --list llama` dene")
            exit_code = 1
            continue

        print(
            f"  ✓ metin   {resp.latency_ms:6.0f} ms  "
            f"{resp.input_tokens}+{resp.output_tokens} tok  "
            f"${resp.cost_usd:.6f}  → {resp.text.strip()[:30]!r}"
        )

        # 2) JSON çalışıyor mu? Pipeline'ın 6 düğümü buna bağlı; küçük
        #    modellerde en sık kırılan yer burasıdır.
        try:
            data, json_resp = provider.complete_json(
                system=(
                    "Return a JSON object with keys `ok` (boolean true) and "
                    "`lang` (the two-letter code of the language of the user text)."
                ),
                user="BGP yönlendirme protokolü nedir?",
                max_tokens=100,
            )
            status = "✓" if data.get("ok") is True else "~"
            print(f"  {status} JSON    {json_resp.latency_ms:6.0f} ms  → {data}")
            if data.get("lang") not in ("tr", "TR"):
                print(f"    ! dil tespiti 'tr' değil ({data.get('lang')!r}) — "
                      f"Türkçe sınıflandırmada zorlanabilir")
        except Exception as exc:
            print(f"  ✗ JSON başarısız: {exc}")
            print("    -> bu kademe kontrol düzlemi düğümlerini çalıştıramaz")
            exit_code = 1

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Sağlayıcı ve model doğrulama")
    parser.add_argument(
        "--list", nargs="?", const="", metavar="FILTRE",
        help="çağrı yapmak yerine sağlayıcının model listesini göster",
    )
    args = parser.parse_args()

    if args.list is not None:
        return list_models(args.list or None)

    print("Yapılandırılmış kademeler deneniyor (her biri 2 küçük çağrı)…")
    code = check_tiers()
    print("\n" + ("✓ tüm kademeler çalışıyor" if code == 0 else "✗ bazı kademeler başarısız"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
