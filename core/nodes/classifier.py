"""LLM Sınıflandırıcı düğümü.

Tek ucuz çağrıda: kategori, zorluk, dil, kendi başına anlaşılır soru, arama
sorgusu ve sorunun andığı belgeler. Ayrı çağrılar dört kat maliyet demekti;
üstelik bu kararlar aynı bağlamdan çıkıyor.

Network Asistanı'ndan farkı: İngilizce sorgu üretimi yok (belgeler Türkçe) ve
sınıflandırıcı yüklü belgelerin listesini görüyor. Soru bir belgeyi adıyla
anıyorsa ("Güney sözleşmesi") kimliğini doğrudan döndürüyor; ayrı bir bulanık
ad eşleştirmesi yazmak yerine bu çözümü zaten dili anlayan modele bırakıyoruz.
"""
from __future__ import annotations

import config
from core import db
from core.state import Category, QueryState
from llm import registry
from llm.base import LLMError
from memory import feedback

_VALID_CATEGORIES = {c.value for c in Category}

# Son iki tur (soru + cevap) takip sorularının neredeyse tamamını çözmeye
# yetiyor; daha uzun pencere çağrıyı pahalılaştırıp dikkati dağıtıyor.
_HISTORY_TURNS = 4
_HISTORY_CHARS = 700
# Belge listesi prompt'a giriyor. Çok belgede liste uzar; o durumda ada göre
# eşleştirme zaten güvenilmez hâle gelir ve arama belge adını BM25'le bulur.
_MAX_LISTED_DOCUMENTS = 60


