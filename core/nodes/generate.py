"""Üret düğümü — tek belge ve çapraz belge soruları aynı kodu paylaşır.

Hangi kademenin kullanılacağını model_router state.tier_used'a yazdı; burada
doğru prompt seçilir ve atıf eşlemesi kurulur.
"""
from __future__ import annotations

import re

import config
from core.state import Category, QueryState, Route
from llm import registry
from llm.base import LLMError


def _group_by_document(state: QueryState) -> list[tuple[object, list]]:
    """Alakalı chunk'ları belgeye göre gruplar, ilk görülme sırasını korur.

    Atıf numarası CHUNK başına değil BELGE başına veriliyor. Chunk başına
    numaralandırıldığında aynı belgenin iki maddesi "[1][2]" olarak çıkıyor ve
    cevap iki bağımsız kaynağın aynı şeyi söylediği izlenimini veriyor —
    Network Asistanı'nda gözlemlenmişti. Chunk'lar belge içinde sayfa sırasına
    diziliyor; bir sözleşmeyi Madde 7'den Madde 3'e okumak modeli şaşırtıyor.
    """
    groups: dict[int, list] = {}
    order: list[int] = []
    for hit in state.graded_hits:
        if hit.document_id not in groups:
            groups[hit.document_id] = []
            order.append(hit.document_id)
        groups[hit.document_id].append(hit)
    for hits in groups.values():
        hits.sort(key=lambda h: (h.kind != "summary", h.page_start or 0, h.ordinal))
    return [(groups[key][0], groups[key]) for key in order]


