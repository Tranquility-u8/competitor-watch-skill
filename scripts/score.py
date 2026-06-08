"""9 维度本地化评估打分引擎（Phase 1，规则版无 LLM）。

参考：MMO 竞品监测 · 本地化落地实战 v2 PPT。

每篇 article 跑出：
  - 9 维度 1-5 分
  - 加权总分 (满分 45)
  - 5 级标签 S/A/B/C/D
  - 来源加成 + industry 降权 + 命中关键词列表（可解释性）

CLI：
  python scripts/score.py                 # 仅打 score_json IS NULL 的（增量）
  python scripts/score.py --rescore-all   # 重打所有
  python scripts/score.py --since 2026-05-01  # 重打指定日期之后

写入 articles.score_json，幂等。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from db import (  # noqa: E402
    connect,
    query_all_articles,
    query_unscored_articles,
    update_article_score,
)

DB_PATH = ROOT / "data" / "articles.db"
SCORING_CONFIG = ROOT / "config" / "scoring.json"


def load_config() -> dict:
    return json.loads(SCORING_CONFIG.read_text(encoding="utf-8"))


def _hit(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw in text]


def score_article(article: dict, cfg: dict, industry_set: set[str]) -> dict:
    """单条文章打分，返回完整 score_json dict。"""
    title = (article.get("title") or "")
    desc = (article.get("description") or "")
    text = f"{title} {desc}"
    source = article.get("source") or "wechat"
    competitor = article.get("competitor") or ""

    dimensions: dict[str, dict] = {}
    weighted_total = 0.0

    for dim_key, dim_cfg in cfg["dimensions"].items():
        score = 3.0  # 中性起点
        hits: dict[str, list[str]] = {"boost": [], "penalty": []}

        for delta_str, kws in (dim_cfg.get("boost") or {}).items():
            matched = _hit(text, kws)
            if matched:
                score += float(delta_str)  # "+2" / "+1"
                hits["boost"].extend(matched)
        for delta_str, kws in (dim_cfg.get("penalty") or {}).items():
            matched = _hit(text, kws)
            if matched:
                score += float(delta_str)  # "-2" / "-1"，已是负数
                hits["penalty"].extend(matched)

        # source 倾向加成
        bias = (cfg.get("source_weight_bias") or {}).get(source, {})
        if dim_key in bias:
            score += float(bias[dim_key])

        # 钳制 1-5
        score = max(1.0, min(5.0, score))

        weight = float(cfg["weights"][dim_key])
        weighted = score * weight
        weighted_total += weighted

        dimensions[dim_key] = {
            "score": round(score, 2),
            "weight": weight,
            "weighted": round(weighted, 2),
            "hits": hits,
        }

    # industry 降权
    if competitor in industry_set:
        weighted_total += float(cfg.get("industry_filter", {}).get("demote_score", 0))

    weighted_total = round(weighted_total, 2)

    # 分层
    tier = "D"
    for t in ("S", "A", "B", "C", "D"):
        if weighted_total >= float(cfg["tiers"][t]["min"]):
            tier = t
            break

    return {
        "v": "1.0",
        "scored_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": weighted_total,
        "tier": tier,
        "tier_label": cfg["tiers"][tier]["label"],
        "is_industry": competitor in industry_set,
        "dimensions": dimensions,
    }


def load_industry_set() -> set[str]:
    games_path = ROOT / "config" / "games.json"
    doc = json.loads(games_path.read_text(encoding="utf-8"))
    return {g["name"] for g in doc.get("games", []) if g.get("type") == "industry"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rescore-all", action="store_true",
                        help="重打所有文章（覆盖现有 score_json）")
    parser.add_argument("--since", type=str, default=None,
                        help="重打 publish_time >= 该日期 的文章 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="不写库，只打印统计")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    industry_set = load_industry_set()

    with connect(DB_PATH) as conn:
        if args.rescore_all:
            rows = query_all_articles(conn)
            print(f"[score] rescoring ALL {len(rows)} articles ...")
        elif args.since:
            since_ts = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp())
            rows = conn.execute(
                "SELECT * FROM articles WHERE publish_time >= ? ORDER BY publish_time DESC",
                (since_ts,),
            ).fetchall()
            print(f"[score] rescoring {len(rows)} articles since {args.since}")
        else:
            rows = query_unscored_articles(conn)
            print(f"[score] scoring {len(rows)} unscored articles ...")

        if not rows:
            print("[score] nothing to do")
            return 0

        tier_counts = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}
        n_written = 0
        for r in rows:
            article = dict(r)
            res = score_article(article, cfg, industry_set)
            tier_counts[res["tier"]] += 1
            if not args.dry_run:
                update_article_score(conn, article["hash"], json.dumps(res, ensure_ascii=False))
                n_written += 1
            if args.verbose:
                t = (article.get("title") or "")[:48]
                print(f"  [{res['tier']}] {res['total']:5.1f}  [{article.get('competitor','')[:8]:<8s}] {t}")

        print(f"\n[score] done. written={n_written}  dry_run={args.dry_run}")
        print(f"[score] tier dist: " +
              "  ".join(f"{t}={tier_counts[t]}" for t in ("S", "A", "B", "C", "D")))
        total = sum(tier_counts.values())
        if total:
            for t in ("S", "A", "B", "C", "D"):
                pct = tier_counts[t] / total * 100
                print(f"   {t}: {tier_counts[t]:>4}  ({pct:>5.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
