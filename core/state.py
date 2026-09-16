"""Akış boyunca taşınan durum nesnesi.

Her düğüm QueryState alır, alanlarını doldurur, aynı nesneyi döndürür.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class Category(str, Enum):
    BELGE_ICI = "belge_ici"            # tek belgeden cevaplanan soru -> RAG
    CAPRAZ_BELGE = "capraz_belge"      # belgeleri karşılaştır / birleştir -> belge başına RAG
    BELGE_OZETI = "belge_ozeti"        # "şu belgeyi özetle", "neler yüklü" -> saklı özet
    KAPSAM_DISI = "kapsam_disi"        # belgelerle ilgisiz -> kibar ret

    @classmethod
    def parse(cls, value: str | None) -> "Category":
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            # Beklenmeyen etiket gelirse en güvenli yol belgelerde aramak:
            # gereksiz ret vermektense biraz fazla harcarız.
            return cls.BELGE_ICI


class Route(str, Enum):
    REFUSAL = "refusal"
    OZET = "ozet"          # saklı belge özeti, üretim yok
    RAG = "rag"


@dataclass
class QueryState:
    # --- girdi ---
    query: str
    # Düğüm başlarken çağrılan geri çağrı (adı alır). Arayüz ilerlemeyi
    # buradan gösteriyor; boru hattı bunu bilmek zorunda değil, yalnızca
    # varsa haber veriyor.
    on_step: object = None
    # Önceki tur(lar): [{"role": "user"|"assistant", "content": str}]. Takip
    # sorularını çözmek için gerekli — "peki bunun dezavantajı ne?" sorusunun
    # neye işaret ettiği yalnızca burada yazılı.
    history: list[dict] = field(default_factory=list)
    ui_lang: str = "auto"                 # "auto" | "tr" | "en"

    # --- sınıflandırma ---
    lang: str = "tr"                      # cevabın yazılacağı dil
    category: Category = Category.BELGE_ICI
    difficulty: int = 3                   # 1-5
    # Hız sınırı yüzünden beklenen toplam süre (saniye). Maliyet değil ama
    # kullanıcının hissettiği gecikmenin büyük kısmı burada olabiliyor.
    rate_limit_wait_s: float = 0.0
    # Takip sorusunun kendi başına anlaşılır hâli. Sınıflandırıcı üretiyor;
    # "peki cezası ne?" -> "Güney sözleşmesindeki gecikme cezası nedir?".
    # Retrieval ve üretim bunu kullanıyor: "peki cezası ne?" ham hâliyle
    # neyin sorulduğunu söylemiyor.
    standalone_query: str = ""
    # Arama sorgusu. Network Asistanı'nda İngilizceye çevriliyordu; burada
    # belgeler de Türkçe, sınıflandırıcı yalnızca soruyu arama için sadeleştiriyor.
    search_query: str = ""
    # Sorunun adıyla andığı belgeler ("Güney sözleşmesi", "yönetmelik").
    # Sınıflandırıcı adı çıkarıyor, graph onu belge kimliğine çözüyor.
    document_refs: list[str] = field(default_factory=list)
    document_ids: list[int] = field(default_factory=list)
    # Kullanıcının sohbeti ODAKLADIĞI belge (kenar çubuğundan "bu belgeye
    # sor"). Sınıflandırıcının `document_ids` tahmininden farklı olarak bu
    # bir karar değil talimat: arama yalnızca bu belgede yapılıyor.
    scope_document_ids: list[int] = field(default_factory=list)
    scope_titles: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    # --- retrieval ---
    hits: list[Any] = field(default_factory=list)          # belge.store.Hit
    graded_hits: list[Any] = field(default_factory=list)   # alakalı bulunanlar
    context_text: str = ""
    context_tokens: int = 0
    # Çapraz sentezde karşılaştırılan belgeler bağlama sığdıysa arama yerine
    # TAMAMI okunuyor; o durumda puanlayıcı atlanıyor (bkz. retrieve._capraz).
    full_context: bool = False

    # --- üretim ---
    route: Route = Route.RAG
    tier_used: str = "cheap"
    model_used: str = ""
    answer: str = ""
    citations: list[dict] = field(default_factory=list)

    # --- doğrulama ---
    grounded: bool | None = None
    sufficient: bool | None = None
    unsupported_claims: list[str] = field(default_factory=list)
    low_confidence: bool = False
    # Model servisi cevap veremedi (kota, zaman aşımı). Doluysa üretilen
    # "cevap" bir hata metnidir ve denetim döngüsüne sokulmamalı.
    service_error: str = ""

    # --- döngü sayaçları (graph.py zorlar) ---
    rewrites: int = 0
    regens: int = 0


    # --- muhasebe ---
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    steps: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_ms: float = 0.0

    # --- yeniden hesaplanmasın diye taşınan vektör ---
    query_vector: np.ndarray | None = field(default=None, repr=False)

    def add_cost(self, response) -> None:
        """Bir LLMResponse'un maliyetini duruma ekler."""
        self.cost_usd += response.cost_usd
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens

        # Hız sınırı beklemesi sessiz kalmamalı: kullanıcı açısından bu süre
        # "model düşünüyor"dan ayırt edilemiyor ve sistem donmuş gibi görünüyor.
        wait = getattr(response, "retry_wait_s", 0.0)
        if wait:
            self.rate_limit_wait_s += wait
            self.note(
                f"Sağlayıcı hız sınırı: {wait:.0f} sn beklendi "
                f"({getattr(response, 'retries', 0)} yeniden deneme)"
            )

    def note(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "standalone_query": self.standalone_query,
            "lang": self.lang,
            "category": self.category.value,
            "difficulty": self.difficulty,
            "route": self.route.value,
            "tier_used": self.tier_used,
            "model_used": self.model_used,
            "answer": self.answer,
            "citations": self.citations,
            "cost_usd": round(self.cost_usd, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "rewrites": self.rewrites,
            "regens": self.regens,
            "grounded": self.grounded,
            "sufficient": self.sufficient,
            "low_confidence": self.low_confidence,
            "warnings": self.warnings,
            "rate_limit_wait_s": self.rate_limit_wait_s,
            "document_ids": self.document_ids,
            "scope_document_ids": self.scope_document_ids,
            "scope_titles": self.scope_titles,
            "full_context": self.full_context,
            "steps": self.steps,
            "total_ms": round(self.total_ms, 1),
        }
