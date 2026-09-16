"""Belgelerden chunk getir.

İki strateji:

  belge_ici     tek havuzdan hibrit arama; soru belgeleri adıyla andıysa arama
                o belgelerle sınırlı.
  capraz_belge  her belge KENDİ İÇİNDE ayrı aranıyor ve sonuçlar birleşiyor.

Çapraz sentezde ortak havuzdan arama yetmiyor: "hangi sözleşme yönetmeliğe
aykırı?" sorusu sözleşme metinlerine yönetmelikten daha çok benziyor ve ortak
top_k tamamen sözleşmelerle dolup yönetmeliği dışarıda bırakabiliyor. O zaman
soru cevapsız kalır — karşılaştırmanın bir tarafı hiç okunmamıştır. Belge başına
arama her belgenin temsil edilmesini garanti ediyor; bedeli yalnızca birkaç
lokal embedding.
"""
from __future__ import annotations

import config
from core.state import Category, QueryState
from belge import embedder
from belge.store import get_store

from .classifier import hazir_belgeler


def run(state: QueryState) -> QueryState:
    store = get_store()
    queries = [state.search_query or state.standalone_query or state.query]
    # Sadeleştirilmiş arama sorgusu ile sorunun kendisi farklıysa ikisi de
    # aranıp birleştiriliyor: sadeleştirme bazen ayırt edici bir kelimeyi
    # atıyor ("peşin"), ham soru onu koruyor. Ek model maliyeti yok.
    tam = state.standalone_query or state.query
    if tam.casefold() != queries[0].casefold():
        queries.append(tam)

    vectors = [embedder.encode_one(q, is_query=True) for q in queries]
    if state.query_vector is None:
        state.query_vector = vectors[-1]

    if state.category == Category.CAPRAZ_BELGE:
        state.hits = _capraz(store, state, queries, vectors)
        return state

    top_k = int(config.get("retrieval.top_k", 8))
    max_chunks = max(top_k, int(config.get("retrieval.max_chunks", 24)))
    scope = set(state.document_ids) or None

    # ADI GEÇEN BELGE KÜÇÜKSE TAMAMI OKUNUYOR.
    #
    # ÖLÇÜLDÜ (zorlayıcı soru seti): "Güney sözleşmesinde 60 gün gecikirse ceza
    # ne olur?" sorusunda ceza oranı Madde 5'te, sözleşme bedeli Madde 3'te.
    # Puanlayıcı "ceza" sorusu için bedel maddesini alakasız sayıp eledi ve
    # model hesap yapamayıp "60 gün için özel kural yok" dedi. Cevabın
    # girdilerinin aynı belgenin farklı maddelerinde durması sözleşmelerde
    # kural, istisna değil. Çapraz sentezdeki tam okumanın aynısı, tek belge
    # için. Büyük belgede arama ve puanlama devam ediyor.
    if scope:
        butce = int(config.get("retrieval.single_full_context_chars", 16000))
        tum = store.document_chunks(sorted(scope))
        toplam = sum(len(h.text) for h in tum)
        if tum and toplam <= butce:
            state.full_context = True
            state.hits = tum
            state.note(f"Tam okuma: adı geçen belgenin tamamı bağlama alındı "
                       f"({toplam} karakter, bütçe {butce}); arama ve puanlama atlandı")
            return state
    cap = None
    if scope:
        # BELGE BAŞINA TAVAN KAPSAMA GÖRE GENİŞLİYOR.
        #
        # Tavanın amacı tek bir belgenin tüm sonuçları doldurup diğerlerini
        # dışarıda bırakmaması. Arama zaten adı geçen belgelerle sınırlıyken bu
        # amaç ortadan kalkıyor ama tavan kalıyordu: tek belgeye sorulan her
        # soru en fazla 3 chunk görüyordu. ÖLÇÜLDÜ (G9 veri sayfası, 9 chunk):
        # "DS0 kapasiteleri" sorusunda kapasite chunk'ları hiç gelmedi, cevap
        # yalnızca özetten çıktı ve iki yeniden yazma boşa harcandı. Artık pay
        # kapsamdaki belgelere eşit bölünüyor: tek belgede top_k'nın tamamı.
        cap = max(int(config.get("retrieval.max_chunks_per_document", 3)),
                  -(-max_chunks // len(scope)))
    per_query = [
        store.search(q, query_vector=v, top_k=max_chunks,
                     document_ids=scope, max_per_document=cap)
        for q, v in zip(queries, vectors)
    ]
    bulunan = _fuse(per_query, top_k=max_chunks, max_per_doc=cap)
    state.hits = _butceye_gore(bulunan, en_az=top_k)
    if len(state.hits) > top_k:
        karakter = sum(len(h.text) for h in state.hits)
        state.note(f"Bağlam bütçesi: {len(state.hits)} chunk, {karakter} karakter "
                   f"(taban {top_k} chunk)")
    return state


def _butceye_gore(hits: list, *, en_az: int) -> list:
    """Alaka sırasını bozmadan, karakter bütçesi dolana kadar chunk alır.

    Sabit bir chunk sayısı, chunk boyutu belgeden belgeye değiştiği için sabit
    bir bağlam vermiyor: 8 chunk bir sözleşmede belgenin yarısı, 52 sayfalık
    parçalı bir ders notunda %9'u. `en_az` eski davranışın tabanı — küçük
    chunk'lı belgede üstüne çıkılıyor, büyük chunk'lı belgede bütçe aşılsa bile
    bu kadarı yine alınıyor (aksi hâlde uzun maddeli sözleşmede bağlam
    daralırdı).
    """
    butce = int(config.get("retrieval.context_chars", 12000))
    out, toplam = [], 0
    for hit in hits:
        if len(out) >= en_az and toplam >= butce:
            break
        out.append(hit)
        toplam += len(hit.text)
    return out


def _capraz(store, state: QueryState, queries, vectors) -> list:
    per_doc = int(config.get("retrieval.capraz_per_document", 3))
    max_docs = int(config.get("retrieval.capraz_max_documents", 8))

    if len(state.document_ids) >= 2:
        hedef = state.document_ids
    else:
        # Tek belge adı verilmiş ya da hiç verilmemiş: karşılaştırma tüm
        # belgelere karşı. "Güney sözleşmesi yönetmeliğe uyuyor mu?" sorusunda
        # sınıflandırıcı yalnızca Güney'i döndürse bile yönetmeliğin okunması
        # gerekiyor.
        hedef = [b["id"] for b in hazir_belgeler()]
    # BELGELER BAĞLAMA SIĞIYORSA ARAMA YOK, TAMAMI OKUNUYOR.
    #
    # Uygunluk kontrolünde ("hangi sözleşme yönetmeliğe aykırı?") hangi
    # maddelerin karşılaştırılacağını önceden bilmek mümkün değil. Belge başına
    # 3 chunk'lık arama ceza maddesini getirip gizlilik maddesini getirmedi;
    # model de "gizlilik süresine ilişkin veri yok" yazdı — oysa sözleşmenin
    # 7. maddesinde yazıyordu. Kısmi okuma, eksik değil YANLIŞ cevap üretti.
    butce = int(config.get("retrieval.capraz_full_context_chars", 24000))
    tum = store.document_chunks(hedef[:max_docs])
    toplam = sum(len(h.text) for h in tum)
    if tum and toplam <= butce and len(hedef) <= max_docs:
        state.full_context = True
        state.note(
            f"Çapraz okuma: {len(hedef)} belgenin tamamı bağlama alındı "
            f"({toplam} karakter, bütçe {butce}); arama ve puanlama atlandı"
        )
        return tum

    if len(hedef) > max_docs:
        state.note(f"{len(hedef)} belge var, karşılaştırmaya en alakalı {max_docs} tanesi alındı")
        # En alakalıları seçmek için önce ortak havuzdan kısa bir arama.
        kaba = store.search(queries[0], query_vector=vectors[0], top_k=max_docs * 3,
                            max_per_document=1, document_ids=set(hedef))
        hedef = [h.document_id for h in kaba][:max_docs]

    hits = []
    for doc_id in hedef:
        per_query = [
            store.search(q, query_vector=v, document_ids={doc_id},
                         top_k=per_doc, max_per_document=per_doc)
            for q, v in zip(queries, vectors)
        ]
        hits.extend(_fuse(per_query, top_k=per_doc))
    state.note(f"Çapraz arama: {len(hedef)} belge, belge başına en fazla {per_doc} chunk")
    return hits


def _fuse(results: list[list], *, top_k: int, max_per_doc: int | None = None) -> list:
    """Sorgu varyantlarının sonuçlarını Reciprocal Rank Fusion ile birleştirir.

    store.search zaten dense+BM25'i RRF ile kaynaştırıyor; bu ikinci kat aynı
    sabitle sorgu varyantlarını kaynaştırıyor. RRF yalnızca SIRA bilgisini
    kullandığı için farklı sorgulara ait kıyaslanamaz skorlar sorun olmuyor.
    """
    if len(results) == 1:
        return results[0][:top_k]
    rrf_k = int(config.get("retrieval.rrf_k", 60))
    if max_per_doc is None:
        max_per_doc = int(config.get("retrieval.max_chunks_per_document", 3))

    fused: dict[int, float] = {}
    best: dict[int, object] = {}
    for hits in results:
        for rank, hit in enumerate(hits):
            fused[hit.row_id] = fused.get(hit.row_id, 0.0) + 1.0 / (rrf_k + rank + 1)
            previous = best.get(hit.row_id)
            if previous is None or hit.dense_score > previous.dense_score:
                best[hit.row_id] = hit

    # Belge başına sınır füzyondan SONRA yeniden uygulanmalı: her arama kendi
    # içinde sınırı tutuyor ama farklı sorgular aynı belgenin farklı
    # chunk'larını getirebiliyor ve toplamda sınır aşılıyor.
    out, per_doc = [], {}
    for row_id, score in sorted(fused.items(), key=lambda kv: -kv[1]):
        hit = best[row_id]
        seen = per_doc.get(hit.document_id, 0)
        if max_per_doc and seen >= max_per_doc:
            continue
        per_doc[hit.document_id] = seen + 1
        hit.fused_score = score
        out.append(hit)
        if len(out) >= top_k:
            break
    return out
