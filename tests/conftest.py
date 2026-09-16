"""Test altyapısı: izole DB/indeks + sahte LLM + sahte embedding.

Testlerin hiçbiri gerçek LLM çağrısı yapmıyor; embedding `fake_embedder` ile
deterministik (BGE-M3 indirmesi 2 GB'lık bir maliyet). PDF'ler ise GERÇEK:
tests/ornek_metinler.py'deki metinlerden her test için yeniden üretiliyor,
çünkü PDF okuma katmanını sahtelemek tam da test edilmesi gereken şeyi
atlamak olurdu.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from llm.base import LLMProvider, LLMResponse, TierConfig  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path):
    """Her test kendi DB'si, indeksi ve belge dizini ile çalışır."""
    settings = config.load()
    original_paths = dict(settings["paths"])
    settings["paths"]["db"] = str(tmp_path / "test.db")
    settings["paths"]["index_dir"] = str(tmp_path / "index")
    settings["paths"]["belgeler_dir"] = str(tmp_path / "belgeler")

    from belge import store
    from core import db

    db.close()
    store.reset()
    yield tmp_path
    db.close()
    store.reset()
    settings["paths"] = original_paths


@pytest.fixture
def fake_embedder(monkeypatch):
    """Deterministik, kelime tabanlı sahte embedding.

    Ortak kelime paylaşan metinler benzer çıkıyor. Skor ölçeği BGE-M3'ünkiyle
    aynı değil, bu yüzden `retrieval.score_floor` sıfırlanıyor — gerçek modele
    göre kalibre edilmiş bir taban sahte skorları eleyip retrieval'la ilgisi
    olmayan testleri düşürürdü (Network Asistanı'nda yaşandı).
    """
    from belge import embedder

    monkeypatch.setitem(config.load()["retrieval"], "score_floor", 0.0)
    dim = 64

    def _vec(text: str) -> np.ndarray:
        acc = np.zeros(dim, dtype=np.float32)
        for word in (text.lower().split() or [text.lower()]):
            rng = np.random.default_rng(abs(hash(word)) % (2**32))
            acc += rng.standard_normal(dim).astype(np.float32)
        norm = np.linalg.norm(acc)
        return acc / norm if norm > 0 else acc

    def encode(texts, *, is_query=False):
        if not texts:
            return np.zeros((0, dim), dtype=np.float32)
        return np.vstack([_vec(t) for t in texts]).astype(np.float32)

    monkeypatch.setattr(embedder, "encode", encode)
    monkeypatch.setattr(embedder, "encode_one", lambda text, *, is_query=True: _vec(text))
    monkeypatch.setattr(embedder, "dim", lambda: dim)
    return dim


class FakeProvider(LLMProvider):
    """Düğüm bazlı yanıt veren sahte sağlayıcı.

    `responses` sözlüğünün anahtarı bir PROMPT ADI ("classifier", "ocr"):
    sistem prompt'u o dosyanın metniyle birebir eşleşince değer döner.
    Prompt CÜMLELERİNE bağlanmıyoruz — prompt'lar sürekli yeniden yazılıyor ve
    bir kelime değişince ilgisiz testlerin kırılması yanlış (yaşandı).

    Değer liste ise her çağrıda sıradaki döner, sonuncusu tekrarlanır.
    Değer çağrılabilir ise (system, user) ile çağrılır. İstisna ise fırlatılır.
    """

    def __init__(self, tier: TierConfig, responses: dict):
        super().__init__(tier)
        self.responses = responses
        self.calls: list[dict] = []

    def _yanit(self, system: str, user: str):
        for key, value in self.responses.items():
            # `startswith`, eşitlik değil: complete_json sistem prompt'unun SONUNA
            # "yalnızca JSON dön" talimatı ekliyor. Eşitlik aranınca JSON dönen
            # her düğüm (sınıflandırıcı, puanlayıcı, denetçiler) eşleşmiyordu.
            try:
                if not system.startswith(config.prompt(key)):
                    continue
            except FileNotFoundError:
                continue
            if isinstance(value, list):
                value = value.pop(0) if len(value) > 1 else value[0]
            if isinstance(value, Exception):
                raise value
            return value(system, user) if callable(value) else value
        return "{}"

    def complete(self, system, user, *, max_tokens=None, stop=None,
                 cache_system=False, temperature=None, images=None):
        self.calls.append({"system": system, "user": user, "images": images})
        text = self._yanit(system, user)
        return LLMResponse(text=text, model=self.tier.model, provider="fake",
                           input_tokens=100, output_tokens=50,
                           cost_usd=self.tier.cost(100, 50))

    def complete_vision(self, system, user, images, *, max_tokens=None):
        return self.complete(system, user, max_tokens=max_tokens, images=images)


@pytest.fixture
def fake_llm(monkeypatch):
    """`registry.tier` çağrılarını FakeProvider'a yönlendirir."""
    from llm import registry

    state = {"responses": {}}
    providers: dict[str, FakeProvider] = {}

    def fake_tier(name: str):
        if name not in providers:
            providers[name] = FakeProvider(registry.tier_config(name), state["responses"])
        return providers[name]

    monkeypatch.setattr(registry, "tier", fake_tier)

    def configure(responses: dict):
        state["responses"] = responses
        providers.clear()
        return providers

    return configure


@pytest.fixture(scope="session")
def ornek_pdfler(tmp_path_factory):
    """Sentetik belgelerin dijital ve taranmış PDF'leri (oturum başına bir kez)."""
    from scripts.ornek_pdf_uret import dijital, temiz_tarama
    from tests.ornek_metinler import SOZLESME_A, SOZLESME_B, YONETMELIK

    out = {}
    for ad, belge in [("kuzey", SOZLESME_A), ("guney", SOZLESME_B), ("yonetmelik", YONETMELIK)]:
        d = dijital(belge)
        out[ad] = d
        out[f"{ad}_tarama"] = temiz_tarama(d)
    return out