def build_context(state: QueryState) -> str:
    blocks = []
    for n, (head, hits) in enumerate(_group_by_document(state), start=1):
        parts = [f"[{n}] {head.title}  (dosya: {head.filename})"]
        for hit in hits:
            parts.append(f"  — {hit.location}\n  {hit.text}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def build_citations(state: QueryState) -> list[dict]:
    """Atıf listesi — kullanılan chunk metinleri ve sayfaları dahil.

    Kullanıcı bir iddiayı doğrulamak istediğinde belge adı yetmiyor: nereden
    geldiğini ve o metnin ne dediğini görmesi lazım. Sayfa numarası da
    taşınıyor ki arayüz o sayfanın görüntüsünü açabilsin.
    """
    limit = int(config.get("retrieval.citation_snippet_chars", 1200))
    citations = []
    for n, (head, hits) in enumerate(_group_by_document(state), start=1):
        citations.append({
            "n": n,
            "kind": "belge",
            "document_id": head.document_id,
            "title": head.title,
            "filename": head.filename,
            "score": round(max(h.fused_score for h in hits), 3),
            "passages": [
                {
                    "section": hit.location,
                    "chunk_kind": hit.kind,
                    "page_start": hit.page_start,
                    "page_end": hit.page_end,
                    "text": hit.text[:limit],
                    "truncated": len(hit.text) > limit,
                }
                for hit in hits
            ],
        })
    return citations


def run(
    state: QueryState, *, feedback_hint: str = "", on_delta=None,
    temperature: float | None = None,
) -> QueryState:
    """Bağlamdan cevabı üretir. `feedback_hint` yeniden üretim turlarında dolu."""
    state.route = Route.RAG
    state.service_error = ""
    state.context_text = build_context(state)
    state.citations = build_citations(state)
    prompt_name = "capraz" if state.category == Category.CAPRAZ_BELGE else "generate"

    if not state.context_text.strip():
        state.note("Bağlam boş, üretim atlandı")
        state.answer = not_found_message(state.lang)
        state.grounded = True
        state.sufficient = False
        return state

    provider = registry.tier(state.tier_used)
    question = state.standalone_query or state.query

    user_parts = [config.lang_instruction(state.lang)]
    if state.history:
        # Yalnızca SON tur, kısaltılmış: amaç tekrar etmemek ve tonu sürdürmek.
        # Cevabın dayanağı kaynaklar olmalı, önceki cevap değil.
        last = state.history[-1]
        prev = " ".join((last.get("content") or "").split())[:600]
        if prev:
            user_parts.append(
                f"\nFor continuity only — your previous answer in this "
                f"conversation (do NOT cite it as a source, do not repeat it):\n{prev}"
            )
        if question != state.query:
            user_parts.append(
                f'\nThe user actually typed: "{state.query}" — answer the '
                f"resolved question below, in the same conversational register."
            )
    kapsam = ""
    if state.full_context:
        kapsam = ("\nThe sources below are the COMPLETE text of each document, not excerpts. "
                  "If a document does not address a point, you may say the document does not contain it.")
        if state.category == Category.BELGE_ICI:
            # Tek belge okunduğunda model diğer belgelerin varlığını bilmiyor ve
            # "yüklenen belgelerde bilgi yok" diyordu — tüm belgeler taranmış
            # gibi. Söylenecek olan "bu belgede yok".
            kapsam += (" Only the document(s) below were read — other uploaded documents "
                       "were NOT searched. Write \"bu belgede\" or name the document; never "
                       "\"yüklenen belgelerde\".")
    user_parts += [f"\nQuestion: {question}", f"{kapsam}\nSources:\n{state.context_text}"]
    if feedback_hint:
        user_parts.append(
            f"\nYour previous draft was rejected. Fix exactly this and rewrite "
            f"the full answer:\n{feedback_hint}"
        )

    try:
        # Akış yalnızca İLK turda: yeniden üretim turunda ekranda zaten bir
        # taslak duruyor ve onu karakter karakter ikinci kez yazmak, cevabın
        # değiştiğini gizler.
        resp = provider.stream(
            system=config.prompt(prompt_name),
            user="\n".join(user_parts),
            cache_system=True,
            on_delta=on_delta if not feedback_hint else None,
            temperature=temperature,
        )
    except LLMError as exc:
        state.note(f"Üretim başarısız ({state.tier_used}): {exc}")
        state.service_error = str(exc)
        state.answer = service_message(state.lang, str(exc))
        return state

    state.add_cost(resp)
    state.model_used = resp.model
    state.answer = _atifsiz(_kisa_cevap_basa(resp.text.strip()))
    return state


# `[1]`, `[2][3]`, `([1])`, `[1], [2]` — modelin cümle sonuna koyduğu kaynak
# numaraları. Markdown bağlantısına (`[metin](url)`) dokunmaması için yalnızca
# içi RAKAM olan köşeli parantezler eşleşiyor.
# Satır sonu EŞLEŞMEYE GİRMİYOR: `\s` yazıldığında "…DS0 [1]\nİkinci satır"
# ifadesindeki satır sonu da yutuluyor ve iki madde tek satıra biniyordu.
_ATIF = re.compile(r"[ \t]*\(?[ \t]*(?:\[\d+\][ \t,]*)+\)?")


def _atifsiz(answer: str) -> str:
    """Cevaptan kaynak numaralarını siler.

    Prompt zaten yazmamasını söylüyor; bu ağ, talimatın tutmadığı koşular için.
    Kaynağın nerede olduğu cümlenin kendisinde yazıyor ("Madde 5'e göre",
    "s. 3") ve belgeler cevabın altındaki kaynak panelinde listeleniyor;
    numaranın metinde bir karşılığı yok.
    """
    def _yaz(eslesme: "re.Match[str]") -> str:
        # Noktalama ve kelime arasında kalan boşluk korunuyor: "cezadır [1]."
        # -> "cezadır." ama "…yüzde 25 [2] tavanı" -> "…yüzde 25 tavanı".
        sonraki = answer[eslesme.end():eslesme.end() + 1]
        return "" if sonraki in {"", ".", ",", ";", ":", ")", "!", "?", "\n"} else " "

    temiz = _ATIF.sub(_yaz, answer)
    # Tablo hücresinde atıf tek başına kaldıysa hücre boş kalmasın.
    temiz = re.sub(r"\|\s{2,}\|", "| — |", temiz)
    return temiz.strip()


def _kisa_cevap_basa(answer: str) -> str:
    """Sonda yazılmış "Kısa cevap" paragrafını başa taşır.

    Çapraz prompt'u sonucu BİLEREK en sona yazdırıyor: kısa cevap tablodan önce
    yazıldığında model sonuca kanıtı kurmadan karar veriyordu ve aynı soruya
    iki koşuda iki farklı sonuç geldi (biri gizlilik ihlalini atladı). Okuyan
    kişi yine önce sonucu görmeli; sıra yalnızca üretimde tersine.
    """
    isaret = "**Kısa cevap:**"
    konum = answer.rfind(isaret)
    if konum <= 0:
        return answer
    kisa = answer[konum:].strip()
    govde = answer[:konum].strip()
    # Model talimata rağmen başa da bir kısa cevap yazabiliyor (arayüzde iki
    # kez görüldü). Sondaki kanıttan SONRA yazıldığı için o kalıyor; baştakinin
    # paragrafı atılıyor.
    if govde.startswith(isaret):
        govde = govde.split("\n\n", 1)[1].strip() if "\n\n" in govde else ""
    # Modelin yine de ürettiği satır sonu etiketleri ekranda çıplak görünüyor.
    return re.sub(r"\s*<br\s*/?>\s*", " · ", f"{kisa}\n\n{govde}".strip())


def not_found_message(lang: str, *, scope_titles: list[str] | None = None,
                      searched_titles: list[str] | None = None) -> str:
    if scope_titles and lang == "tr":
        # Odaklıyken "bulamadım" demek yetmiyor: cevap başka bir belgede olabilir
        # ve kullanıcı aramanın yalnızca bir belgeyle sınırlı olduğunu unutmuş
        # olabilir.
        return (
            f"Şu an yalnızca **{', '.join(scope_titles)}** içinde arıyorum ve bu "
            "belgede soruyu cevaplayan bir bölüm bulamadım. Cevap başka bir belgede "
            "olabilir: odağı kaldırıp tüm belgelerde yeniden sorabilirsiniz."
        )
    if scope_titles:
        return (
            f"I am searching only in **{', '.join(scope_titles)}** and found no passage "
            "that answers this. Remove the focus to search all documents."
        )
    if searched_titles and lang == "tr":
        # Soru belirli bir belgeyi andıysa hangisinde arandığı söylenmeli:
        # "yüklenen belgelerde bulamadım" tüm belgelerin tarandığını ima ediyor.
        return (
            f"**{', '.join(searched_titles)}** içinde bu soruyu cevaplayan bir bölüm "
            "bulamadım."
        )
    if lang == "tr":
        return (
            "Yüklenen belgelerde bu soruyu cevaplayan bir bölüm bulamadım. "
            "Soru başka bir belgeyle ilgiliyse o belgeyi yükleyebilir, ya da "
            "belgede geçen bir terimle (madde adı, taraf adı, tarih) yeniden "
            "sorabilirsiniz."
        )
    return (
        "I could not find a passage in the uploaded documents that answers this. "
        "If it concerns another document, upload it, or ask again using a term that "
        "appears in the document (a clause title, a party name, a date)."
    )


def service_message(lang: str, hata: str) -> str:
    """Model servisi cevap vermediğinde kullanıcıya dürüst mesaj.

    Eskiden genel bir "hata oluştu" metni cevap yerine geçiyor ve denetim
    döngüsüne giriyordu: yeterlilik denetçisi hata metnini "yetersiz" bulup
    yeniden üretim istiyor, o da kotaya takılıyordu.
    """
    kota = "429" in hata or "quota" in hata.lower()
    if lang == "tr":
        return (
            "Model servisi şu an kota sınırında (ücretsiz katman), bu soru "
            "cevaplanamadı. Birkaç saniye sonra aynı soruyu tekrar sorabilirsiniz."
            if kota else
            "Model servisine şu an ulaşılamadı, bu soru cevaplanamadı. Lütfen tekrar deneyin."
        )
    return (
        "The model service is at its quota limit right now. Please ask again in a few seconds."
        if kota else "The model service could not be reached. Please try again."
    )


def terim_ipucu(state: QueryState) -> str:
    """Bulunamayan sorunun terimi BAŞKA bir belgede geçiyorsa bunu söyler.

    Çıplak "bulamadım" yanıltıcı olabiliyor: "G9'u AWS'ye kurabilir miyim?"
    sorusunda AWS G9 belgesinde yok ama IMS çözüm özetinde var. Bu bilgi
    deterministik: metinde terim araması, model çağrısı yok, uydurma riski yok.

    Yalnızca soru belirli belgeleri andığında ya da sohbet bir belgeye
    odaklıyken çalışıyor — hedef belge bilinmeden "şurada geçiyor" demek gürültü.
    """
    import re as _re

    from belge.store import _tokenize
    from core import db

    hedef = set(state.scope_document_ids or state.document_ids)
    if not hedef:
        return ""

    # YALNIZCA ÖZEL AD VE KODLAR: büyük harfle başlayan ya da rakam içeren
    # kelimeler (AWS, 5G, VoNR, Kubernetes, Antalya). Sınıflandırıcının anahtar
    # kelimeleri kullanılmıyor: ölçümde "kurulum" ve "sözleşme" gibi genel
    # kelimeler "G9'da geçmiyor, Kuzey sözleşmesinde geçiyor" diye gürültü üretti.
    # Türkçe ek atılıyor: "AWS'ye" -> "AWS".
    terimler: list[str] = []
    for kelime in _re.findall(r"[\w/-]+(?:['’]\w+)?", state.query):
        kok = _re.split(r"['’]", kelime)[0]
        # Salt rakam ("400") ipucu olamaz: belge "400M+" yazıyordu ve Güney'deki
        # "400 adet monitör" ile eşleşip yanlış bir "şurada geçiyor" üretti.
        if kok.isdigit():
            continue
        if len(kok) >= 2 and (kok[0].isupper() or any(ch.isdigit() for ch in kok)):
            if kok.casefold() not in (t.casefold() for t in terimler):
                terimler.append(kok)

    satirlar = db.connect().execute(
        """SELECT c.document_id, c.page_start, c.text, d.title
           FROM chunks c JOIN documents d ON d.id = c.document_id
           WHERE c.deleted = 0 AND c.kind = 'body' AND d.status = 'ready'"""
    ).fetchall()
    tokenli = [(r["document_id"], r["page_start"], set(_tokenize(r["text"])), r["title"]) for r in satirlar]
    hedef_adlari = sorted({t for d, _, _, t in tokenli if d in hedef})

    notlar = []
    for terim in terimler:
        parca = _tokenize(terim)
        if not parca:
            continue
        gecen = [(d, p, t) for d, p, toks, t in tokenli if all(x in toks for x in parca)]
        if any(d in hedef for d, _, _ in gecen) or not gecen:
            continue
        yerler: dict[str, set] = {}
        for _, p, t in gecen:
            yerler.setdefault(t, set()).add(p)
        konum = "; ".join(
            f"{t} (s. {', '.join(str(x) for x in sorted(v) if x)})" for t, v in sorted(yerler.items())
        )
        notlar.append(f"**{terim}** {', '.join(hedef_adlari)} içinde geçmiyor; şurada geçiyor: {konum}.")
    if not notlar:
        return ""
    return "\n\n" + "\n".join(f"- {n}" for n in notlar)


def _error_message(lang: str) -> str:
    if lang == "tr":
        return "Yanıt üretilirken bir hata oluştu. Lütfen tekrar deneyin."
    return "An error occurred while generating the answer. Please try again."
