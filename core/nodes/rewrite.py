"""Sorguyu Yeniden Yaz düğümü — ucuz kademe.

Soru belgede geçmeyen kelimelerle sorulduğunda ("ne kadar ceza var") arama
belgedeki terimi ("cezai şart") kaçırabiliyor; bu düğüm sorguyu belgenin
diline yaklaştırıyor.

Döngü sayacı burada değil `core/graph.py`'de tutulur; bu düğüm yalnızca
denenmiş sorguları görüp yeni bir tane üretmekten sorumlu.
"""
from __future__ import annotations

import config
from core.state import QueryState
from llm import registry
from llm.base import LLMError


def run(state: QueryState, tried: list[str]) -> QueryState:
    provider = registry.tier("cheap")

    tried_block = "\n".join(f"- {q}" for q in tried) or "- (none)"
    user = (
        f"Original user question: {state.query}\n"
        f"Queries already tried (do not repeat these):\n{tried_block}\n"
        f"Attempt number: {state.rewrites + 1}"
    )

    try:
        data, resp = provider.complete_json(
            system=config.prompt("rewrite"),
            user=user,
            max_tokens=200,
            cache_system=True,
        )
    except LLMError as exc:
        state.note(f"Sorgu yeniden yazma başarısız: {exc}")
        return state

    state.add_cost(resp)

    new_query = str(data.get("search_query") or "").strip()
    if not new_query or new_query.lower() in {q.lower() for q in tried}:
        state.note("Yeniden yazma yeni bir sorgu üretemedi")
        return state

    state.rewrites += 1
    state.search_query = new_query
    state.note(
        f"Sorgu yeniden yazıldı ({data.get('strategy', '?')}): '{new_query}'"
    )
    return state