def hazir_belgeler() -> list[dict]:
    rows = db.connect().execute(
        "SELECT id, title, filename, summary FROM documents WHERE status = 'ready' ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


# 180 DEĞİL: özetlerin ilk cümlesi "Bu belge, X tarafından yayımlanan…" diye
# başlayan künye oluyor ve belgenin NEYİ KAPSADIĞINI söyleyen cümle ikinci
# sırada. 180 karakterde ders notunun "vektörel büyüklükler, koordinat
# sistemleri…" cümlesi kesiliyordu ve soru yine kapsam dışı sayıldı.
_SUMMARY_CHARS = 320


def _belge_listesi(belgeler: list[dict]) -> str:
    if not belgeler:
        return "Uploaded documents: none yet.\n\n"
    satirlar = []
    for b in belgeler[:_MAX_LISTED_DOCUMENTS]:
        tur = ""
        ozet = b.get("summary") or ""
        if ozet.startswith("Tür:"):
            tur = ozet.split("\n", 1)[0].split(":", 1)[1].strip()
            ozet = ozet.split("\n", 1)[1] if "\n" in ozet else ""
        satir = f"  [{b['id']}] {b['title'] or b['filename']}" + (f" ({tur})" if tur else "")
        # BAŞLIK TEK BAŞINA KONUYU SÖYLEMİYOR. ÖLÇÜLDÜ: "Rectangular to
        # Cylindrical Transformation hakkında bilgi ver" sorusu, yüklü
        # belgelerden biri tam olarak bunu anlatan ders notu olduğu hâlde
        # "kapsam dışı" sayılıp reddedildi — sınıflandırıcı listede yalnızca
        # "Elektromanyetik Alanlar Ders Notları" başlığını görüyordu. Özetin
        # ilk cümlesi belgenin neyi kapsadığını söylüyor.
        konu = " ".join(ozet.split())[:_SUMMARY_CHARS]
        if konu:
            satir += f"\n      konu: {konu}…"
        satirlar.append(satir)
    fazla = len(belgeler) - _MAX_LISTED_DOCUMENTS
    if fazla > 0:
        satirlar.append(f"  … and {fazla} more")
    return "Uploaded documents:\n" + "\n".join(satirlar) + "\n\n"


def run(state: QueryState) -> QueryState:
    provider = registry.tier("cheap")
    belgeler = hazir_belgeler()
    gecerli_idler = {b["id"] for b in belgeler}

    # Geri bildirimden öğrenilen örnekler ve geçmiş, sistem prompt'una DEĞİL
    # kullanıcı mesajına giriyor: sistem prompt'u sabit kalsın ki prompt-cache
    # öneki her sorguda geçersizleşmesin.
    examples = feedback.fewshot_examples(state.query, query_vector=state.query_vector)
    fewshot = feedback.render_fewshot(examples)

    user = _belge_listesi(belgeler)
    if state.scope_document_ids:
        # Odak sınıflandırıcıya da söyleniyor: "garanti kaç ay?" sorusunun kendi
        # başına anlaşılır hâli belgenin adını içermeli.
        user += (
            "The user has FOCUSED this conversation on: "
            + "; ".join(f"[{i}] {t}" for i, t in zip(state.scope_document_ids, state.scope_titles))
            + ". Questions refer to this document unless they explicitly name another.\n\n"
        )
    if state.history:
        user += "Earlier turns in this conversation:\n"
        for turn in state.history[-_HISTORY_TURNS:]:
            role = "User" if turn.get("role") == "user" else "Assistant"
            text = " ".join((turn.get("content") or "").split())
            if len(text) > _HISTORY_CHARS:
                text = text[:_HISTORY_CHARS] + "…"
            user += f"  {role}: {text}\n"
        user += "\n"
    user += f"Question: {state.query}"
    if fewshot:
        user += f"\n{fewshot}"

    try:
        data, resp = provider.complete_json(
            system=config.prompt("classifier"),
            user=user,
            max_tokens=400,
            cache_system=True,
        )
    except LLMError as exc:
        # Sınıflandırma çökerse en güvenli yola devam: belgelerde ara.
        state.note(f"Sınıflandırma başarısız, belgelerde aramaya düşüldü: {exc}")
        # Sorunun hangi belgeyi andığı bilinmeden aranıyor; ölçümde bu yol
        # yanlış belgelerden kendinden emin bir cevap üretti. Rozet bunu söylemeli.
        state.low_confidence = True
        state.category = Category.BELGE_ICI
        state.difficulty = 3
        state.lang = "tr"
        state.search_query = state.query
        state.standalone_query = state.query
        return state

    state.add_cost(resp)
    state.model_used = resp.model

    raw_category = str(data.get("category", "")).strip().lower()
    if raw_category not in _VALID_CATEGORIES:
        state.note(f"Bilinmeyen kategori {raw_category!r}, belge_ici varsayıldı")
    state.category = Category.parse(raw_category)
    state.difficulty = _clamp_difficulty(data.get("difficulty"))

    detected = str(data.get("language", "")).strip().lower()
    state.lang = detected if detected in ("tr", "en") else "tr"
    if state.lang == "en" and _turkce_mi(state.query):
        # ÖLÇÜLDÜ: "Rectangular to Cylindrical Transformation hakkında bilgi
        # ver" sorusuna cevap İngilizce geldi — cümledeki İngilizce terim
        # sınıflandırıcıyı yanılttı. Teknik terim sorunun dilini değiştirmiyor;
        # Türkçe işaretleri (ekler, soru kelimeleri, Türkçeye özgü harfler)
        # taşıyan bir cümle Türkçedir.
        state.lang = "tr"
        state.note("Soru Türkçe yazılmış, cevap dili Türkçeye çevrildi")

    standalone = str(data.get("standalone_question") or "").strip()
    state.standalone_query = standalone or state.query
    if state.history and standalone and standalone != state.query:
        state.note(f"Takip sorusu çözümlendi: '{standalone}'")

    state.search_query = str(data.get("search_query") or "").strip() or state.standalone_query

    # Model listede olmayan bir kimlik uydurabilir; yalnızca gerçekten var
    # olanlar geçiyor. Uydurma bir kimlikle arama, boş sonuç ve "belgede yok"
    # cevabı demekti — kullanıcı belgenin kendisinde sorun var sanırdı.
    ids = []
    for item in data.get("document_ids") or []:
        try:
            doc_id = int(item)
        except (TypeError, ValueError):
            continue
        if doc_id in gecerli_idler and doc_id not in ids:
            ids.append(doc_id)
    uydurma = len(data.get("document_ids") or []) - len(ids)
    if uydurma > 0:
        state.note(f"Sınıflandırıcının döndürdüğü {uydurma} belge kimliği listede yoktu, atıldı")
    state.document_ids = ids

    keywords = data.get("keywords") or []
    state.keywords = [str(k).strip() for k in keywords if str(k).strip()][:5]

    if examples:
        state.note(f"{len(examples)} geri bildirim örneği sınıflandırmaya eklendi")
    return state


# Türkçeye özgü harfler ve İngilizcede karşılığı olmayan işlev kelimeleri.
# Yalnızca bunlara bakılıyor: İngilizce bir cümlede hiçbiri geçmez, Türkçe bir
# cümlede ise teknik terimler İngilizce olsa bile en az biri geçer.
_TR_HARF = set("çğıöşüÇĞİÖŞÜ")
_TR_KELIME = {
    "hakkında", "nedir", "nasıl", "nerede", "kaç", "mı", "mi", "mu", "mü",
    "ne", "ver", "anlat", "açıkla", "özetle", "göre", "için", "ile", "var",
    "yok", "midir", "bu", "şu", "hangi", "kadar", "olur", "belge", "belgede",
    "sözleşme", "sözleşmede", "madde", "sayfa", "bana", "söyle", "yaz",
}


def _turkce_mi(soru: str) -> bool:
    if any(harf in _TR_HARF for harf in soru):
        return True
    kelimeler = {k.strip(".,:;?!()").casefold() for k in soru.split()}
    return bool(kelimeler & _TR_KELIME)


def _clamp_difficulty(value) -> int:
    try:
        return max(1, min(5, int(float(value))))
    except (TypeError, ValueError):
        return 3
