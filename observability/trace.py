"""Düğüm bazlı maliyet/gecikme izleme.

`düşük maliyet` hedefi ancak ölçülebilirse savunulabilir; her düğümün ne kadar
token ve süre harcadığı buradan kayda geçer ve UI'da gösterilir.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager

from core import db
from core.state import QueryState


@contextmanager
def step(state: QueryState, name: str, **meta):
    """Bir düğümün süresini ve maliyet deltasını kaydeder.

    Ayrıca düğüm BAŞLARKEN `state.on_step` çağrılıyor (varsa). Arayüz bunu
    kullanarak kullanıcıya nerede olduğunu gösteriyor: "kaynaklar taranıyor"
    gibi tek bir belirsiz mesaj yerine adım adım. Ölçülen sorgular 10-80 saniye
    sürebiliyor ve o süre boyunca ne olduğunu bilmemek, sistemin donduğu
    izlenimi veriyordu.
    """
    if state.on_step:
        try:
            state.on_step(name)
        except Exception:
            pass          # gösterim hatası boru hattını düşürmemeli
    started = time.perf_counter()
    cost_before = state.cost_usd
    tokens_before = state.input_tokens + state.output_tokens
    error: str | None = None
    try:
        yield
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        entry = {
            "node": name,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "cost_usd": round(state.cost_usd - cost_before, 6),
            "tokens": (state.input_tokens + state.output_tokens) - tokens_before,
        }
        if meta:
            entry.update({k: v for k, v in meta.items() if v is not None})
        if error:
            entry["error"] = error
        state.steps.append(entry)


def persist(state: QueryState) -> None:
    """Tamamlanmış bir sorgunun izini veritabanına yazar."""
    conn = db.connect()
    conn.execute(
        """INSERT INTO traces
             (query_raw, lang, category, route, tier_used, cost_usd,
              total_ms, rewrites, regens, steps, user_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            state.query, state.lang, state.category.value, state.route.value,
            state.tier_used, state.cost_usd, state.total_ms,
            state.rewrites, state.regens,
            json.dumps(state.steps, ensure_ascii=False),
            state.user_id,
        ),
    )
    conn.commit()


def kullanici_ozeti() -> list[dict]:
    """Kullanıcı bazında kullanım — admin panelinin istatistiği.

    SORU METNİ DÖNMÜYOR: admin "kim kaç sorgu attı, ne kadar sürdü, ne kadar
    tuttu" görüyor, "ne sordu" görmüyor. Seçilen yetki modeli bu.
    """
    rows = db.connect().execute(
        """SELECT u.id, u.username, u.display_name, u.role, u.last_login,
                  COUNT(t.id) AS sorgu,
                  COALESCE(SUM(t.cost_usd), 0) AS maliyet,
                  COALESCE(AVG(t.total_ms), 0) AS ort_ms,
                  (SELECT COUNT(*) FROM documents d
                    WHERE d.owner_id = u.id AND d.status != 'deleted') AS belge,
                  (SELECT COUNT(*) FROM conversations c WHERE c.owner_id = u.id) AS sohbet
           FROM users u LEFT JOIN traces t ON t.user_id = u.id
           GROUP BY u.id ORDER BY u.role DESC, u.username"""
    ).fetchall()
    return [dict(r) for r in rows]


def summary(user_id: int | None = None) -> dict:
    """Maliyet özeti. `user_id` verilince YALNIZCA o kullanıcının kullanımı.

    Kenar çubuğundaki sayaç kullanıcının kendi harcamasını göstermeli:
    şirketin toplamını herkese göstermek hem gereksiz hem de kaç kişinin
    sistemi kullandığını sızdırır.
    """
    conn = db.connect()
    kosul = " WHERE user_id = ?" if user_id is not None else ""
    parametre = (user_id,) if user_id is not None else ()
    row = conn.execute(
        f"""SELECT COUNT(*) AS queries,
                   COALESCE(SUM(cost_usd), 0)  AS total_cost,
                   COALESCE(AVG(cost_usd), 0)  AS avg_cost,
                   COALESCE(AVG(total_ms), 0)  AS avg_ms
            FROM traces{kosul}""",
        parametre,
    ).fetchone()
    queries = row["queries"] or 0
    by_route = {
        r["route"]: r["c"]
        for r in conn.execute(
            f"SELECT route, COUNT(*) AS c FROM traces{kosul} GROUP BY route", parametre)
    }
    by_tier = {
        r["tier_used"]: r["c"]
        for r in conn.execute(
            f"SELECT tier_used, COUNT(*) AS c FROM traces{kosul} GROUP BY tier_used", parametre)
    }
    return {
        "queries": queries,
        "total_cost": float(row["total_cost"]),
        "avg_cost": float(row["avg_cost"]),
        "avg_ms": float(row["avg_ms"]),
        "by_route": by_route,
        "by_tier": by_tier,
    }
