"""Geri bildirim kaydı ve few-shot düzeltme havuzu.

Oylar sınıflandırıcı prompt'una eklenen few-shot örneklerini besliyor. Bu,
sistemin fine-tuning olmadan hatalarından öğrenmesini sağlar: 👎 alan
bir sorgu düzeltilmiş kategorisiyle havuza girer, benzer bir sorgu geldiğinde
sınıflandırıcıya örnek olarak gösterilir.
"""
from __future__ import annotations

import numpy as np

import config
from core import db
from belge import embedder


def record(
    query: str,
    *,
    vote: int,
    lang: str = "tr",
    reason: str | None = None,
    predicted_category: str | None = None,
    corrected_category: str | None = None,
    query_vector: np.ndarray | None = None,
) -> int:
    """Oyu kaydeder; sınıflandırıcının örnek havuzuna girer."""
    if query_vector is None:
        query_vector = embedder.encode_one(query, is_query=True)

    conn = db.connect()
    cur = conn.execute(
        """INSERT INTO feedback
             (query_raw, lang, vote, reason,
              predicted_category, corrected_category, embedding)
           VALUES (?,?,?,?,?,?,?)""",
        (
            query, lang, 1 if vote > 0 else -1, reason,
            predicted_category, corrected_category,
            np.asarray(query_vector, dtype=np.float32).tobytes(),
        ),
    )
    conn.commit()

    return cur.lastrowid


def fewshot_examples(
    query: str, *, query_vector: np.ndarray | None = None, limit: int | None = None
) -> list[dict]:
    """Sorguya en benzer geri bildirim örneklerini döner.

    Yalnızca öğretici olanlar alınır: 👎 + düzeltilmiş kategori (yanlış karar
    örneği) veya 👍 + tahmin edilmiş kategori (doğrulanmış karar örneği).
    """
    limit = limit or int(config.get("feedback.fewshot_pool_size", 5))
    min_sim = float(config.get("feedback.fewshot_min_similarity", 0.55))

    rows = db.connect().execute(
        """SELECT query_raw, vote, reason, predicted_category, corrected_category, embedding
           FROM feedback
           WHERE (vote = -1 AND corrected_category IS NOT NULL)
              OR (vote =  1 AND predicted_category IS NOT NULL)
           ORDER BY id DESC LIMIT 200"""
    ).fetchall()
    if not rows:
        return []

    if query_vector is None:
        query_vector = embedder.encode_one(query, is_query=True)

    matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    sims = embedder.cosine(query_vector, matrix)

    scored = [
        (float(sim), row) for row, sim in zip(rows, sims) if float(sim) >= min_sim
    ]
    scored.sort(key=lambda pair: -pair[0])

    return [
        {
            "query": row["query_raw"],
            "category": row["corrected_category"] or row["predicted_category"],
            "was_wrong": row["vote"] < 0,
            "reason": row["reason"],
            "similarity": round(sim, 3),
        }
        for sim, row in scored[:limit]
    ]


def render_fewshot(examples: list[dict]) -> str:
    """Few-shot örneklerini prompt'a eklenecek metne çevirir.

    Bu blok bilinçli olarak SİSTEM prompt'una değil kullanıcı mesajına eklenir:
    dinamik içerik sistem prompt'unda olsaydı prompt-cache prefix'i her sorguda
    değişir ve cache tamamen boşa giderdi.
    """
    if not examples:
        return ""
    lines = [
        "",
        "Learned examples from past user feedback "
        "(these corrections take precedence over your prior):",
    ]
    for ex in examples:
        marker = "PREVIOUSLY MISCLASSIFIED" if ex["was_wrong"] else "CONFIRMED"
        line = f'- [{marker}] "{ex["query"]}" -> category: {ex["category"]}'
        if ex.get("reason"):
            line += f" (user note: {ex['reason']})"
        lines.append(line)
    return "\n".join(lines)


def stats() -> dict:
    row = db.connect().execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(vote = 1), 0)  AS up,
                  COALESCE(SUM(vote = -1), 0) AS down,
                  COALESCE(SUM(corrected_category IS NOT NULL), 0) AS corrections
           FROM feedback"""
    ).fetchone()
    return {
        "total": row["total"],
        "up": row["up"],
        "down": row["down"],
        "corrections": row["corrections"],
    }
