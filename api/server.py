"""Sunucu: HTTP API ve arayüzün kendisi.

Arayüz React ve tarayıcıda çalışıyor; `core.graph`, `belge.ingest`, BGE-M3 ve
SQLite ise burada, Python'da. İkisinin arasındaki sınır bu dosya. (Önceki
Streamlit arayüzü kaldırıldı: arayüzü Python'da olduğu için API'ye ihtiyacı
yoktu, ama iki arayüzü ayakta tutmak aynı veritabanına ve aynı vektör
indeksine yazan iki süreç demekti.)

Derlenmiş React (`web/dist`) varsa aynı sunucudan veriliyor: tek komut, tek
adres, tek süreç. Yoksa API tek başına çalışıyor ve geliştirmede Vite
(5173) ayrı portta koşuyor.

AKIŞ NEDEN SSE
Cevap 5-80 saniye sürüyor ve kullanıcı o süre boyunca hem boru hattının hangi
adımda olduğunu hem de yazılmakta olan metni görmeli. WebSocket'e gerek yok:
trafik tek yönlü (sunucudan tarayıcıya). SSE, `fetch` + `ReadableStream` ile
POST üzerinde de çalışıyor — `EventSource` yalnızca GET desteklediği için o
kullanılmıyor.

TEK SÜREÇ KURALI
Vektör indeksi bellekte tutuluyor ve diske yazılıyor; iki süreç aynı anda
yazarsa satır numaraları çakışır (yaşandı). Aynı anda ikinci bir sunucu
(ikinci bir uvicorn, bir script) belge yüklememeli.
"""
from __future__ import annotations

import io
import json
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
from belge import ingest
from belge import pdf as pdfmod
from core import graph
from llm import registry
from memory import conversations, feedback
from observability import trace

app = FastAPI(title="Belge Asistanı API")

# Geliştirmede React ayrı portta (Vite 5173) koşuyor. Üretimde statik dosyalar
# aynı sunucudan verilirse bu ayar gereksiz kalıyor ama zararı yok: API yerel
# makinede, kimlik doğrulaması olmayan bir demo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ısıtma ---------------------------------------------------------------
#
# BGE-M3'ü ilk sorguda yüklemek 40 saniye sürüyor ve o süre duvar saati
# bütçesini yiyip doğrulama düğümlerini atlatıyordu (Streamlit tarafında
# ölçüldü). Sunucu açılırken arka planda yükleniyor.
_isinma = {"basladi": time.time(), "bitti": False, "hata": ""}


def _isit() -> None:
    try:
        from belge import embedder
        from belge.store import get_store

        embedder.warm()
        get_store().load()
    except Exception as exc:
        _isinma["hata"] = f"{type(exc).__name__}: {exc}"
    finally:
        _isinma["bitti"] = True


@app.on_event("startup")
def _baslat() -> None:
    threading.Thread(target=_isit, daemon=True).start()


def _sse(olay: str, veri: dict) -> str:
    return f"event: {olay}\ndata: {json.dumps(veri, ensure_ascii=False)}\n\n"


# --- durum ----------------------------------------------------------------


@app.get("/api/durum")
def durum() -> dict:
    return {
        "isinma": {
            "bitti": _isinma["bitti"],
            "saniye": round(time.time() - _isinma["basladi"], 1),
            "hata": _isinma["hata"],
        },
        "maliyet": trace.summary(),
        "modeller": registry.describe(),
        "kipler": {
            ad: config.get(f"tiers.{ad}.model", "") for ad in ("writer", "strong")
        },
    }


# --- belgeler -------------------------------------------------------------


@app.get("/api/belgeler")
def belgeler() -> list[dict]:
    return ingest.listele()


_yuklemeler: dict[str, dict] = {}


