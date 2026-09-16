"""Boru hattı orkestratörü.

    Soru
      -> LLM Sınıflandırıcı
           kapsam_disi   -> Kibar Ret [SON]
           belge_ozeti   -> Saklı Özet (0 token) [SON]
           belge_ici     -> Getir -> Puanla
           capraz_belge  -> Belge Başına Getir -> Puanla
                              alakalı değil -> Yeniden Yaz (<= max_rewrites)
                                                tükendiyse -> "Belgelerde yok" [SON]
                              alakalı       -> Model Seçici -> Üret
                                                -> Halüsinasyon? -> Yeterli?
                                                   (<= max_regen yeniden üretim)
                                                -> Atıflı Yanıt [SON]

Network Asistanı'ndan farkı: web araması YOK. Belgede bulunmayan bir sorunun
cevabı "bulunamadı"dır; kurumsal belge asistanının değeri söylediği her şeyin
yüklenen belgeye dayanması. Belgede olmayan bir şeyi dışarıdan tamamlayan bir
asistan, belgede OLAN şeye dair cevaplarına da güveni sarsar.

Guard'lar aynen korundu (orada ölçülerek eklenmişti): döngü sayaçları, maliyet
ve süre devre kesicileri. Guard devreye girdiğinde akış sessizce kesilmez:
eldeki en iyi cevap `low_confidence` etiketiyle döner.
"""
from __future__ import annotations

import time

import config
from core.nodes import (
    classifier,
    generate,
    grade,
    hesap,
    model_router,
    retrieve,
    rewrite,
    simple_paths,
    verify,
)
from core.state import Category, QueryState, Route
from observability import trace
from belge import embedder
from belge.store import get_store


class Budget:
    """Maliyet ve süre devre kesicisi."""

    def __init__(self, state: QueryState):
        self.state = state
        self.max_cost = float(config.get("limits.max_cost_usd_per_query", 0.05))
        self.max_seconds = float(config.get("limits.max_wall_seconds", 90))
        self.started = time.perf_counter()

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def exceeded(self) -> str | None:
        if self.state.cost_usd >= self.max_cost:
            return f"maliyet tavanı aşıldı (${self.state.cost_usd:.4f} >= ${self.max_cost})"
        if self.elapsed >= self.max_seconds:
            return f"süre tavanı aşıldı ({self.elapsed:.0f}s >= {self.max_seconds:.0f}s)"
        return None

    def check(self) -> bool:
        reason = self.exceeded()
        if reason:
            self.state.low_confidence = True
            self.state.note(f"Devre kesici: {reason}")
            return True
        return False


