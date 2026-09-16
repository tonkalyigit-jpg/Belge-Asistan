"""Kullanıcı hesapları ve oturumlar.

Şirket içi kurulum: hesapları admin açıyor, kimse kendi kendine kayıt olmuyor.
Parola `scrypt` ile saklanıyor — düz metin hiçbir yerde tutulmuyor, log'a
yazılmıyor, API'den geri dönmüyor.

ERİŞİM KURALI TEK YERDE: kimin hangi belgeyi görebileceğine `gorulebilir_ids`
karar veriyor ve boru hattı da, API de bunu kullanıyor. Kuralı iki yere
yazmak, birini güncelleyip diğerini unutmak demek; bu tür bir sızıntı sessiz
olur ve fark edilmesi aylar alır.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from core import db

# scrypt parametreleri. n=2**14 tek doğrulamada ~50 ms: parola denemesini
# pahalı kılacak kadar yavaş, girişte fark edilmeyecek kadar hızlı.
_N, _R, _P, _UZUNLUK, _TUZ = 2**14, 8, 1, 32, 16


def _hashle(parola: str, tuz: bytes) -> bytes:
    return hashlib.scrypt(parola.encode("utf-8"), salt=tuz, n=_N, r=_R, p=_P,
                          dklen=_UZUNLUK)


def olustur(username: str, parola: str, *, rol: str = "user",
            ad: str = "") -> int:
    """Yeni hesap. Kullanıcı adı tekil; aynı ad varsa `ValueError`."""
    username = username.strip().lower()
    if not username or not parola:
        raise ValueError("Kullanıcı adı ve parola zorunlu")
    if rol not in ("user", "admin"):
        raise ValueError("Rol 'user' ya da 'admin' olmalı")
    tuz = os.urandom(_TUZ)
    conn = db.connect()
    var = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if var:
        raise ValueError(f"'{username}' zaten kayıtlı")
    cur = conn.execute(
        "INSERT INTO users (username, display_name, password_hash, salt, role) "
        "VALUES (?,?,?,?,?)",
        (username, ad or username, _hashle(parola, tuz), tuz, rol),
    )
    conn.commit()
    return int(cur.lastrowid)


def dogrula(username: str, parola: str) -> dict | None:
    """Kullanıcı adı + parola doğruysa hesap, değilse None.

    Kullanıcı yoksa da bir hash hesaplanıyor: "hesap yok" ile "parola yanlış"
    cevapları aynı sürede dönsün. Süre farkı, geçerli kullanıcı adlarını
    dışarıdan taramaya izin verir.
    """
    row = db.connect().execute(
        "SELECT * FROM users WHERE username = ? AND active = 1",
        (username.strip().lower(),),
    ).fetchone()
    if row is None:
        _hashle(parola, b"x" * _TUZ)
        return None
    beklenen = _hashle(parola, row["salt"])
    if not hmac.compare_digest(beklenen, row["password_hash"]):
        return None
    conn = db.connect()
    conn.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (row["id"],))
    conn.commit()
    return _kullanici(row)


def _kullanici(row) -> dict:
    """Dışarıya giden hesap kaydı — parola ve tuz ASLA dahil değil."""
    return {"id": row["id"], "username": row["username"],
            "ad": row["display_name"] or row["username"], "rol": row["role"],
            "aktif": bool(row["active"]), "created_at": row["created_at"],
            "last_login": row["last_login"]}


def getir(user_id: int) -> dict | None:
    row = db.connect().execute(
        "SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)
    ).fetchone()
    return _kullanici(row) if row else None


def listele() -> list[dict]:
    rows = db.connect().execute(
        "SELECT * FROM users ORDER BY role DESC, username"
    ).fetchall()
    return [_kullanici(r) for r in rows]


def sayi() -> int:
    return int(db.connect().execute("SELECT COUNT(*) c FROM users").fetchone()["c"])


def parola_degistir(user_id: int, yeni: str) -> None:
    if not yeni:
        raise ValueError("Parola boş olamaz")
    tuz = os.urandom(_TUZ)
    conn = db.connect()
    conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                 (_hashle(yeni, tuz), tuz, user_id))
    # Parola değişince açık oturumlar kapanıyor: parolayı sıfırlamanın amacı
    # erişimi kesmekse, eski çerezin çalışmaya devam etmesi bunu boşa çıkarır.
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()


def sil(user_id: int) -> None:
    """Hesabı ve ona ait her şeyi siler (belgeler çağıran tarafta siliniyor)."""
    conn = db.connect()
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


def rol_degistir(user_id: int, rol: str) -> None:
    if rol not in ("user", "admin"):
        raise ValueError("Rol 'user' ya da 'admin' olmalı")
    conn = db.connect()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (rol, user_id))
    conn.commit()


def admin_sayisi() -> int:
    return int(db.connect().execute(
        "SELECT COUNT(*) c FROM users WHERE role = 'admin' AND active = 1"
    ).fetchone()["c"])


# --- oturumlar ------------------------------------------------------------


def _damga(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def oturum_ac(user_id: int) -> str:
    """Yeni oturum jetonu. Veritabanında yalnızca sha256'sı duruyor."""
    token = secrets.token_urlsafe(32)
    conn = db.connect()
    conn.execute("INSERT INTO sessions (token_hash, user_id) VALUES (?,?)",
                 (_damga(token), user_id))
    conn.commit()
    return token


def oturum_sahibi(token: str | None) -> dict | None:
    if not token:
        return None
    conn = db.connect()
    row = conn.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND u.active = 1""",
        (_damga(token),),
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE sessions SET last_seen = datetime('now') WHERE token_hash = ?",
                 (_damga(token),))
    conn.commit()
    return _kullanici(row)


def oturum_kapat(token: str | None) -> None:
    if not token:
        return
    conn = db.connect()
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_damga(token),))
    conn.commit()


# --- erişim ---------------------------------------------------------------


def gorulebilir_ids(user_id: int | None) -> list[int]:
    """Bu kullanıcının görebileceği belge kimlikleri.

    Kendi yüklediği belgeler + ortak havuz. Tek yetkili kural burası: arama,
    sınıflandırıcı, atıf ve sayfa görüntüsü hepsi bu listeden geçiyor.
    """
    if user_id is None:
        return []
    rows = db.connect().execute(
        """SELECT id FROM documents
           WHERE status != 'deleted' AND (owner_id = ? OR paylasim = 'ortak')""",
        (user_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def belge_sahibi_mi(user_id: int, document_id: int) -> bool:
    row = db.connect().execute(
        "SELECT 1 FROM documents WHERE id = ? AND owner_id = ?", (document_id, user_id)
    ).fetchone()
    return row is not None


def gorebilir_mi(user_id: int, document_id: int) -> bool:
    row = db.connect().execute(
        """SELECT 1 FROM documents WHERE id = ? AND status != 'deleted'
           AND (owner_id = ? OR paylasim = 'ortak')""",
        (document_id, user_id),
    ).fetchone()
    return row is not None