@app.post("/api/belgeler")
async def belge_yukle(dosya: UploadFile = File(...)) -> dict:
    """Yüklemeyi başlatır, iş kimliği döner. İlerleme SSE ile izleniyor.

    Yükleme 10 saniye ile dakikalar arasında sürüyor (taranmış sayfa başına bir
    OCR çağrısı). İsteği bitene kadar açık tutmak tarayıcı zaman aşımına
    açık; iş arka planda koşuyor ve ilerleme ayrı bir akıştan okunuyor.
    """
    veri = await dosya.read()
    ad = dosya.filename or "belge.pdf"
    is_id = uuid.uuid4().hex[:12]
    olaylar: "queue.Queue[dict]" = queue.Queue()
    _yuklemeler[is_id] = {"olaylar": olaylar, "sonuc": None}

    def calis() -> None:
        def ilerleme(asama: str, i: int, n: int) -> None:
            olaylar.put({"tip": "asama", "asama": asama, "i": i, "n": n})

        try:
            sonuc = ingest.yukle(veri, ad, on_progress=ilerleme)
            _yuklemeler[is_id]["sonuc"] = sonuc
            olaylar.put({
                "tip": "bitti",
                "sonuc": {
                    "belge_id": sonuc.document_id, "durum": sonuc.status,
                    "baslik": sonuc.title, "dosya": sonuc.filename,
                    "sayfa": sonuc.page_count, "ocr_sayfa": sonuc.ocr_pages,
                    "chunk": sonuc.chunks, "ozet": sonuc.summary,
                    "saniye": sonuc.seconds, "uyarilar": sonuc.warnings,
                    "hata": sonuc.error,
                },
            })
        except Exception as exc:
            olaylar.put({"tip": "hata", "mesaj": f"{type(exc).__name__}: {exc}"})

    threading.Thread(target=calis, daemon=True).start()
    return {"is_id": is_id}


@app.get("/api/belgeler/yukleme/{is_id}")
def yukleme_akisi(is_id: str) -> StreamingResponse:
    is_ = _yuklemeler.get(is_id)
    if is_ is None:
        raise HTTPException(404, "Böyle bir yükleme yok")

    def akis():
        while True:
            try:
                olay = is_["olaylar"].get(timeout=120)
            except queue.Empty:
                yield _sse("hata", {"mesaj": "Yükleme zaman aşımına uğradı"})
                return
            yield _sse(olay.pop("tip"), olay)
            if not is_["olaylar"].qsize() and olay.get("sonuc") is not None:
                _yuklemeler.pop(is_id, None)
                return
            if "mesaj" in olay:
                return

    return StreamingResponse(akis(), media_type="text/event-stream")


@app.delete("/api/belgeler/{belge_id}")
def belge_sil(belge_id: int) -> dict:
    ingest.sil(belge_id)
    return {"silindi": belge_id}


