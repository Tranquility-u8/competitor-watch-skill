"""Generate Markdown reports (daily / weekly / monthly / yearly) from articles.db.

严格语义（v3.4+）：
  - daily: 默认锚点 = 昨天（"工作日 12:00 拉昨天的"）
  - daily 内容严格按 publish_time ∈ [date 00:00 CST, date+1 00:00 CST)
  - weekly/monthly/yearly 行为不变（也都是回看上一完整周/月/年）
  - 支持 --catch-up N：扫过去 N 天，缺哪天/标记 dirty 的就重生成

v3.7 变更：
  - 输出格式抽到 config/report_format.json，可手动编辑（关键词/长度/标题/章节顺序）
  - 当 anchor == 今天 时，文件头加警告横幅，文件末加 dirty marker
  - catch-up 扫到 dirty marker 视为不完整，自动 force 重生
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from db import connect, query_articles  # noqa: E402

DB_PATH = ROOT / "data" / "articles.db"
REPORT_DIR = ROOT / "data" / "reports"
FORMAT_PATH = ROOT / "config" / "report_format.json"

CST = timezone(timedelta(hours=8))


# ---------- format config ----------

def load_format() -> dict:
    if not FORMAT_PATH.exists():
        raise FileNotFoundError(f"format config not found: {FORMAT_PATH}")
    return json.loads(FORMAT_PATH.read_text(encoding="utf-8"))


_FORMAT_CACHE: dict | None = None


def fmt() -> dict:
    global _FORMAT_CACHE
    if _FORMAT_CACHE is None:
        _FORMAT_CACHE = load_format()
    return _FORMAT_CACHE


def content_tags() -> list[tuple[str, tuple[str, ...]]]:
    """Return ordered list of (tag, keywords). Excludes _doc keys."""
    raw = fmt().get("content_tags", {})
    return [(k, tuple(v)) for k, v in raw.items() if not k.startswith("_") and isinstance(v, list)]


def source_labels() -> dict[str, str]:
    return {k: v for k, v in fmt().get("source_labels", {}).items() if not k.startswith("_")}


def to_ts(d: datetime) -> int:
    return int(d.timestamp())


def fmt_dt(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=CST).strftime("%Y-%m-%d %H:%M")


def yesterday_cst() -> date:
    return (datetime.now(CST) - timedelta(days=1)).date()


def today_cst() -> date:
    return datetime.now(CST).date()


def daterange_for(kind: str, anchor: date | None) -> tuple[datetime, datetime, str]:
    """Compute [start, end) interval and label for a given report kind.

    For daily: anchor 默认 = 昨天（CST），区间 [anchor 00:00, anchor+1 00:00)。
    For weekly/monthly/yearly: 仍然回看上一完整周/月/年。
    """
    if kind == "daily":
        d = anchor or yesterday_cst()
        start = datetime.combine(d, time.min, tzinfo=CST)
        end = start + timedelta(days=1)
        return start, end, d.strftime("%Y-%m-%d")

    today = anchor or datetime.now(CST).date()
    if kind == "weekly":
        weekday = today.weekday()
        this_monday = today - timedelta(days=weekday)
        last_monday = this_monday - timedelta(days=7)
        last_sunday = last_monday + timedelta(days=7)
        start = datetime.combine(last_monday, time.min, tzinfo=CST)
        end = datetime.combine(last_sunday, time.min, tzinfo=CST)
        iso_year, iso_week, _ = last_monday.isocalendar()
        return start, end, f"{iso_year}-W{iso_week:02d}"
    if kind == "monthly":
        first_of_this = today.replace(day=1)
        last_of_prev = first_of_this - timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        start = datetime.combine(first_of_prev, time.min, tzinfo=CST)
        end = datetime.combine(first_of_this, time.min, tzinfo=CST)
        return start, end, first_of_prev.strftime("%Y-%m")
    if kind == "yearly":
        first_of_this_year = today.replace(month=1, day=1)
        last_year_first = first_of_this_year.replace(year=first_of_this_year.year - 1)
        start = datetime.combine(last_year_first, time.min, tzinfo=CST)
        end = datetime.combine(first_of_this_year, time.min, tzinfo=CST)
        return start, end, last_year_first.strftime("%Y")
    raise ValueError(f"unknown kind: {kind}")


def is_dirty_label(kind: str, label: str) -> bool:
    """A daily report whose label == today (CST) is dirty (unfinished)."""
    if kind != "daily":
        return False
    try:
        d = datetime.strptime(label, "%Y-%m-%d").date()
    except ValueError:
        return False
    return d == today_cst()


def file_is_dirty(path: Path) -> bool:
    """Quick scan: does the on-disk md contain the dirty marker?"""
    if not path.exists():
        return False
    pat = re.compile(fmt().get("dirty_marker", {}).get("marker_regex", r"competitor-watch-status:\s*dirty"))
    try:
        return bool(pat.search(path.read_text(encoding="utf-8", errors="ignore")))
    except Exception:
        return False


# ---------- summary helpers ----------

def _classify(title: str, desc: str) -> list[str]:
    text = f"{title} {desc}"
    return [tag for tag, kws in content_tags() if any(kw in text for kw in kws)]


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _render_summary(out: list[str], rows) -> None:
    f = fmt()
    title = f.get("section_titles", {}).get("summary", "## 摘要")

    if not rows:
        out.append(title)
        out.append("")
        out.append("> 本期暂无符合 `publish_time` 区间的文章，无内容可总结。")
        out.append("")
        return

    by_comp: dict[str, list] = defaultdict(list)
    tag_counter: Counter[str] = Counter()
    comp_tag_counter: dict[str, Counter[str]] = defaultdict(Counter)
    tier_counter: Counter[str] = Counter()

    # 预解析 score_json
    row_meta: dict = {}
    for r in rows:
        comp = r["competitor"] or r["publisher_name"] or "（未分类）"
        by_comp[comp].append(r)
        tags = _classify(r["title"] or "", _strip_html(r["description"] or ""))
        for t in tags:
            tag_counter[t] += 1
            comp_tag_counter[comp][t] += 1
        # 评分元信息
        sj = r["score_json"] if "score_json" in r.keys() else None
        tier = "?"
        total = None
        if sj:
            try:
                d = json.loads(sj)
                tier = d.get("tier", "?")
                total = d.get("total")
            except Exception:  # noqa: BLE001
                pass
        tier_counter[tier] += 1
        row_meta[r["hash"]] = {"tier": tier, "total": total, "tags": tags}

    limits = f.get("limits", {})
    titles_per = int(limits.get("summary_titles_per_competitor", 8))
    desc_chars = int(limits.get("summary_description_chars", 80))
    top_tags = int(limits.get("competitor_top_tags", 3))

    out.append(title)
    out.append("")
    out.append(f"今日共 **{len(rows)}** 篇内容，覆盖 **{len(by_comp)}** 个竞品/行业号。")
    out.append("")

    # 评分分层（9 维度评估引擎）
    if any(t in tier_counter for t in ("S", "A", "B", "C", "D")):
        out.append("**评分分层**（9 维度评估，满分 45）：")
        out.append("")
        out.append("| 等级 | 数量 | 占比 | 说明 |")
        out.append("|---|---|---|---|")
        tier_labels = {
            "S": "强烈推荐 · 战略级",
            "A": "可立项 · 进策划评审",
            "B": "差异化借鉴 · 排进版本",
            "C": "存档参考",
            "D": "忽略",
        }
        total_n = len(rows)
        for t in ("S", "A", "B", "C", "D"):
            n = tier_counter.get(t, 0)
            pct = (n / total_n * 100) if total_n else 0
            out.append(f"| {t} | {n} | {pct:.1f}% | {tier_labels[t]} |")
        if tier_counter.get("?", 0):
            out.append(f"| ? | {tier_counter['?']} | - | 未打分（旧数据） |")
        out.append("")

    if tag_counter:
        out.append("**内容类型分布**：")
        out.append("")
        out.append("| 类型 | 数量 |")
        out.append("|---|---|")
        for tag, n in tag_counter.most_common():
            out.append(f"| {tag} | {n} |")
        out.append("")

    out.append("**各竞品当日动作**（按文章数排序）：")
    out.append("")
    # 竞品内部按 score 总分降序，没分的排后
    def _sort_key(it):
        m = row_meta.get(it["hash"], {})
        # tier 优先 S>A>B>C>D>?，分数越高越前
        tier_rank = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "?": 5}.get(m.get("tier", "?"), 5)
        return (tier_rank, -(m.get("total") or 0))

    for comp, lst in sorted(by_comp.items(), key=lambda kv: -len(kv[1])):
        tags = comp_tag_counter.get(comp) or Counter()
        tag_str = " / ".join(f"{t}×{n}" for t, n in tags.most_common(top_tags)) if tags else "（未分类）"
        out.append(f"### {comp}（{len(lst)} 篇 · {tag_str}）")
        out.append("")
        sorted_lst = sorted(lst, key=_sort_key)
        for it in sorted_lst[:titles_per]:
            t = it["title"] or ""
            desc = _strip_html(it["description"] or "")
            meta = row_meta.get(it["hash"], {})
            tier = meta.get("tier", "?")
            score = meta.get("total")
            it_tags = meta.get("tags") or []
            # 前缀：【S 37.8】【新版本】【活动运营】
            prefix_parts = []
            if tier and tier != "?":
                if score is not None:
                    prefix_parts.append(f"【{tier} {score:.1f}】")
                else:
                    prefix_parts.append(f"【{tier}】")
            for tg in it_tags:
                prefix_parts.append(f"【{tg}】")
            prefix = "".join(prefix_parts)
            line = f"- {prefix}**{t}**" if prefix else f"- **{t}**"
            if desc:
                line += f" — {desc[:desc_chars]}{'…' if len(desc) > desc_chars else ''}"
            out.append(line)
        if len(lst) > titles_per:
            out.append(f"- ……还有 {len(lst) - titles_per} 篇")
        out.append("")

    out.append("> 摘要由脚本基于标题/描述关键词自动生成；评分由 9 维度规则引擎计算（详见 `config/scoring.json`）。")
    out.append("")


def _render_grouped_section(out: list[str], rows, header: str) -> None:
    out.append(header)
    out.append("")
    by_source: dict[str, list] = defaultdict(list)
    for r in rows:
        by_source[r["source"] or "wechat"].append(r)
    labels = source_labels()
    desc_chars = int(fmt().get("limits", {}).get("raw_description_chars", 160))
    for src in labels.keys():
        items = by_source.get(src)
        if not items:
            continue
        out.append(f"### {labels.get(src, src)} ({len(items)} 条)")
        out.append("")
        by_pub: dict[str, list] = defaultdict(list)
        for it in items:
            key = it["competitor"] or it["publisher_name"] or "（未分类）"
            by_pub[key].append(it)
        for pub, lst in sorted(by_pub.items(), key=lambda kv: -len(kv[1])):
            out.append(f"#### {pub} ({len(lst)} 条)")
            out.append("")
            for it in lst:
                out.append(f"- [{it['title']}]({it['url']})  ·  {fmt_dt(it['publish_time'])}")
                desc = _strip_html(it["description"] or "")
                if desc:
                    out.append(f"  - {desc[:desc_chars]}{'…' if len(desc) > desc_chars else ''}")
            out.append("")


def render_markdown(rows, kind: str, label: str, start: datetime, end: datetime,
                     *, dirty: bool = False) -> str:
    f = fmt()
    by_source: dict[str, list] = defaultdict(list)
    for r in rows:
        by_source[r["source"] or "wechat"].append(r)

    kind_zh = f.get("kind_titles", {}).get(kind, kind)
    section_titles = f.get("section_titles", {})
    section_order = (f.get("section_order", {}).get("default")
                     or ["overview", "summary", "raw"])
    labels = source_labels()

    out: list[str] = []
    out.append(f"# {kind_zh} {label}")
    out.append("")

    # dirty banner（只对 daily today 生效）
    if dirty:
        tpl = f.get("dirty_marker", {}).get("header_template", "")
        if tpl:
            now_str = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
            out.append(tpl.format(render_time=now_str))
            out.append("")

    out.append(f"区间：{start.strftime('%Y-%m-%d %H:%M')} ~ {end.strftime('%Y-%m-%d %H:%M')} (CST)")
    out.append("")

    def render_section(name: str) -> None:
        if name == "overview":
            out.append(section_titles.get("overview", "## 概览"))
            out.append("")
            out.append(f"- 区间内**发布**文章数：**{len(rows)}**")
            for src, items in by_source.items():
                out.append(f"  - {labels.get(src, src)}: {len(items)} 条")
            out.append("")
        elif name == "summary":
            _render_summary(out, rows)
        elif name == "raw":
            if rows:
                raw_title = section_titles.get("raw", "## 原文清单").format(kind_zh=kind_zh)
                _render_grouped_section(out, rows, raw_title)
            else:
                empty = f.get("empty_message", "> 本期暂无文章。")
                out.append(empty)
                out.append("")

    for sec in section_order:
        render_section(sec)

    # dirty footer marker（机器可读）
    if dirty:
        marker = f.get("dirty_marker", {}).get("marker_html", "")
        if marker:
            out.append("")
            out.append(marker)
            out.append("")

    return "\n".join(out)


def generate_one(kind: str, anchor: date | None, sources: list[str] | None,
                 *, force: bool = False, force_dirty: bool = True
                 ) -> tuple[Path | None, int, str]:
    """Generate one report.

    force_dirty: when True (default), an existing on-disk *dirty* file is treated
                 as not present (i.e. always regenerated even without --force).

    Returns (out_path, article_count, status) where status is one of:
        'created' | 'updated' | 'skipped' | 'refreshed-dirty'.
    """
    start, end, label = daterange_for(kind, anchor)
    out_dir = REPORT_DIR / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{label}.md"

    dirty = is_dirty_label(kind, label)
    existed = out_path.exists()
    was_dirty_on_disk = file_is_dirty(out_path) if existed else False

    if existed and not force:
        # 还是当日（dirty）→ 默认刷新；非当日且没标 dirty 残留 → 跳过
        if not (dirty or (was_dirty_on_disk and force_dirty)):
            return out_path, -1, "skipped"

    with connect(DB_PATH) as conn:
        rows = query_articles(conn, to_ts(start), to_ts(end), sources=sources)

    md = render_markdown(rows, kind, label, start, end, dirty=dirty)
    out_path.write_text(md, encoding="utf-8")

    if not existed:
        status = "created"
    elif dirty:
        status = "refreshed-dirty"
    elif was_dirty_on_disk:
        status = "refreshed-dirty"  # 本来 disk 上是 dirty，现在 anchor 已过期，重写后 marker 自然消失
    else:
        status = "updated"
    return out_path, len(rows), status




def main() -> int:
    parser = argparse.ArgumentParser(description="Generate competitor report")
    parser.add_argument("kind", choices=["daily", "weekly", "monthly", "yearly"])
    parser.add_argument("--date",
                        help="anchor date YYYY-MM-DD. daily 默认 = 昨天；其它 kind 默认 = 今天回看")
    parser.add_argument("--source", action="append",
                        help="filter by source (repeatable)")
    parser.add_argument("--catch-up", type=int, metavar="N", default=0,
                        help="补齐过去 N 天的缺失日报（仅 daily 有效）。"
                             "扫 [date-N+1, date] 这 N 天，缺哪天补哪天。")
    parser.add_argument("--force", action="store_true",
                        help="即使报告文件已存在也重新生成")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[report] db not found: {DB_PATH}; run ingest.py first", file=sys.stderr)
        return 1

    anchor = (datetime.strptime(args.date, "%Y-%m-%d").date()
              if args.date else None)

    # 普通单报告（含 weekly/monthly/yearly）
    if args.kind != "daily" or args.catch_up <= 0:
        path, n, status = generate_one(args.kind, anchor, args.source, force=args.force)
        if status == "skipped":
            print(f"[report] skip (already exists): {path}  (use --force to overwrite)")
        else:
            tag = " [DIRTY]" if status == "refreshed-dirty" else ""
            print(f"[report] {status}: {path}  ({n} articles){tag}")
        if path:
            print(str(path))
        return 0

    # daily catch-up：补齐 [anchor-N+1, anchor]，自动刷新 dirty
    base = anchor or yesterday_cst()
    days = list(reversed(range(args.catch_up)))  # 旧到新
    print(f"[report] catch-up daily: 扫 {args.catch_up} 天 (anchor={base})")
    counts = {"created": 0, "updated": 0, "refreshed-dirty": 0, "skipped": 0}
    for offset in days:
        d = base - timedelta(days=offset)
        path, n, status = generate_one("daily", d, args.source, force=args.force)
        counts[status] = counts.get(status, 0) + 1
        if status == "skipped":
            print(f"  - {d}: skip → {path}")
        elif status == "refreshed-dirty":
            print(f"  ↻ {d}: refresh dirty {n} 篇 → {path}")
        else:
            sym = "+" if status == "created" else "~"
            print(f"  {sym} {d}: {status} {n} 篇 → {path}")
    print(f"[report] catch-up done: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
