"""Paylaşılan SQLite bağlantısı ve şema.

Hem belge indeksinin metadata'sı (belge/store.py) hem sohbet/geri bildirim
(memory/*) aynı dosyayı kullanır — tek dosya yedeklemek yeterli olsun diye.
"""
from __future__ import annotations

import sqlite3
import threading

import config

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Yüklenen her PDF. Aynı dosya iki kez yüklenirse ikinci kayıt açılmıyor:
-- sha256 tekil. Ad değil içerik belirleyici, çünkü aynı evrak farklı adlarla
-- ("sozlesme_son.pdf", "sozlesme_son_v2.pdf") tekrar yüklenebiliyor.
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    sha256       TEXT UNIQUE NOT NULL,
    filename     TEXT NOT NULL,
    title        TEXT,                    -- özetleyicinin çıkardığı başlık
    page_count   INTEGER NOT NULL DEFAULT 0,
    ocr_pages    INTEGER NOT NULL DEFAULT 0,  -- kaç sayfa taramadan okundu
    -- processing | ready | failed
    status       TEXT NOT NULL DEFAULT 'processing',
    error        TEXT,
    summary      TEXT,                    -- yükleme anında üretilen belge özeti
    added_at     TEXT NOT NULL DEFAULT (datetime('now')),
    -- Çok kullanıcılı kurulum: belge bir sahibe ait ve varsayılan olarak
    -- ÖZEL. 'ortak' yalnızca admin'in yüklediği şirket geneli belgeler için.
    owner_id     INTEGER,
    paylasim     TEXT NOT NULL DEFAULT 'ozel'
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

-- Sayfa bazlı metin. Atıfın "sayfa 7" diyebilmesi ve cevabın hangi sayfanın
-- taramadan okunduğunu bilmesi için sayfa ayrı saklanıyor; chunk'lar sayfa
-- sınırını aşabildiği için bu bilgi chunk'tan geri çıkarılamazdı.
CREATE TABLE IF NOT EXISTS pages (
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no      INTEGER NOT NULL,        -- 1'den başlar
    source       TEXT NOT NULL,           -- 'text' (metin katmanı) | 'ocr'
    text         TEXT NOT NULL,
    PRIMARY KEY (document_id, page_no)
);

-- İndekslenen asıl birim. row_id = vektör matrisindeki satır indeksi.
-- Bir belge = 1 özet chunk'ı + N gövde chunk'ı.
CREATE TABLE IF NOT EXISTS chunks (
    row_id       INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL,
    ordinal      INTEGER NOT NULL,        -- belge içindeki sıra
    kind         TEXT NOT NULL,           -- 'summary' | 'body'
    section      TEXT,                    -- "MADDE 5 - CEZAİ ŞART" gibi
    page_start   INTEGER,
    page_end     INTEGER,
    text         TEXT NOT NULL,
    -- Silinen belgenin chunk'ı fiziksel olarak kalıyor ama aramaya girmiyor:
    -- satır silmek vektör matrisindeki satır indekslerini kaydırırdı.
    deleted      INTEGER NOT NULL DEFAULT 0,
    added_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(document_id, ordinal, kind)
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

-- Kullanıcı geri bildirimi + few-shot düzeltme havuzu
CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY,
    query_raw    TEXT NOT NULL,
    lang         TEXT,
    vote         INTEGER NOT NULL,          -- +1 | -1
    reason       TEXT,
    predicted_category TEXT,
    corrected_category TEXT,                -- 👎 + düzeltme verildiyse
    user_id      INTEGER,
    embedding    BLOB NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_vote ON feedback(vote);

-- Sorgu bazlı çalışma izi (maliyet/gecikme/yol analizi)
-- Sohbet geçmişi. Streamlit'in `session_state`'i tarayıcı bağlantısına bağlı:
-- bağlantı düşünce yeni oturum açılıyor ve geçmiş kayboluyor (yaşandı). Kalıcı
-- olması gereken tek yer burası.
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    owner_id    INTEGER,                   -- sohbetler paylaşılmıyor
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,          -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    -- Cevabın tüm telemetrisi (atıflar, adımlar, maliyet) JSON olarak.
    -- Ayrı sütunlara açılmadı: yapısı değiştikçe şema göçü gerekirdi ve bu
    -- veri yalnızca gösterim için okunuyor, sorgulanmıyor.
    result          TEXT,
    voted           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);