@app.get("/api/belgeler/{belge_id}/sayfa/{sayfa_no}")
def belge_sayfasi(belge_id: int, sayfa_no: int) -> Response:
    """Sayfanın PNG görüntüsü — cevabın dayandığı kâğıdın kendisi."""
    veri = ingest.pdf_bytes(belge_id)
    if not veri:
        raise HTTPException(404, "Belge dosyası bulunamadı")
    try:
        gorsel = pdfmod.render_page(veri, sayfa_no, dpi=110)
    except Exception as exc:
        raise HTTPException(400, f"Sayfa açılamadı: {exc}") from exc
    tampon = io.BytesIO()
    gorsel.save(tampon, format="PNG")
    # Sayfa görüntüsü değişmiyor; tarayıcı önbelleğe alsın.
    return Response(tampon.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


# --- sohbetler ------------------------------------------------------------


@app.get("/api/sohbetler")
def sohbetler() -> list[dict]:
    return conversations.list_all()


@app.post("/api/sohbetler")
def sohbet_ac() -> dict:
    return {"id": conversations.create()}


@app.get("/api/sohbetler/{sohbet_id}")
def sohbet(sohbet_id: int) -> dict:
    return {"id": sohbet_id, "mesajlar": conversations.load(sohbet_id)}


@app.delete("/api/sohbetler/{sohbet_id}")
def sohbet_sil(sohbet_id: int) -> dict:
    conversations.delete(sohbet_id)
    return {"silindi": sohbet_id}


# --- soru -----------------------------------------------------------------


@app.post("/api/sor")
async def sor(istek: Request) -> StreamingResponse:
    """Soruyu çalıştırır; adımları ve metni akıtır.

    Boru hattı ayrı bir iş parçacığında koşuyor, olaylar kuyruğa düşüyor ve
    burada SSE'ye çevriliyor. Streamlit tarafındaki akış da aynı desende:
    üretim ile gösterim ayrı, çünkü sağlayıcı metni düzensiz aralıklarla
    gönderiyor.
    """
    govde = await istek.json()
    soru = (govde.get("soru") or "").strip()
    sohbet_id = govde.get("sohbet_id")
    kip = govde.get("kip") or "writer"
    odak = govde.get("odak_belge_id")
    yeniden = bool(govde.get("yeniden"))
    if not soru:
        raise HTTPException(400, "Soru boş")
    # SOHBET HÂLÂ VAR MI? Tarayıcı elindeki kimliği gönderiyor ve o sohbet bu
    # arada silinmiş olabilir (başka sekme, Streamlit arayüzü, temizlik).
    # Yoksa mesaj eklemek FOREIGN KEY hatasıyla düşüyordu — canlıda yaşandı.
    if sohbet_id is not None and not conversations.var_mi(int(sohbet_id)):
        sohbet_id = None
    if sohbet_id is None:
        sohbet_id = conversations.create()

    gecmis = [
        {"role": m["role"], "content": m["content"]}
        for m in conversations.load(sohbet_id) if m.get("content")
    ]
    if yeniden:
        # Yeniden üretimde son soru geçmişin PARÇASI DEĞİL: kendisi soruluyor.
        # Son kullanıcı mesajından sonrası atılıyor.
        son_soru = next((i for i in range(len(gecmis) - 1, -1, -1)
                         if gecmis[i]["role"] == "user"), None)
        gecmis = gecmis[:son_soru] if son_soru is not None else gecmis
    else:
        mesaj = {"role": "user", "content": soru}
        conversations.append(sohbet_id, mesaj)

    parcalar: "queue.Queue[str]" = queue.Queue()
    adimlar: "queue.Queue[str]" = queue.Queue()
    bitti = threading.Event()
    sonuc: dict = {}

    def calis() -> None:
        try:
            sonuc["durum"] = graph.run(
                soru, on_delta=parcalar.put, history=gecmis, regenerate=yeniden,
                on_step=adimlar.put, force_tier=kip,
                scope_document_ids=[odak] if odak else None,
            )
        except Exception as exc:
            sonuc["hata"] = f"{type(exc).__name__}: {exc}"
        finally:
            bitti.set()

    threading.Thread(target=calis, daemon=True).start()

    def akis():
        yield _sse("sohbet", {"sohbet_id": sohbet_id})
        while True:
            bos = True
            try:
                while True:
                    yield _sse("adim", {"ad": adimlar.get_nowait()})
                    bos = False
            except queue.Empty:
                pass
            try:
                while True:
                    yield _sse("parca", {"metin": parcalar.get_nowait()})
                    bos = False
            except queue.Empty:
                pass
            if bitti.is_set() and parcalar.empty() and adimlar.empty():
                break
            if bos:
                time.sleep(0.05)

        if "hata" in sonuc:
            yield _sse("hata", {"mesaj": sonuc["hata"]})
            return

        durum = sonuc["durum"]
        mesaj = {"role": "assistant", "content": durum.answer, "result": durum.to_dict()}
        if yeniden:
            son_soru = conversations.message_count(sohbet_id) - 2
            conversations.replace_after(sohbet_id, max(son_soru, 0), mesaj)
        else:
            conversations.append(sohbet_id, mesaj)
            if conversations.message_count(sohbet_id) == 2:
                conversations.generate_title(sohbet_id, soru, durum.answer)
        yield _sse("son", {"cevap": durum.answer, "sonuc": durum.to_dict(),
                           "sohbet_id": sohbet_id})

    return StreamingResponse(akis(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# --- geri bildirim --------------------------------------------------------


@app.post("/api/oy")
async def oy(istek: Request) -> dict:
    govde = await istek.json()
    feedback.record(
        govde.get("soru", ""),
        vote=int(govde.get("yon", 0)),
        lang=govde.get("dil", "tr"),
        predicted_category=govde.get("kategori"),
    )
    sohbet_id, sira, etiket = govde.get("sohbet_id"), govde.get("sira"), govde.get("etiket", "")
    if sohbet_id is not None and sira is not None:
        conversations.set_vote(int(sohbet_id), int(sira), etiket)
    return {"kaydedildi": True}


# --- arayüz ---------------------------------------------------------------
#
# Bu blok dosyanın SONUNDA: FastAPI yolları tanımlanma sırasına göre
# eşleştiriyor ve kökü (`/`) burada bağlıyoruz. Yukarıda kalsaydı `/api/...`
# istekleri de statik dosya arayışına düşerdi.
_DAGITIM = Path(__file__).resolve().parent.parent / "web" / "dist"

if _DAGITIM.exists():
    app.mount("/assets", StaticFiles(directory=_DAGITIM / "assets"), name="assets")

    @app.get("/{yol:path}")
    def arayuz(yol: str) -> Response:
        """Derlenmiş React. Bilinmeyen yol index.html'e düşüyor (SPA)."""
        dosya = _DAGITIM / yol
        if yol and dosya.is_file():
            return FileResponse(dosya)
        return FileResponse(_DAGITIM / "index.html")