def run(
    query: str,
    *,
    persist: bool = True,
    on_delta=None,
    history: list[dict] | None = None,
    regenerate: bool = False,
    on_step=None,
    force_tier: str | None = None,
    scope_document_ids: list[int] | None = None,
) -> QueryState:
    """Bir sorguyu uçtan uca çalıştırır ve tamamlanmış durumu döner.

    `regenerate=True` "yeniden üret" düğmesi için: küçük bir sıcaklıkla
    üretiliyor, çünkü T=0'da aynı bağlam aynı cevabı verir ve düğme işlevsiz
    görünür.

    ÖNBELLEK YOK (kaldırıldı 2026-09-14). Belge kümesi sürekli değişiyor
    (yükleme, silme) ve sohbet bir belgeye odaklanabiliyor; aynı soru metni
    bu koşullara göre farklı cevap gerektiriyor. Önbellek anahtarını bu
    durumların hepsine göre doğru kurmak, tasarrufundan daha fazla hata
    riski taşıyordu.
    """
    state = QueryState(query=query.strip(), history=history or [])
    state.on_step = on_step
    budget = Budget(state)

    # ODAK: yalnızca hâlâ hazır olan belgeler geçerli. Odaklanılan belge bu
    # arada silinmişse sessizce tüm belgelerde aramak yanlış olurdu —
    # kullanıcı "yalnızca bu belgede" dedi. Odak düşüyor ve not düşülüyor.
    if scope_document_ids:
        hazir = {b["id"]: b for b in classifier.hazir_belgeler()}
        gecerli = [i for i in scope_document_ids if i in hazir]
        if len(gecerli) < len(scope_document_ids):
            state.note("Odaklanılan belge artık yüklü değil; odak kaldırıldı")
        state.scope_document_ids = gecerli
        state.scope_titles = [hazir[i]["title"] or hazir[i]["filename"] for i in gecerli]

    if not state.query:
        state.note("Boş sorgu")
        return state

    # --- 2. Sınıflandırıcı ---
    with trace.step(state, "classifier"):
        classifier.run(state)
    _odagi_uygula(state)
    state.steps[-1].update(
        category=state.category.value,
        difficulty=state.difficulty,
        documents=len(state.document_ids),
    )

    # --- 3. Üretimsiz yollar ---
    if state.category == Category.KAPSAM_DISI and _belgelerde_var_mi(state):
        state.category = Category.BELGE_ICI
        state.search_query = state.search_query or state.standalone_query or state.query

    if state.category == Category.KAPSAM_DISI:
        with trace.step(state, "refusal"):
            simple_paths.refuse(state)
        return _finish(state, budget, persist)

    if state.category == Category.BELGE_OZETI:
        with trace.step(state, "summary"):
            simple_paths.summary(state)
        return _finish(state, budget, persist)

    if not classifier.hazir_belgeler():
        state.route = Route.REFUSAL
        state.answer = simple_paths.no_documents_message(state.lang)
        return _finish(state, budget, persist)

    # --- 4. Getir -> Puanla -> (Yeniden Yaz) ---
    max_rewrites = int(config.get("limits.max_rewrites", 2))
    tried_queries: list[str] = []
    # Yeniden yazma daha kötü bir sorgu üretebilir: 1. tur 1 alakalı chunk
    # bulup 2. tur hiç bulamazsa elde kalan 0 olur. En iyi tur ayrıca saklanıyor.
    best_hits: list = []

    while True:
        tried_queries.append(state.search_query or state.query)
        with trace.step(state, "retrieve", query=state.search_query):
            retrieve.run(state)
        state.steps[-1]["hits"] = len(state.hits)

        if state.full_context:
            # Belgelerin tamamı okunuyor; puanlayıcı "alakasız" bulduğu maddeleri
            # atardı, ama uygunluk kontrolünde hangi maddenin önemli olduğuna
            # karşılaştırma karar veriyor, önceden değil.
            state.graded_hits = state.hits
            break

        with trace.step(state, "grade"):
            grade.run(state)
        state.steps[-1]["relevant"] = len(state.graded_hits)

        if len(state.graded_hits) > len(best_hits):
            best_hits = state.graded_hits
        if grade.is_sufficient(state):
            break
        if budget.check() or state.rewrites >= max_rewrites:
            break

        before = state.rewrites
        with trace.step(state, "rewrite", attempt=state.rewrites + 1):
            rewrite.run(state, tried_queries)
        if state.rewrites == before:
            break

    if len(best_hits) > len(state.graded_hits):
        state.note(f"Yeniden yazma sonucu kötüleştirdi; en iyi tur geri alındı ({len(best_hits)} chunk)")
        state.graded_hits = best_hits

    # --- 5. Belgelerde yoksa dürüstçe söyle ---
    if not state.graded_hits:
        state.route = Route.RAG
        adlar = {b["id"]: b["title"] or b["filename"] for b in classifier.hazir_belgeler()}
        state.answer = (generate.not_found_message(
                            state.lang, scope_titles=state.scope_titles,
                            searched_titles=[adlar[i] for i in state.document_ids if i in adlar])
                        + generate.terim_ipucu(state))
        state.grounded = True
        state.sufficient = False
        state.note("Yüklenen belgelerde alakalı bir bölüm bulunamadı")
        return _finish(state, budget, persist)

    if not state.full_context:
        _komsulari_ekle(state)

    if not grade.is_sufficient(state):
        # Eşiğin altında ama sıfır değil: elimizdekiyle cevap veriyoruz ve
        # kaynağın zayıf olduğunu açıkça işaretliyoruz.
        state.low_confidence = True
        state.note(
            f"Yalnızca {len(state.graded_hits)} alakalı chunk bulundu "
            f"(eşik {config.get('retrieval.min_relevant')}); cevap eksik olabilir"
        )

    # --- 6. Model Seçici ---
    state.context_text = generate.build_context(state)
    with trace.step(state, "model_router"):
        model_router.run(state, force_tier=force_tier)
    state.steps[-1].update(tier=state.tier_used, context_tokens=state.context_tokens)

    # --- 7. Üret -> Halüsinasyon? -> Yeterli? (ortak yeniden üretim bütçesi) ---
    max_regen = int(config.get("limits.max_regen", 1))
    hint = ""
    while True:
        with trace.step(state, "generate", tier=state.tier_used, regen=state.regens):
            generate.run(
                state, feedback_hint=hint, on_delta=on_delta,
                temperature=None if not regenerate else float(
                    config.get("limits.regenerate_temperature", 0.35)
                ),
            )
        if state.service_error:
            # Servis cevap vermedi: elde bir cevap değil hata metni var. Onu
            # denetime sokmak kotayı daha da zorlar ve "yetersiz" bulunup
            # yeniden üretim istenir — ölçümde tam olarak böyle oldu.
            state.low_confidence = True
            state.grounded = None
            state.sufficient = None
            break

        if budget.check():
            break

        # Hesap denetimi LLM'siz ve anında; halüsinasyon denetçisinden önce
        # çalışıyor ki yanlış bir sayı için iki model çağrısı harcanmasın.
        with trace.step(state, "calculation_check"):
            hesaplar = hesap.denetle(state.answer)
            hatalar = [h for h in hesaplar if not h.dogru]
        state.steps[-1].update(checked=len(hesaplar), wrong=len(hatalar))
        if hatalar:
            state.note(f"Hesap hatası: {len(hatalar)}/{len(hesaplar)} satır yazılımla tutmadı")
            if state.regens < max_regen:
                state.regens += 1
                hint = hesap.ipucu(hatalar)
                continue
            state.low_confidence = True
            state.note("Yeniden üretim bütçesi doldu, cevaptaki hesaplar hatalı olabilir")
        else:
            eksik = hesap.kisa_cevap_eksik(state.answer, hesaplar)
            if eksik and state.regens < max_regen:
                state.note("Hesap yapılmış ama sonuç Kısa cevap'ta yok; yeniden yazdırılıyor")
                state.regens += 1
                hint = eksik
                continue

        with trace.step(state, "hallucination_check"):
            verify.check_hallucination(state)
        if not state.grounded and state.regens < max_regen:
            state.regens += 1
            hint = verify.hallucination_hint(state)
            continue

        with trace.step(state, "sufficiency_check"):
            verify.check_sufficiency(state)
        if not state.sufficient and state.regens < max_regen:
            state.regens += 1
            hint = verify.sufficiency_hint(state)
            continue
        break

    # Tam okumada "bulunamadı" yolu hiç çalışmıyor (bütün belge bağlamda); model
    # terimin bu belgede olmadığını söyleyebilir ama BAŞKA bir belgede olduğunu
    # bilemez. Deterministik ipucu burada da ekleniyor. Denetimlerden SONRA:
    # denetçinin kaynaklarla karşılaştırdığı metin modelin yazdığı metin olmalı.
    if state.full_context and state.category == Category.BELGE_ICI and not state.service_error:
        ipucu = generate.terim_ipucu(state)
        if ipucu:
            onek = (f"\n\n_Arama yalnızca odaklanılan belgede yapıldı._"
                    if state.scope_document_ids else "")
            state.answer = state.answer.rstrip() + onek + ipucu

    # Sorun giderildiyse bunu da yaz: iz yalnızca "taslak yetersiz" dediğinde
    # okuyan kişi bunu ekrandaki cevaba dair bir hüküm sanıyordu.
    if state.regens and state.grounded is not False and state.sufficient is not False:
        state.note(f"Yeniden üretim sorunu giderdi ({state.regens}. tur geçti)")
    if state.grounded is False:
        state.low_confidence = True
        state.note("Yeniden üretim bütçesi doldu, cevap desteklenmemiş ifadeler içerebilir")
    if state.sufficient is False:
        state.low_confidence = True
        state.note("Yeniden üretim bütçesi doldu, cevap eksik kalmış olabilir")

    return _finish(state, budget, persist)


