"""Multi-source ingest dispatcher for competitor-watch.

Sources:
  - wechat   (wcrss.com)
  - taptap   (TapTap web open APIs)
  - hykb     (好游快爆 3839.com HTML scrape)
  - official (官网/官方论坛, 配置文件驱动: RSS 优先, HTML fallback)

Usage:
  python scripts/ingest.py                     # all enabled sources
  python scripts/ingest.py --source wechat     # just one
  python scripts/ingest.py --source wechat --recent-days 7 --num 100
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from sources import wechat as src_wechat       # noqa: E402
from sources import taptap as src_taptap       # noqa: E402
from sources import hykb as src_hykb           # noqa: E402
from sources import official as src_official   # noqa: E402

ALL_SOURCES = {
    "wechat": src_wechat.run,
    "taptap": src_taptap.run,
    "hykb": src_hykb.run,
    "official": src_official.run,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="competitor-watch multi-source ingest")
    parser.add_argument(
        "--source",
        choices=list(ALL_SOURCES.keys()) + ["all"],
        default="all",
        help="which source(s) to run (default: all enabled)",
    )
    parser.add_argument("--recent-days", type=int, default=3, help="wechat lookback window")
    parser.add_argument("--num", type=int, default=50, help="wechat per-call article count")
    parser.add_argument("--limit", type=int, default=20, help="taptap reviews per app per run")
    args = parser.parse_args()

    targets = list(ALL_SOURCES.keys()) if args.source == "all" else [args.source]

    total_new = 0
    total_dup = 0
    for name in targets:
        runner = ALL_SOURCES[name]
        print(f"\n[ingest] === source: {name} ===")
        try:
            new, dup = runner(
                recent_days=args.recent_days,
                num=args.num,
                limit=args.limit,
            )
            total_new += new
            total_dup += dup
            print(f"[ingest:{name}] inserted={new} duplicates={dup}")
        except Exception as e:  # noqa: BLE001
            print(f"[ingest:{name}] FAILED: {type(e).__name__}: {e}", file=sys.stderr)

    print(f"\n[ingest] DONE  total_new={total_new}  total_dup={total_dup}")

    # Phase 1: 自动给新入库的文章打分（增量；幂等）
    if total_new > 0:
        print("\n[ingest] running scoring engine on new articles ...")
        try:
            import score as _score
            from db import connect, query_unscored_articles, update_article_score
            cfg = _score.load_config()
            industry_set = _score.load_industry_set()
            db_path = Path(__file__).resolve().parent.parent / "data" / "articles.db"
            with connect(db_path) as conn:
                rows = query_unscored_articles(conn)
                tier_counts = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}
                for r in rows:
                    res = _score.score_article(dict(r), cfg, industry_set)
                    tier_counts[res["tier"]] += 1
                    update_article_score(conn, r["hash"], json.dumps(res, ensure_ascii=False))
                print(f"[score] {len(rows)} new articles scored: " +
                      "  ".join(f"{t}={tier_counts[t]}" for t in ("S","A","B","C","D")))
        except Exception as e:  # noqa: BLE001
            print(f"[score] FAILED: {type(e).__name__}: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
