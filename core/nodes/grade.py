"""Pasajları Puanla / Pasaj Alakalı mı? düğümü.

İki maliyet optimizasyonu:
  1. Cosine tabanının altındaki pasajlar LLM'e HİÇ gitmez (ücretsiz eleme).
  2. Kalanlar K ayrı çağrı yerine TEK batched çağrıda puanlanır.
"""
from __future__ import annotations

import config
from core.state import QueryState
from llm import registry
from llm.base import LLMError


def run(state: QueryState) -> QueryState:
    floor = float(config.get("retrieval.score_floor", 0.35))

    candidates = [h for h in state.hits if h.dense_score >= floor]
    prefiltered = len(state.hits) - len(candidates)
    if prefiltered:
        state.note(
            f"{prefiltered} chunk skor tabanının altında kaldı, "
            f"LLM'e gönderilmedi"
        )

    if not candidates:
        state.graded_hits = []
        return state

    # BENZERLİĞİ KESİN OLANLAR PUANLAYICIYA HİÇ GİTMİYOR.
    #
    # Bu parçalar puanlayıcı "alakasız" dese bile bağlamda kalıyor (aşağıdaki
    # kural). Yani onlar için harcanan girdi token'ı ve bekleme, sonucu
    # değiştirmeyen bir iş. ÖLÇÜLDÜ: puanlama medyan 2.08 sn ve toplam sürenin
    # %19'u; gönderilen pasajların çoğu zaten eşiğin üstünde.
    kesin_esik = float(config.get("retrieval.kesin_alaka", 0.60))
    kesinler = [h for h in candidates if h.dense_score >= kesin_esik]
    belirsizler = [h for h in candidates if h.dense_score < kesin_esik]

    if not belirsizler:
        state.graded_hits = sorted(kesinler, key=lambda h: -h.dense_score)
        state.note(f"{len(kesinler)} chunk'ın hepsi benzerlik eşiğinin üstünde "
                   f"(≥{kesin_esik:.2f}); puanlayıcı çalıştırılmadı")
        return state

    candidates = belirsizler
    provider = registry.tier("cheap")
    # Kesme sınırı bilinçli olarak cömert. 900 karakterde kesildiğinde
    # abstract'ların %84'ü kırpılıyor ve puanlayıcının gördüğü metnin %30'u
    # kayboluyordu — bir makalenin alakalı olduğu çoğu zaman abstract'ın
    # SONUNDA (sonuçlar bölümünde) belli olur, yani tam da kesilen yerde.
    # Maliyeti önemsiz: tek batched çağrıda ~2000 ek girdi token'ı.
    limit = int(config.get("retrieval.grade_snippet_chars", 2000))
    # Bölüm adı puanlayıcıya bağlam veriyor: "Related Work"tan gelen bir parça
    # ile "Evaluation"dan gelen aynı konuda olsa bile aynı değerde değil.
    passages = "\n\n".join(
        f"[{i}] {h.title} — {h.location}\n{h.text[:limit]}"
        for i, h in enumerate(candidates)
    )

    # SORUNUN ÇÖZÜLMÜŞ HÂLİ. `state.query` kullanıcının yazdığı ham metin:
    # takip sorularında "daha detaylı anlat" ya da "peki cezası ne?" oluyor ve
    # puanlayıcı böyle bir cümleye göre alaka kararı veremiyor. ÖLÇÜLDÜ (EMAT
    # ders notu): aynı soru üç koşuda 24 chunk'ın 9'unu, 24'ünü ve 3'ünü
    # alakalı buldu — kararın kendisi gürültüydü. Sınıflandırıcı sorunun
    # kendine yeten hâlini zaten yazıyor.
    soru = state.standalone_query or state.query
    try:
        data, resp = provider.complete_json(
            system=config.prompt("grade"),
            user=f"Question: {soru}\n\nPassages:\n\n{passages}",
            # 900 değil: bütçe dolana kadar chunk alındığında puanlanacak
            # pasaj sayısı 8'den 24'e çıkabiliyor ve her pasaj için bir JSON
            # satırı dönüyor; kesilen JSON hiç puan gelmemesi demek.
            max_tokens=1600,
            cache_system=True,
        )
    except LLMError as exc:
        # Puanlama çökerse pasajları atmak yerine cosine sıralamasına güven;
        # üretim düğümü zaten groundedness kontrolünden geçecek.
        state.note(f"Pasaj puanlama başarısız, cosine sıralaması kullanıldı: {exc}")
        state.low_confidence = True
        candidates = kesinler + candidates
        # `top_k` ile kesilmiyor: kaç chunk'ın bağlama gireceğine karakter
        # bütçesiyle retrieve karar verdi. Burada yeniden kesmek, puanlayıcı
        # çöktüğü için bağlamı da daraltmak olurdu.
        state.graded_hits = candidates
        return state

    state.add_cost(resp)

    grades = data.get("grades") or data.get("items") or []
    relevant_idx: dict[int, float] = {}
    for grade in grades:
        if not isinstance(grade, dict):
            continue
        try:
            idx = int(grade.get("idx"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(candidates) and bool(grade.get("relevant")):
            relevant_idx[idx] = _to_float(grade.get("score"), 0.5)

    if not grades:
        state.note("Puanlayıcı boş sonuç döndürdü, cosine sıralaması kullanıldı")
        state.graded_hits = kesinler + candidates
        return state

    kept = [
        (candidates[i], score)
        for i, score in sorted(relevant_idx.items(), key=lambda kv: -kv[1])
    ]
    for hit, score in kept:
        hit.fused_score = score

    # Eşiğin üstündekiler başa: en yüksek güvenli parçalar önce.
    state.graded_hits = sorted(kesinler, key=lambda h: -h.dense_score) + [hit for hit, _ in kept]

    # PUANLAYICI TEK KARAR VERİCİ DEĞİL.
    #
    # ÖLÇÜLDÜ (Elektromanyetik Alanlar ders notu, aynı soru üç koşu): 24
    # chunk'ın kâh 24'ü, kâh 9'u, kâh 3'ü "alakalı" bulundu. Karar ucuz bir
    # modelin tek bir JSON çıktısına bakıyor ve o çıktı gürültülü; elenen
    # parçalar cevaptan da eleniyor, yani cevabın uzunluğu ve kapsamı koşudan
    # koşuya değişiyordu.
    #
    # Bu yüzden vektör benzerliği yüksek olan parçalar, puanlayıcı elese bile
    # bağlamda kalıyor. Eşik ölçülerek seçildi: belgede gerçekten karşılığı
    # olan sorularda en iyi parçalar 0.60-0.72, konuyla ilgisiz sorularda
    # 0.35-0.46 veriyor. Puanlayıcının işi sürüyor — sıralamayı ve "hiç alaka
    # yok, sorguyu yeniden yaz" kararını hâlâ o veriyor.
    if kesinler:
        state.note(f"{len(kesinler)} chunk benzerlik eşiğinin üstünde olduğu için "
                   f"puanlayıcıya gönderilmeden bağlama alındı (≥{kesin_esik:.2f})")
    state.note(f"{len(kept)}/{len(candidates)} belirsiz chunk alakalı bulundu")
    return state


def is_sufficient(state: QueryState) -> bool:
    """"Pasaj Alakalı mı?" kararı — Hayır ise rewrite/web yoluna gidilir."""
    return len(state.graded_hits) >= int(config.get("retrieval.min_relevant", 2))


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
