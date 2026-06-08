"""SQLite schema and CRUD helpers for competitor-watch (multi-source)."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

# Multi-source schema. `source` distinguishes wechat / taptap / hykb / official.
SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS publishers (
    mp_id      TEXT PRIMARY KEY,
    nickname   TEXT NOT NULL,
    mp_cover   TEXT,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS articles (
    hash         TEXT PRIMARY KEY,
    source       TEXT NOT NULL DEFAULT 'wechat',
    competitor   TEXT NOT NULL DEFAULT '',
    mp_id        TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    description  TEXT,
    publish_time INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now')),
    raw_path     TEXT,
    extra_json   TEXT
);

CREATE TABLE IF NOT EXISTS rating_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    competitor    TEXT NOT NULL,
    snapshot_time INTEGER NOT NULL,
    score         REAL,
    review_count  INTEGER,
    extra_json    TEXT,
    UNIQUE(source, competitor, snapshot_time)
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_articles_publish_time ON articles(publish_time);
CREATE INDEX IF NOT EXISTS idx_articles_mp_id        ON articles(mp_id);
CREATE INDEX IF NOT EXISTS idx_articles_source       ON articles(source);
CREATE INDEX IF NOT EXISTS idx_articles_competitor   ON articles(competitor);
CREATE INDEX IF NOT EXISTS idx_ratings_competitor    ON rating_snapshots(competitor);
"""


def update_article_score(
    conn: sqlite3.Connection, article_hash: str, score_json: str
) -> None:
    conn.execute(
        "UPDATE articles SET score_json = ? WHERE hash = ?",
        (score_json, article_hash),
    )


def query_unscored_articles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """For incremental scoring: rows whose score_json IS NULL."""
    return conn.execute(
        "SELECT * FROM articles WHERE score_json IS NULL ORDER BY publish_time DESC"
    ).fetchall()


def query_all_articles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """For --rescore-all"""
    return conn.execute("SELECT * FROM articles ORDER BY publish_time DESC").fetchall()


def article_hash(source: str, url: str) -> str:
    return hashlib.sha256(f"{source}::{url.strip()}".encode("utf-8")).hexdigest()


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent migration from v1 (wechat-only) to v2 (multi-source).
    + v3 (2026-06-02): score_json (Phase 1: 9-dim scoring engine)."""
    if not _has_column(conn, "articles", "source"):
        conn.execute("ALTER TABLE articles ADD COLUMN source TEXT NOT NULL DEFAULT 'wechat'")
    if not _has_column(conn, "articles", "competitor"):
        conn.execute("ALTER TABLE articles ADD COLUMN competitor TEXT NOT NULL DEFAULT ''")
    if not _has_column(conn, "articles", "extra_json"):
        conn.execute("ALTER TABLE articles ADD COLUMN extra_json TEXT")
    if not _has_column(conn, "articles", "score_json"):
        conn.execute("ALTER TABLE articles ADD COLUMN score_json TEXT")
    # backfill competitor from publishers.nickname for existing wechat rows
    conn.execute(
        """
        UPDATE articles
           SET competitor = COALESCE((SELECT nickname FROM publishers p WHERE p.mp_id = articles.mp_id), '')
         WHERE competitor = '' AND mp_id != ''
        """
    )


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA_TABLES)
        _migrate(conn)
        conn.executescript(SCHEMA_INDEXES)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_publishers(conn: sqlite3.Connection, publishers: Iterable[dict]) -> None:
    for p in publishers:
        conn.execute(
            "INSERT OR IGNORE INTO publishers(mp_id, nickname, mp_cover) VALUES (?,?,?)",
            (p["mp_id"], p.get("nickname", ""), p.get("mp_cover", "")),
        )


def insert_article_if_new(
    conn: sqlite3.Connection,
    article: dict,
    raw_path: str | None,
    *,
    source: str = "wechat",
    competitor: str = "",
    extra_json: str | None = None,
) -> bool:
    """Insert article. Returns True if a new row was created."""
    h = article_hash(source, article["url"])
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO articles
          (hash, source, competitor, mp_id, title, url, description,
           publish_time, raw_path, extra_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            h,
            source,
            competitor,
            article.get("mp_id", ""),
            article.get("title", ""),
            article["url"],
            article.get("description", ""),
            int(article.get("publish_time") or 0),
            raw_path,
            extra_json,
        ),
    )
    return cur.rowcount == 1


def insert_rating_snapshot(
    conn: sqlite3.Connection,
    *,
    source: str,
    competitor: str,
    snapshot_time: int,
    score: float | None,
    review_count: int | None,
    extra_json: str | None = None,
) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO rating_snapshots
          (source, competitor, snapshot_time, score, review_count, extra_json)
        VALUES (?,?,?,?,?,?)
        """,
        (source, competitor, snapshot_time, score, review_count, extra_json),
    )
    return cur.rowcount == 1


def query_articles(
    conn: sqlite3.Connection,
    start_ts: int,
    end_ts: int,
    sources: list[str] | None = None,
) -> list[sqlite3.Row]:
    sql = """
        SELECT a.*, p.nickname AS publisher_name
        FROM articles a
        LEFT JOIN publishers p ON a.mp_id = p.mp_id
        WHERE a.publish_time >= ? AND a.publish_time < ?
    """
    params: list = [start_ts, end_ts]
    if sources:
        placeholders = ",".join("?" * len(sources))
        sql += f" AND a.source IN ({placeholders})"
        params.extend(sources)
    sql += " ORDER BY a.publish_time DESC"
    return conn.execute(sql, params).fetchall()


def query_articles_by_fetched(
    conn: sqlite3.Connection,
    start_iso: str,
    end_iso: str,
    sources: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Articles whose `fetched_at` falls in [start_iso, end_iso). UTC string compare."""
    sql = """
        SELECT a.*, p.nickname AS publisher_name
        FROM articles a
        LEFT JOIN publishers p ON a.mp_id = p.mp_id
        WHERE a.fetched_at >= ? AND a.fetched_at < ?
    """
    params: list = [start_iso, end_iso]
    if sources:
        placeholders = ",".join("?" * len(sources))
        sql += f" AND a.source IN ({placeholders})"
        params.extend(sources)
    sql += " ORDER BY a.publish_time DESC"
    return conn.execute(sql, params).fetchall()


def query_latest_ratings(
    conn: sqlite3.Connection, source: str | None = None
) -> list[sqlite3.Row]:
    sql = """
        SELECT r.*
        FROM rating_snapshots r
        JOIN (
            SELECT source, competitor, MAX(snapshot_time) AS ts
            FROM rating_snapshots
            GROUP BY source, competitor
        ) latest
        ON r.source = latest.source
       AND r.competitor = latest.competitor
       AND r.snapshot_time = latest.ts
    """
    params: list = []
    if source:
        sql += " WHERE r.source = ?"
        params.append(source)
    sql += " ORDER BY r.competitor"
    return conn.execute(sql, params).fetchall()
