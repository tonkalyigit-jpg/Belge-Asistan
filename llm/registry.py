"""Kademe adı -> sağlayıcı örneği çözümlemesi.

Pipeline düğümleri `registry.tier("cheap")` / `registry.tier("strong")` der;
arkasında hangi sağlayıcının olduğunu bilmezler.
"""
from __future__ import annotations

from typing import Any

import config

from .base import LLMError, LLMProvider, TierConfig

_PROVIDERS: dict[str, Any] = {}
_INSTANCES: dict[str, LLMProvider] = {}


def _provider_class(name: str):
    if name in _PROVIDERS:
        return _PROVIDERS[name]

    if name == "anthropic":
        from .providers.anthropic_provider import AnthropicProvider

        cls = AnthropicProvider
    elif name == "openai_compat":
        from .providers.openai_compat import OpenAICompatProvider

        cls = OpenAICompatProvider
    else:
        raise LLMError(
            f"Bilinmeyen sağlayıcı: {name!r}. Desteklenenler: anthropic, openai_compat"
        )

    _PROVIDERS[name] = cls
    return cls


def tier_config(name: str) -> TierConfig:
    tiers = config.get("tiers", {})
    if name not in tiers:
        raise LLMError(
            f"`{name}` kademesi settings.yaml'da tanımlı değil. Mevcut: {list(tiers)}"
        )
    return TierConfig.from_dict(name, tiers[name])


def tier(name: str) -> LLMProvider:
    """Kademeyi çözer ve sağlayıcı örneğini süreç boyunca tekrar kullanır."""
    if name not in _INSTANCES:
        cfg = tier_config(name)
        _INSTANCES[name] = _provider_class(cfg.provider)(cfg)
    return _INSTANCES[name]


def reset() -> None:
    """Test/benchmark sırasında config değiştikten sonra örnekleri tazeler."""
    _INSTANCES.clear()


def describe() -> dict[str, dict[str, Any]]:
    """UI'da göstermek için aktif kademe özeti."""
    out: dict[str, dict[str, Any]] = {}
    for name in config.get("tiers", {}):
        cfg = tier_config(name)
        out[name] = {
            "provider": cfg.provider,
            "model": cfg.model,
            "price_in": cfg.price_in,
            "price_out": cfg.price_out,
        }
    return out
