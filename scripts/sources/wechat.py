"""WeChat 公众号 source — wraps wcrss.com /sapi/articles."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import (  # noqa: E402
    article_hash,
    connect,
    insert_article_if_new,
    upsert_publishers,
)
from wcrss_client import fetch_articles  # noqa: E402

from . import _common as C  # noqa: E402

SOURCE = "wechat"


def _save_raw(article: dict) -> str | None:
    html = article.get("content_html") or ""
    if not html:
        return None
    pt = int(article.get("publish_time") or 0)
    sub = (
        datetime.fromtimestamp(pt, tz=timezone.utc).strftime("%Y-%m")
        if pt else "unknown"
    )
    out_dir = C.RAW_DIR / SOURCE / sub
    out_dir.mkdir(parents=True, exist_ok=True)
    h = article_hash(SOURCE, article["url"])
    path = out_dir / f"{h}.html"
    path.write_text(html, encoding="utf-8")
    return str(path.relative_to(C.ROOT).as_posix())


def run(*, recent_days: int = 3, num: int = 50, **_: object) -> tuple[int, int]:
    print(f"  fetch recentDays={recent_days} num={num}")
    payload = fetch_articles(recent_days, num)
    publishers = payload.get("publishers", []) or []
    articles = payload.get("articles", []) or []
    print(f"  got {len(articles)} articles, {len(publishers)} publishers")

    pub_by_id = {p["mp_id"]: p for p in publishers}

    # 用 derived/wcrss.json (resolve 后产物) 决定哪些 mp_id 属于 games.json 名单内
    derived = C.load_derived("wcrss").get("entries", []) or []
    in_scope_mp_ids: set[str] = {e["mp_id"] for e in derived if e.get("mp_id")}
    pub_to_game: dict[str, str] = {e["mp_id"]: e["name"] for e in derived if e.get("mp_id")}
    use_filter = bool(in_scope_mp_ids)
    if use_filter:
        print(f"  filter: 只保留 games.json 名单内的 {len(in_scope_mp_ids)} 个公众号")

    new = 0
    dup = 0
    skipped = 0
    with connect(C.DB_PATH) as conn:
        upsert_publishers(conn, publishers)
        for art in articles:
            if not art.get("url"):
                continue
            mp_id = art.get("mp_id", "")
            if use_filter and mp_id not in in_scope_mp_ids:
                skipped += 1
                continue
            raw_path = _save_raw(art)
            # 优先使用 games.json 里的规范名（避免 wcrss 公众号别名导致竞品分裂）
            competitor = pub_to_game.get(mp_id) or \
                (pub_by_id.get(mp_id, {}) or {}).get("nickname", "")
            inserted = insert_article_if_new(
                conn,
                art,
                raw_path,
                source=SOURCE,
                competitor=competitor,
            )
            if inserted:
                new += 1
            else:
                dup += 1
    if skipped:
        print(f"  skipped {skipped} articles outside games.json scope")
    return new, dup
