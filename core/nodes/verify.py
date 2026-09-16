"""İki doğrulama düğümü: "Halüsinasyon var mı?" ve "Yanıt yeterli mi?".

İkisi de ucuz kademede çalışır ve JSON döner. Başarısız olurlarsa graph.py
`max_regen` bütçesi dahilinde üretimi tekrarlar; bütçe dolduğunda cevap
"düşük güven" etiketiyle yine de kullanıcıya verilir — sessizce yutulmaz.
"""
from __future__ import annotations

import config
from core.state import QueryState
from llm import registry
from llm.base import LLMError


def check_hallucination(state: QueryState) -> QueryState:
    """Cevabın kaynaklarla desteklenip desteklenmediğini denetler."""
    if not state.context_text.strip() or not state.answer.strip():
        state.grounded = True
        return state

    provider = registry.tier("cheap")
    try:
        data, resp = provider.complete_json(
            system=config.prompt("hallucination"),
            user=(
                ("The sources below are the COMPLETE text of each document.\n"
                 if state.full_context else "")
                + f"Sources:\n{state.context_text}\n\n"
                f"---\nDraft answer:\n{state.answer}"
            ),
            max_tokens=600,
            cache_system=True,
        )
    except LLMError as exc:
        # Denetleyici çökerse cevabı reddetmek yanlış olur; geçir ama işaretle.
        # İşaret rozete de yansımalı: denetlenmemiş cevap "doğrulandı" gibi
        # görünmemeli.
        state.note(f"Halüsinasyon kontrolü çalıştırılamadı: {exc}")
        state.grounded = True
        state.low_confidence = True
        return state

    state.add_cost(resp)

    state.grounded = bool(data.get("grounded", True))
    unsupported = [str(x) for x in (data.get("unsupported") or []) if str(x).strip()]
    bad_citations = [str(x) for x in (data.get("bad_citations") or []) if str(x).strip()]
    state.unsupported_claims = unsupported + bad_citations

    if not state.grounded:
        state.note(f"Halüsinasyon tespit edildi: {len(state.unsupported_claims)} iddia")

    return state


def hallucination_hint(state: QueryState) -> str:
    items = "\n".join(f"- {claim}" for claim in state.unsupported_claims[:6])
    return (
        "These statements are not supported by the sources. Remove them, or replace "
        f"them with what the sources actually say:\n{items}"
    )


def check_sufficiency(state: QueryState) -> QueryState:
    """Cevabın gerçekten sorulan soruyu yanıtlayıp yanıtlamadığını denetler."""
    if not state.answer.strip():
        state.sufficient = False
        return state

    provider = registry.tier("cheap")
    try:
        data, resp = provider.complete_json(
            system=config.prompt("sufficiency"),
            # Puanlayıcıdaki ile aynı gerekçe: "daha detaylı anlat" cümlesine
            # bakarak cevabın soruyu karşılayıp karşılamadığına karar verilemez.
            user=(f"Question: {state.standalone_query or state.query}"
                  f"\n\n---\nAnswer:\n{state.answer}"),
            max_tokens=400,
            cache_system=True,
        )
    except LLMError as exc:
        state.note(f"Yeterlilik kontrolü çalıştırılamadı: {exc}")
        state.sufficient = True
        state.low_confidence = True
        return state

    state.add_cost(resp)

    state.sufficient = bool(data.get("sufficient", True))
    state._sufficiency_hint = str(data.get("hint") or "")  # type: ignore[attr-defined]
    missing = [str(x) for x in (data.get("missing") or []) if str(x).strip()]
    state._sufficiency_missing = missing  # type: ignore[attr-defined]

    if not state.sufficient:
        state.note(
            f"Taslak yetersiz bulundu: {'; '.join(missing[:3]) or 'belirtilmedi'}"
        )

    return state


def sufficiency_hint(state: QueryState) -> str:
    hint = getattr(state, "_sufficiency_hint", "")
    missing = getattr(state, "_sufficiency_missing", [])
    parts = []
    if missing:
        parts.append(
            "The answer does not address these parts of the question:\n"
            + "\n".join(f"- {m}" for m in missing[:6])
        )
    if hint:
        parts.append(hint)
    return "\n".join(parts) or "Answer the question more directly and completely."
