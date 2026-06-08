"""TapTap source — 仅取游戏评分快照（不再抓评论）。

用户只关注官方更新动态，玩家评论从 v3.8 起不入 articles 表。
评分快照仍然写 rating_snapshots，作为辅助信息保留（可在 ingest 输出里看到）。

Endpoints (公开 web，无需登录):
  - https://www.taptap.cn/webapiv2/app/v4/detail?id=<app_id>
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, insert_rating_snapshot  # noqa: E402

from . import _common as C  # noqa: E402

SOURCE = "taptap"
DETAIL_URL = "https://www.taptap.cn/webapiv2/app/v4/detail?id={app_id}&X-UA={ua}"


def _x_ua() -> str:
    raw = (
        f"V=1&PN=WebApp&LANG=zh_CN&VN_CODE=102&LOC=CN&PLT=PC&DS=Android"
        f"&UID={uuid.uuid4()}"
    )
    return quote(raw, safe="")


def _fetch_detail(app_id: int) -> dict:
    url = DETAIL_URL.format(app_id=app_id, ua=_x_ua())
    return C.http_get_json(
        url,
        headers={"Referer": f"https://www.taptap.cn/app/{app_id}"},
    )


def run(**_: object) -> tuple[int, int]:
    derived = C.load_derived("taptap").get("entries", []) or []
    competitors = [e for e in derived if e.get("app_id")]
    if not competitors:
        print("  no taptap competitors with app_id (run resolve.py + fill app_id manually first)")
        missing = [e["name"] for e in derived if not e.get("app_id")]
        if missing:
            print(f"  待补 app_id: {missing[:5]}{'...' if len(missing) > 5 else ''}")
        return 0, 0

    order = {n: i for i, n in enumerate(C.game_priority_order())}
    competitors.sort(key=lambda c: order.get(c["name"], 999))

    snap_ts = C.now_ts()
    captured = 0
    with connect(C.DB_PATH) as conn:
        for entry in competitors:
            app_id = int(entry["app_id"])
            label = entry.get("name") or str(app_id)
            try:
                detail = _fetch_detail(app_id)
                app = detail.get("data") or {}
                stat = app.get("stat") or {}
                rating = stat.get("rating") or {}
                score = float(rating.get("score") or 0) or None
                review_count = int(stat.get("review_count") or 0) or None
                insert_rating_snapshot(
                    conn,
                    source=SOURCE,
                    competitor=label,
                    snapshot_time=snap_ts,
                    score=score,
                    review_count=review_count,
                    extra_json=json.dumps(
                        {
                            "vote_info": stat.get("vote_info"),
                            "latest_score": rating.get("latest_score"),
                            "latest_version_score": rating.get("latest_version_score"),
                        },
                        ensure_ascii=False,
                    ),
                )
                captured += 1
                print(f"  [taptap:{label}] score={score} reviews={review_count}")
            except Exception as e:  # noqa: BLE001
                print(f"  [taptap:{label}] detail failed: {e}")

            time.sleep(1.0)

    print(f"  [taptap] rating snapshots captured: {captured}")
    return 0, 0  # 不再产生 articles

