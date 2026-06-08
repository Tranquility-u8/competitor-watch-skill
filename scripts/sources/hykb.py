"""好游快爆 (3839.com) source — pulls per-game rating + latest update bulletin.

Approach: HTML scrape the public game detail page `https://www.3839.com/a/<fid>.htm`.
We do NOT scrape user comments here (反爬中等, 留给二期). Two artifacts produced:
  - rating_snapshot   (score)
  - article (latest update bulletin from `#game_open_log_a`)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, insert_article_if_new, insert_rating_snapshot  # noqa: E402

from . import _common as C  # noqa: E402

SOURCE = "hykb"
DETAIL_URL = "https://www.3839.com/a/{fid}.htm"

_RE_TITLE = re.compile(r"<title>([^<]+)</title>")
_RE_SCORE = re.compile(r'class="sp-val[^"]*">\s*([0-9]+(?:\.[0-9])?)')
_RE_UPDATE = re.compile(
    r'<a id="game_open_log_a"[^>]*>\s*([^<]+?)\s*</a>',
    re.S,
)


def _parse(html: str) -> dict:
    out: dict = {}
    if m := _RE_TITLE.search(html):
        out["title"] = m.group(1).strip()
    if m := _RE_SCORE.search(html):
        try:
            out["score"] = float(m.group(1))
        except ValueError:
            pass
    if m := _RE_UPDATE.search(html):
        out["latest_update"] = re.sub(r"\s+", " ", m.group(1)).strip()
    return out


def run(**_: object) -> tuple[int, int]:
    competitors = C.load_derived("hykb").get("entries", []) or []
    if not competitors:
        print("  no hykb competitors configured (run resolve.py to generate config/derived/hykb.json)")
        return 0, 0
    order = {n: i for i, n in enumerate(C.game_priority_order())}
    competitors.sort(key=lambda c: order.get(c["name"], 999))

    new = 0
    dup = 0
    snap_ts = C.now_ts()
    with connect(C.DB_PATH) as conn:
        for entry in competitors:
            if not entry.get("fid"):
                # resolve.py 没拿到高置信 fid 的占位条目，跳过
                continue
            fid = int(entry["fid"])
            label = entry.get("name") or str(fid)
            url = DETAIL_URL.format(fid=fid)
            try:
                html = C.http_get_text(url, headers={"Referer": "https://www.3839.com/"})
            except Exception as e:  # noqa: BLE001
                print(f"  [hykb:{label}] fetch failed: {e}")
                continue

            parsed = _parse(html)
            insert_rating_snapshot(
                conn,
                source=SOURCE,
                competitor=label,
                snapshot_time=snap_ts,
                score=parsed.get("score"),
                review_count=None,
                extra_json=json.dumps(parsed, ensure_ascii=False),
            )

            update_text = parsed.get("latest_update", "")
            if update_text:
                # use update text + month as dedup key (hykb does not expose timestamp)
                today_tag = time.strftime("%Y-%m-%d")
                article_url = f"{url}#update-{today_tag}"
                inserted = insert_article_if_new(
                    conn,
                    {
                        "title": f"[更新公告] {update_text[:100]}",
                        "url": article_url,
                        "description": update_text[:400],
                        "publish_time": snap_ts,
                        "mp_id": "",
                    },
                    None,
                    source=SOURCE,
                    competitor=label,
                    extra_json=json.dumps({"score": parsed.get("score")}, ensure_ascii=False),
                )
                if inserted:
                    new += 1
                else:
                    dup += 1
            print(f"  [hykb:{label}] score={parsed.get('score')} update={'yes' if update_text else 'no'}")
            time.sleep(1.0)
    return new, dup
