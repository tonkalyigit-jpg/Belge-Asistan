"""BGE-M3 embedding sarmalayıcısı.

Lokal ve ücretsiz; TR-EN çapraz dil aramasını doğrudan destekliyor (kullanıcı
Türkçe sorar, indeks İngilizce arXiv abstract'larıdır).

Model yüklemesi pahalı (~2GB, ilk çağrıda ~10sn) olduğu için süreç boyunca
tek örnek tutulur ve yükleme tembel (lazy) yapılır.
"""
from __future__ import annotations

import threading

import numpy as np

import config

_lock = threading.Lock()

# Çıkarım (inference) kilidi model kurulum kilidinden AYRI olmalı: encode()
# içinde _get_model() çağrılıyor ve threading.Lock yeniden girişli değil —
# tek kilit kullanılsa kilitlenme (deadlock) olurdu.
#
# NEDEN VAR: PyTorch'un MPS arka ucuna birden çok iş parçacığından aynı anda
# girilince süreç segmentation fault ile ölüyor. Gözlemlendi (2026-08-31,
# çökme raporu Python-2026-08-31-165045.ips):
#     EXC_BAD_ACCESS (SIGSEGV)
#       at::native::mps::copy_cast_kernel_mps
#       at::native::mps::mps_copy_
#     iş parçacığı #6 — ana iş parçacığı değil
# Streamlit her kullanıcı etkileşiminde script'i yeniden koşturuyor ve bunu
# ayrı bir iş parçacığında yapıyor; önceki sorgunun embedding'i sürerken yeni
# bir istek gelirse iki iş parçacığı aynı anda GPU'ya giriyordu. Uygulama
# hatasız, izsiz ölüyordu — Python seviyesinde istisna değil, çökme.
#
# Seri hale getirmenin bedeli pratikte yok: embedding zaten boru hattının
# darboğazı ve sistem tek kullanıcılı.
_infer_lock = threading.Lock()
_model = None


def _resolve_device(requested: str) -> str:
    if requested and requested != "auto":
        return requested
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _resolve_dtype(requested: str, device: str):
    """GPU'da fp16 kullanır.

    Ölçüm (Apple Silicon MPS, BGE-M3): fp16 %26 daha hızlı ve ürettiği vektörün
    fp32 ile cosine benzerliği 0.99984 — retrieval açısından aynı vektör. CPU'da
    fp16 desteklenmiyor/yavaş olduğu için kapalı.

    Vektörler diskte her hâlükârda float32 saklanıyor (bkz. `encode`), yani
    daha önce fp32 ile indekslenmiş parçalarla karışık kullanım güvenli.
    """
    if requested not in ("auto", "float16", "fp16"):
        return None
    if requested == "auto" and device not in ("cuda", "mps"):
        return None
    try:
        import torch
    except ImportError:
        return None
    return torch.float16


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                name = config.get("embedding.model", "BAAI/bge-m3")
                device = _resolve_device(config.get("embedding.device", "auto"))
                kwargs = {}
                # Model diskte varken bile sentence-transformers HuggingFace
                # Hub'a "güncelleme var mı" diye sorup ağı bekliyor. Yavaş bir
                # bağlantıda bu tek başına onlarca saniye: ölçüldü, mobil
                # hotspot üzerinde soğuk yükleme 13 sn yerine 41.6 sn sürdü ve
                # sorgunun 90 sn'lik duvar saati bütçesinin yarısını yedi.
                # İndirme zaten bir kez yapıldı; her açılışta doğrulamaya gerek
                # yok. Model yerelde yoksa `local_files_only` hata verir, o
                # yüzden ilk kurulumda ağa izin verilip tekrar denenir.
                if config.get("embedding.local_files_only", True):
                    kwargs["local_files_only"] = True
                dtype = _resolve_dtype(config.get("embedding.dtype", "auto"), device)
                if dtype is not None:
                    kwargs["model_kwargs"] = {"torch_dtype": dtype}
                try:
                    model = SentenceTransformer(name, device=device, **kwargs)
                except Exception:
                    # İlk kurulum: model henüz indirilmemiş. Ağa izin ver.
                    kwargs.pop("local_files_only", None)
                    model = SentenceTransformer(name, device=device, **kwargs)
                max_len = config.get("embedding.max_seq_length")
                if max_len:
                    model.max_seq_length = int(max_len)
                _model = model
    return _model


def warm() -> None:
    """Modeli önceden yükler. Uygulama açılışında arka planda çağrılır.

    Bedel kaybolmuyor, YERİ değişiyor: kullanıcının ilk sorusuna değil,
    uygulamanın açılışına yazılıyor. Ölçüldü — bu yükleme ilk sorgunun 102.4
    saniyesinin 41.6'sıydı ve boru hattını `max_wall_seconds` limitinin üstüne
    çıkarıp doğrulama adımlarının atlanmasına yol açtı.
    """
    encode_one("warm", is_query=True)


def dim() -> int:
    return int(config.get("embedding.dim", 1024))


def is_loaded() -> bool:
    return _model is not None


def encode(texts: list[str], *, is_query: bool = False) -> np.ndarray:
    """Metinleri L2-normalize edilmiş float32 vektörlere çevirir.

    Normalize edildiği için cosine benzerliği = nokta çarpımı.
    """
    if not texts:
        return np.zeros((0, dim()), dtype=np.float32)

    prefix = config.get("embedding.query_prefix", "") if is_query else ""
    payload = [prefix + t for t in texts] if prefix else texts

    # Model kurulumu kilidin DIŞINDA: _get_model() kendi kilidini kullanıyor.
    model = _get_model()
    with _infer_lock:
        vectors = model.encode(
            payload,
            batch_size=int(config.get("embedding.batch_size", 16)),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    return np.asarray(vectors, dtype=np.float32)


def encode_one(text: str, *, is_query: bool = True) -> np.ndarray:
    return encode([text], is_query=is_query)[0]


def cosine(vector: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Tek vektöre karşı matrisin satır bazlı cosine benzerliği.

    Her iki taraf da normalize varsayılır; boş matriste boş dizi döner.
    """
    if matrix.size == 0:
        return np.zeros((0,), dtype=np.float32)
    return matrix @ vector.astype(np.float32)
