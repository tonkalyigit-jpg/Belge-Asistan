"""Kalıcı sohbet geçmişi.

Streamlit'in `session_state`'i tarayıcı bağlantısına bağlı: bağlantı düşerse
yeni oturum açılıyor ve sohbet kayboluyor. Bu modül geçmişi SQLite'a alıyor,
yani tarayıcı kapansa da, uygulama yeniden başlasa da duruyor.

Başlık iki aşamalı: ilk soru geldiğinde hemen kesilmiş hâli yazılıyor (liste
boş kalmasın), ilk cevap tamamlanınca ucuz kademe onu 2-5 kelimelik bir konu
özetine çeviriyor. Ucuz kademe ücretsiz katmanda olduğu için bunun maliyeti
sıfır; gecikmesi de sohbet başına bir kez ~1 saniye.
"""
from __future__ import annotations

import json

from core import db

_TITLE_CHARS = 60


def create(title: str = "") -> int:
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO conversations (title) VALUES (?)", (title or "Yeni sohbet",)
    )
    conn.commit()
    return int(cur.lastrowid)


def var_mi(conversation_id: int) -> bool:
    """Sohbet duruyor mu — silinmiş bir kimliğe mesaj yazmayı önlemek için."""
    row = db.connect().execute(
        "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    return row is not None


def list_all(limit: int = 60) -> list[dict]:
    """En son konuşulan üstte."""
    rows = db.connect().execute(
        """SELECT c.id, c.title, c.updated_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n
           FROM conversations c
           WHERE n > 0
           ORDER BY c.updated_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def load(conversation_id: int) -> list[dict]:
    """Mesajları app.py'nin beklediği biçimde döner."""
    rows = db.connect().execute(
        "SELECT role, content, result, voted FROM messages "
        "WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    out = []
    for r in rows:
        message = {"role": r["role"], "content": r["content"]}
        if r["result"]:
            try:
                message["result"] = json.loads(r["result"])
            except json.JSONDecodeError:
                pass          # bozuk kayıt sohbeti düşürmemeli
        if r["voted"]:
            message["voted"] = r["voted"]
        out.append(message)
    return out


def append(conversation_id: int, message: dict) -> None:
    conn = db.connect()
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, result, voted) "
        "VALUES (?,?,?,?,?)",
        (
            conversation_id,
            message.get("role", "user"),
            message.get("content") or "",
            json.dumps(message["result"], ensure_ascii=False)
            if message.get("result") else None,
            message.get("voted"),
        ),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
        (conversation_id,),
    )
    conn.commit()

    # İlk kullanıcı mesajı başlığı belirler.
    if message.get("role") == "user":
        row = conn.execute(
            "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row and row["title"] in ("", "Yeni sohbet"):
            rename(conversation_id, _title_from(message.get("content") or ""))


def set_vote(conversation_id: int, index: int, label: str) -> None:
    """`index`, sohbetteki mesaj sırası (0 tabanlı)."""
    conn = db.connect()
    rows = conn.execute(
        "SELECT id FROM messages WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    if 0 <= index < len(rows):
        conn.execute("UPDATE messages SET voted = ? WHERE id = ?", (label, rows[index]["id"]))
        conn.commit()


def rename(conversation_id: int, title: str) -> None:
    conn = db.connect()
    conn.execute(
        "UPDATE conversations SET title = ? WHERE id = ?",
        (title or "Yeni sohbet", conversation_id),
    )
    conn.commit()


def delete(conversation_id: int) -> None:
    conn = db.connect()
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()


def _title_from(question: str) -> str:
    text = " ".join((question or "").split())
    if len(text) <= _TITLE_CHARS:
        return text or "Yeni sohbet"
    # Kelime ortasından kesme: başlık listede okunacak, yarım kelime kötü durur.
    cut = text[:_TITLE_CHARS].rsplit(" ", 1)[0]
    return (cut or text[:_TITLE_CHARS]) + "…"


def replace_after(conversation_id: int, index: int, message: dict) -> None:
    """`index`'ten sonraki mesajları silip yerine tek mesaj koyar.

    "Yeniden üret" için: eski cevap kalıcı kayıttan da düşmeli, yoksa sohbeti
    tekrar açtığında iki cevap üst üste görünür ve hangisinin geçerli olduğu
    belirsizleşir.
    """
    conn = db.connect()
    rows = conn.execute(
        "SELECT id FROM messages WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    for row in rows[index + 1:]:
        conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
    conn.commit()
    append(conversation_id, message)


def generate_title(conversation_id: int, question: str, answer: str) -> str | None:
    """İlk alışverişten kısa bir konu başlığı üretir ve kaydeder.

    Ham soru başlık olarak kötü çalışıyor: kenar çubuğu dar ve sorular uzun,
    dolayısıyla liste yarım cümlelerle doluyor ve hiçbiri tanınmıyor. Konu
    özeti ("BGP kaçırma savunmaları") bir bakışta seçilebiliyor.

    Başarısız olursa `None` dönüyor ve mevcut başlık (kesilmiş soru) kalıyor —
    başlık üretemedik diye sohbet kaybolmamalı.
    """
    import config
    from llm import registry

    try:
        data, _ = registry.tier("cheap").complete_json(
            system=config.prompt("title"),
            user=f"Question: {question}\n\nAnswer: {answer[:800]}",
            max_tokens=120,
            cache_system=True,
        )
    except Exception:
        return None

    title = " ".join(str(data.get("title") or "").split())[:_TITLE_CHARS]
    if not title:
        return None
    rename(conversation_id, title)
    return title


def message_count(conversation_id: int) -> int:
    row = db.connect().execute(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return int(row["n"]) if row else 0
