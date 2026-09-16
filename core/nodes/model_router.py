"""Model Seçici düğümü.

PDF'teki karar: "Kısa Bağlam ve Düşük Skor" -> Ucuz Model,
"Uzun Bağlam veya Yüksek Skor" -> Pahalı Model.

Bağlam uzunluğu tahminden değil, gerçekten retrieve edilmiş metinden
hesaplanır — yanlış tahmin doğrudan gereksiz maliyet demek.
"""
from __future__ import annotations

import config
from core.state import QueryState


def estimate_tokens(text: str) -> int:
    """Kaba token tahmini.

    Sağlayıcıdan bağımsız kalmak için tokenizer'a bağlanmıyoruz. ~4 karakter
    /token İngilizce için iyi bir yaklaşım; Türkçe daha yoğun olduğundan
    kelime sayısı tabanıyla birlikte en büyüğü alınır.
    """
    if not text:
        return 0
    return max(len(text) // 4, int(len(text.split()) * 1.3))


def run(state: QueryState, *, force_tier: str | None = None) -> QueryState:
    """Cevabı hangi kademenin yazacağına karar verir.

    `force_tier` kullanıcının açık tercihi ("hızlı" ya da "derin"); verilmişse
    otomatik karar devre dışı kalıyor. Otomatik yönlendirme çoğu durumda doğru
    seçiyor ama kullanıcı bazen bağlamı biliyor: "bunu hızlı geçmek istiyorum"
    ya da "bu soru önemli, iyi düşün" bilgisi sistemde yok, yalnızca onda var.
    """
    state.context_tokens = estimate_tokens(state.context_text)

    always_strong = set(config.get("routing.always_strong_categories", []))

    # KULLANICI TERCİHİNİN GEÇEMEDİĞİ TEK DURUM.
    #
    # `always_strong_categories` bir tercih değil, bir TABAN: quiz yapılandırılmış
    # JSON üretiyor ve zayıf bir model orada bozuk çıktı veriyor — kullanıcı
    # "hızlı" derken bozuk quiz istemiyor, hızlı quiz istiyor. Hız tercihi bir
    # kalite tercihidir; doğruluk tabanı değil.
    #
    # Yaşandı: iki quiz koşusu `writer` kademesine gitti ve iz "Hızlı yanıt
    # seçildi" dedi, oysa settings.yaml "quiz her zaman güçlü modele gider"
    # diyordu. Kural sessizce yalan söylüyordu.
    if force_tier == "writer" and state.category.value in always_strong:
        state.tier_used = "strong"
        state.note(
            f"Hızlı yanıt istendi ama {state.category.value} güçlü model "
            f"gerektiriyor (yapılandırılmış çıktı)"
        )
        return state

    if force_tier in ("writer", "strong"):
        state.tier_used = force_tier
        state.note(
            "Hızlı yanıt seçildi (kullanıcı tercihi)" if force_tier == "writer"
            else "Derin analiz seçildi (kullanıcı tercihi)"
        )
        return state

    ctx_threshold = int(config.get("routing.context_token_threshold", 2500))
    diff_threshold = int(config.get("routing.difficulty_threshold", 4))

    reasons: list[str] = []
    if state.category.value in always_strong:
        reasons.append(f"kategori={state.category.value}")
    if state.difficulty >= diff_threshold:
        reasons.append(f"zorluk={state.difficulty}>={diff_threshold}")
    if state.context_tokens > ctx_threshold:
        reasons.append(f"bağlam={state.context_tokens}>{ctx_threshold} token")

    if reasons:
        state.tier_used = "strong"
        state.note("Güçlü model seçildi: " + ", ".join(reasons))
    else:
        # `cheap` değil `writer`: bu kademe cevabı ÜRETİYOR, yani çıktısı
        # doğrudan kullanıcıya gidiyor. Kontrol düzlemi kademesi (JSON
        # denetçileri) burada uygun değil.
        state.tier_used = "writer"
        state.note(
            f"Ucuz üretici yeterli: zorluk={state.difficulty}, "
            f"bağlam={state.context_tokens} token"
        )

    return state
