"""Üretim gerektirmeyen ya da çok ucuz iki yol: Kibar Ret ve Belge Özeti.

Belge özeti yükleme anında üretilip saklandığı için "şu belgeyi özetle"
sorusunun cevabı SIFIR token ve anında dönüyor — belgeyi yeniden okumaya gerek
yok. Yükleme sırasında bir kez ödenen bedelin kullanıcıya dönüşü burası.
"""
from __future__ import annotations

import config
from core.state import QueryState, Route
from llm import registry
from llm.base import LLMError

from .classifier import hazir_belgeler


def refuse(state: QueryState) -> QueryState:
    """Kibar Ret — belgelerle ilgisiz sorular ve selamlaşma."""
    state.route = Route.REFUSAL
    state.tier_used = "writer"
    belgeler = hazir_belgeler()
    liste = "\n".join(f"- {b['title'] or b['filename']}" for b in belgeler[:12]) or "(none)"

    try:
        resp = registry.tier("writer").complete(
            system=config.prompt("refusal"),
            user=(
                f"{config.lang_instruction(state.lang)}\n\n"
                f"Uploaded documents ({len(belgeler)}):\n{liste}\n\n"
                f"User message: {state.query}"
            ),
            max_tokens=300,
            cache_system=True,
        )
    except LLMError as exc:
        state.note(f"Ret üretimi başarısız: {exc}")
        state.answer = _fallback_refusal(state.lang, bool(belgeler))
        return state

    state.add_cost(resp)
    state.model_used = resp.model
    state.answer = resp.text.strip() or _fallback_refusal(state.lang, bool(belgeler))
    # Ret'te doğrulama anlamsız — kaynak yok, kontrol edilecek iddia yok.
    state.grounded = True
    state.sufficient = True
    return state


def summary(state: QueryState) -> QueryState:
    """Saklı belge özetini döner. LLM çağrısı yok."""
    state.route = Route.OZET
    state.tier_used = ""
    belgeler = {b["id"]: b for b in hazir_belgeler()}

    if not belgeler:
        state.answer = no_documents_message(state.lang)
        state.sufficient = False
        return state

    if state.document_ids:
        secilen = [belgeler[i] for i in state.document_ids if i in belgeler]
    elif len(belgeler) == 1:
        secilen = list(belgeler.values())
    else:
        secilen = []

    if secilen:
        # İstenen belge(ler)in tam özeti.
        parcalar = []
        for b in secilen:
            govde = _govde(b.get("summary") or "")
            parcalar.append(f"### {b['title'] or b['filename']}\n\n{govde or '_Bu belge için özet üretilemedi._'}")
        state.answer = "\n\n".join(parcalar)
    else:
        # Belge adı verilmeden "belgeleri özetle / neler yüklü": liste ve her
        # belgenin ilk paragrafı. Hepsinin tam özetini art arda dökmek, soran
        # kişinin aradığını bulmasını zorlaştırıyor.
        satirlar = [f"**{len(belgeler)} belge yüklü:**" if state.lang == "tr"
                    else f"**{len(belgeler)} documents uploaded:**", ""]
        for b in belgeler.values():
            ilk = _govde(b.get("summary") or "").split("\n\n", 1)[0].strip()
            satirlar.append(f"- **{b['title'] or b['filename']}** — {ilk}" if ilk
                            else f"- **{b['title'] or b['filename']}**")
        satirlar += ["", "Birinin tam özetini görmek için adını yazabilirsiniz."
                     if state.lang == "tr" else "Name one to see its full summary."]
        state.answer = "\n".join(satirlar)
        secilen = list(belgeler.values())

    state.citations = [
        {
            "n": n, "kind": "belge", "document_id": b["id"],
            "title": b["title"] or b["filename"], "filename": b["filename"],
            "score": 1.0, "passages": [],
        }
        for n, b in enumerate(secilen, start=1)
    ]
    state.document_ids = [b["id"] for b in secilen]
    if state.lang != "tr":
        state.note("Özet belgenin dilinde (Türkçe) saklı; çevrilmeden gösterildi")
    state.grounded = True
    state.sufficient = True
    return state


def _govde(kayit: str) -> str:
    """Saklı özet 'Tür: ...' satırıyla başlıyor; gösterimde o satır atılıyor."""
    if kayit.startswith("Tür:"):
        return kayit.split("\n", 1)[1].strip() if "\n" in kayit else ""
    return kayit.strip()


def no_documents_message(lang: str) -> str:
    if lang == "tr":
        return (
            "Henüz yüklenmiş bir belge yok. Sol panelden PDF yükleyin — dijital "
            "PDF'ler ve taranmış belgeler okunabiliyor. Yükleme bittiğinde belgenin "
            "özetini gösterip sorularınızı sayfa numarasıyla cevaplayacağım."
        )
    return (
        "No documents have been uploaded yet. Upload a PDF from the left panel — "
        "digital and scanned PDFs both work."
    )


def _fallback_refusal(lang: str, has_documents: bool) -> str:
    if lang == "tr":
        return (
            "Ben yalnızca yüklediğiniz belgeler üzerinden cevap veriyorum — bir "
            "belgenin özetini çıkarabilir, içindeki bir maddeyi bulabilir ya da "
            "belgeleri karşılaştırabilirim."
            + ("" if has_documents else " Başlamak için sol panelden bir PDF yükleyin.")
        )
    return (
        "I only answer from the documents you upload — I can summarise a document, "
        "find a clause, or compare documents."
        + ("" if has_documents else " Upload a PDF from the left panel to start.")
    )
