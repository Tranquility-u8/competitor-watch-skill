"""Multi-source ingest dispatcher for competitor-watch.

Sources (each gated by an `enabled` switch in config/competitor-watch.json → sources):
  - wechat   (configurable RSS service;   switch: sources.wcrss.enabled)
  - official (官网/官方论坛, RSS 优先;      switch: sources.official.enabled)
  - extra_sources (extensible list;        switch: sources.extra_sources[].enabled)
      built-in collectors: hykb (好游快爆), taptap
      custom collectors: drop a `scripts/sources/<id>.py` with a run() function
      and add {"id": "<id>", ...} to sources.extra_sources — no code changes needed.

Usage:
  python scripts/ingest.py                     # all enabled sources
  python scripts/ingest.py --source wechat     # just one (explicit = ignores the switch)
  python scripts/ingest.py --source wechat --recent-days 7 --num 100
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import config_loader  # noqa: E402
from sources import wechat as src_wechat       # noqa: E402
from sources import taptap as src_taptap       # noqa: E402
from sources import hykb as src_hykb           # noqa: E402
from sources import official as src_official   # noqa: E402

# built-in collectors, keyed by source id
BUILTIN_SOURCES: dict[str, object] = {
    "wechat": src_wechat.run,
    "taptap": src_taptap.run,
    "hykb": src_hykb.run,
    "official": src_official.run,
}


def _load_custom_collector(source_id: str):
    """Try to import scripts/sources/<id>.py as an extensible collector.

    A valid collector module exposes run(**kwargs) -> (new, dup).
    Returns the run function, or None if the module/attr does not exist.
    """
    try:
        mod = importlib.import_module(f"sources.{source_id}")
    except ImportError:
        return None
    run = getattr(mod, "run", None)
    return run if callable(run) else None


def _runner_for(source_id: str):
    run = BUILTIN_SOURCES.get(source_id) or _load_custom_collector(source_id)
    if run is None:
        return None
    label = {
        "wechat": "微信公众号 RSS（sources.wcrss）",
        "official": "游戏官网/论坛（sources.official）",
    }.get(source_id)
    if label is None:
        for e in config_loader.extra_source_ids():
            if e.get("id") == source_id:
                label = f"{e.get('label') or source_id}（sources.extra_sources）"
                break
    return run, label or source_id


def _enabled_plan() -> list[str]:
    """Which source ids should run for `--source all`, per config switches."""
    plan: list[str] = []
    if config_loader.source_enabled("wcrss"):
        plan.append("wechat")
    if config_loader.source_enabled("official"):
        plan.append("official")
    for e in config_loader.extra_source_ids():
        if e.get("enabled", True) and e.get("id"):
            plan.append(e["id"])
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="competitor-watch multi-source ingest")
    parser.add_argument(
        "--source",
        default="all",
        help="which source(s) to run: all | built-in id (wechat/official/hykb/taptap) "
             "| any custom id present in sources.extra_sources (default: all enabled)",
    )
    parser.add_argument("--recent-days", type=int, default=3, help="wechat lookback window")
    parser.add_argument("--num", type=int, default=50, help="wechat per-call article count")
    parser.add_argument("--limit", type=int, default=20, help="taptap reviews per app per run")
    args = parser.parse_args()

    if args.source == "all":
        targets = _enabled_plan()
        print(f"[ingest] enabled sources (config switches): {targets or '(none)'}")
    else:
        # explicit --source always runs, even if disabled in config
        targets = [args.source]
        if not config_loader.source_enabled(args.source):
            print(f"[ingest] note: '{args.source}' is disabled in config; "
                  f"running anyway because it was explicitly requested")

    total_new = 0
    total_dup = 0
    for name in targets:
        resolved = _runner_for(name)
        if resolved is None:
            print(f"[ingest:{name}] SKIP — 未找到采集器 scripts/sources/{name}.py "
                  f"（自定义源需提供 run() 函数，见用户手册第 4 节）")
            continue
        runner, label = resolved
        print(f"\n[ingest] === source: {name} ({label}) ===")
        # wechat 需要已配置 RSS 服务，未配置时友好跳过（而非报错）
        if name == "wechat" and not config_loader.load_sources_flat()["wcrss_feed_url"]:
            print("[ingest:wechat] skip — sources.wcrss.feed_url 未配置 "
                  "（或环境变量 WCRSS_FEED_URL 未设置），其余源不受影响")
            continue
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