def _komsulari_ekle(state: QueryState) -> None:
    """Alakalı bulunan her chunk'ın komşularını da bağlama alır.

    Chunk sınırı, anlatının sınırı değil. ÖLÇÜLDÜ (Elektromanyetik Alanlar
    ders notu, "Stokes teoremini anlat"): teoremin ifadesi bir parçada, ispatın
    "yüzeyi N parçaya böl" adımı sonrakinde, örneğin çizgi integrali
    hesapları iki parça ötede. Arama yalnızca "Stokes" kelimesini taşıyan
    parçayı alakalı buluyor ve model yarım bir ispat okuyor.

    Komşu getirmek yerel ve ücretsiz (SQLite); bütçe yine karakterle sınırlı,
    yani bağlam kontrolsüz büyümüyor. Komşular alaka sırasının SONUNA
    ekleniyor: sıralamayı aramanın kararı belirlemeye devam ediyor.
    """
    if not state.graded_hits:
        return
    butce = int(config.get("retrieval.context_chars", 12000))
    genislik = int(config.get("retrieval.komsu_genislik", 1))
    kullanilan = sum(len(h.text) for h in state.graded_hits)
    if kullanilan >= butce or genislik <= 0:
        return

    elde = {(h.document_id, h.ordinal) for h in state.graded_hits}
    istekler: list[tuple[int, int]] = []
    for hit in state.graded_hits:
        if hit.kind != "body":
            continue
        for kayma in range(-genislik, genislik + 1):
            anahtar = (hit.document_id, hit.ordinal + kayma)
            if kayma and anahtar not in elde and anahtar not in istekler:
                istekler.append(anahtar)
    if not istekler:
        return

    eklenen = []
    for komsu in get_store().komsular(istekler):
        if kullanilan + len(komsu.text) > butce:
            continue
        eklenen.append(komsu)
        kullanilan += len(komsu.text)
    if eklenen:
        state.graded_hits = state.graded_hits + eklenen
        state.note(f"{len(eklenen)} komşu chunk bağlama eklendi "
                   f"(anlatı chunk sınırında kesilmesin); toplam {kullanilan} karakter")