-- Kullanıcılar. Şirket içi kurulum: hesapları admin açıyor, parolalar
-- scrypt ile saklanıyor (düz metin hiçbir yerde yok). Rol iki tane:
--   user  — yalnızca kendi belgelerini ve ortak havuzu görür
--   admin — hesapları yönetir ve kullanım istatistiğini görür; BAŞKASININ
--           BELGESİNİ VE SOHBETİNİ GÖREMEZ (kural kodda, arayüzde değil)
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    display_name  TEXT,
    password_hash BLOB NOT NULL,
    salt          BLOB NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login    TEXT
);

-- Oturumlar. Çerezde duran değerin KENDİSİ değil sha256'sı saklanıyor:
-- veritabanını okuyan biri (yedek dosyası, ekran görüntüsü) oturum çalamasın.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS traces (
    id           INTEGER PRIMARY KEY,
    query_raw    TEXT NOT NULL,
    lang         TEXT,
    category     TEXT,
    route        TEXT,
    tier_used    TEXT,
    cost_usd     REAL DEFAULT 0,
    total_ms     REAL DEFAULT 0,
    rewrites     INTEGER DEFAULT 0,
    regens       INTEGER DEFAULT 0,
    steps        TEXT,                      -- JSON dizi
    user_id      INTEGER,                   -- istatistik kullanıcı bazında
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# Şemaya sonradan eklenen sütunlar. `CREATE TABLE IF NOT EXISTS` var olan bir
# tabloyu değiştirmediği için, eski bir veritabanında şema scripti (özellikle
# yeni sütuna kurulan indeks) patlıyordu. Bu tablo bağlantı açılışında sessizce
# tamamlanıyor, yani eski DB'ler kendiliğinden uyumlu hale geliyor.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    # Çok kullanıcılı sürüm: her belge ve her sohbet bir sahibe ait.
    # `paylasim`: 'ozel' yalnızca sahibine görünür, 'ortak' herkese açık
    # (yönetmelik, şablon gibi şirket geneli belgeler; yüklemesi admin'de).
    "documents": [("owner_id", "INTEGER"), ("paylasim", "TEXT NOT NULL DEFAULT 'ozel'")],
    "conversations": [("owner_id", "INTEGER")],
    # İstatistik kullanıcı bazında: "kim kaç sorgu attı, ne kadar harcadı".
    "traces": [("user_id", "INTEGER")],
    "feedback": [("user_id", "INTEGER")],
}


def _pre_migrate(conn: sqlite3.Connection) -> None:
    """Şema scriptinden ÖNCE eksik sütunları ekler."""
    existing_tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def connect() -> sqlite3.Connection:
    """Thread başına bir bağlantı (Streamlit birden çok thread kullanır)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        path = config.abs_path("paths.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        # `timeout`: SQLite yazma kilidi başkasındayken HATA VERMEK yerine
        # beklesin. İki arayüz (Streamlit ve API) aynı dosyayı kullanıyor ve
        # yükleme/özet yazarken kilidi saniyelerce tutabiliyor. Varsayılan 5 sn
        # yetmedi: "database is locked" canlıda altı kez düştü, sohbet
        # açılamadı ve arayüz hata verdi.
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # PRAGMA'lar BAĞLANTI BAŞINA: `journal_mode` dosyaya yazılıp kalıcı
        # oluyor ama `busy_timeout` ve `synchronous` her bağlantıda yeniden
        # ayarlanmak zorunda — şema betiğinde olmaları yetmiyor.
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")    # okuyucu yazarı bloklamasın
        conn.execute("PRAGMA synchronous=NORMAL")  # WAL ile güvenli, belirgin hızlı
        _pre_migrate(conn)
        conn.executescript(SCHEMA)
        conn.commit()
        _local.conn = conn
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