def _belgelerde_var_mi(state: QueryState) -> bool:
    """Reddetmeden önce belgelere BAKAN son kontrol.

    Sınıflandırıcı konu sorularını genel kültür sanıp reddedebiliyor: ÖLÇÜLDÜ —
    "Rectangular to Cylindrical Transformation hakkında bilgi ver" sorusu, tam
    olarak bunu anlatan 52 sayfalık ders notu yüklüyken üç koşuda da
    `kapsam_disi` döndü. Reddetmek, cevabı 17. sayfada duran bir soru için
    verilebilecek en kötü cevap.

    Kontrol yerel ve ücretsiz: soru vektörü belgelerin gövde parçalarıyla
    karşılaştırılıyor. ÖLÇÜLDÜ (yüklü altı belge): belgede karşılığı olan
    sorular 0.66-0.72, kapsam dışı olanlar ("merhaba", "hava durumu", "kek
    tarifi", "TCP nedir", "sen kimsin") 0.35-0.46 veriyor. Eşik 0.50 aradaki
    boşlukta. Yanılırsa bedeli bir arama: bulunamazsa zaten "belgelerde yok"
    cevabı dönüyor.
    """
    esik = float(config.get("retrieval.kapsam_disi_esigi", 0.50))
    store = get_store()
    try:
        if state.query_vector is None:
            state.query_vector = embedder.encode_one(state.query, is_query=True)
        hits = store.search(state.query, query_vector=state.query_vector,
                            top_k=1, kinds=("body",), max_per_document=1)
    except Exception as exc:          # arama çökerse ret yolu bozulmasın
        state.note(f"Kapsam kontrolü yapılamadı: {exc}")
        return False
    if not hits or hits[0].dense_score < esik:
        return False
    state.note(
        f"Soru kapsam dışı sayılmıştı ama “{hits[0].title}” belgesinde "
        f"benzerlik {hits[0].dense_score:.2f} (eşik {esik:.2f}); belgelerde arandı"
    )
    return True


def _odagi_uygula(state: QueryState) -> None:
    """Kullanıcının odağı sınıflandırıcının tahmininin önüne geçer.

    Odak bir talimat: sınıflandırıcı başka bir belge adı yakalasa da arama
    yalnızca odaklanılan belgede yapılıyor. Tek belgeye odaklıyken
    "karşılaştır" diye sorulan soru çapraz sentez olamaz — ortada tek belge
    var — ve tek belge sorusu olarak cevaplanıyor.
    """
    if not state.scope_document_ids:
        return
    if state.document_ids and set(state.document_ids) - set(state.scope_document_ids):
        state.note("Soru başka bir belgeyi anıyor olabilir; odak nedeniyle yalnızca "
                   "odaklanılan belgede arandı")
    state.document_ids = list(state.scope_document_ids)
    if state.category == Category.CAPRAZ_BELGE and len(state.scope_document_ids) == 1:
        state.category = Category.BELGE_ICI
        state.note("Tek belgeye odaklıyken karşılaştırma yapılamaz; soru bu belge içinde cevaplandı")


def _finish(state: QueryState, budget: Budget, persist: bool) -> QueryState:
    state.total_ms = budget.elapsed * 1000
    if persist:
        trace.persist(state)
    return state
