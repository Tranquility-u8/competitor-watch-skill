"""Unified configuration loader for competitor-watch (schema v2).

All configuration lives in ONE file: config/competitor-watch.json

    {
      "_meta":   { "schema_version": "2.2", ... },
      "project": { "enabled", "name", "genre", "stage", "platform",
                   "core_systems", "target_users", "mount_modules", "monetization",
                   "calendar", "differentiation", "risk_redlines", "knowledge" },
      "games":   { "games": [ {name, aliases, genre, priority, type, notes}, ... ] },
      "sources": { "wcrss": {"enabled": true, "feed_url": ...},
                   "official": {"enabled": true},
                   "extra_sources": [ {"id": "hykb", "label": "好游快爆", "enabled": true, ...}, ... ],
                   "fetch_defaults": {"recent_days":3,"num":50} },
      "scoring": { "weights", "tiers", "dimensions (each may declare project_refs)",
                   "source_weight_bias", "industry_filter" },
      "publish": { "tencent_docs", "wecom_bot", "local_backup" },
      "report":  { "defaults", "content_tags", "source_labels", "limits",
                   "section_order", "section_titles", "kind_titles",
                   "dirty_marker", "empty_message" }
    }

Backward compatibility: if the unified file does not exist, we transparently
merge the legacy split files (config.json / games.json / scoring.json /
publish.json / report_format.json). save_section() always writes the unified
file, which auto-migrates legacy setups on first write.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
UNIFIED_PATH = CONFIG_DIR / "competitor-watch.json"
EXAMPLE_PATH = CONFIG_DIR / "mmo-example.json"

# legacy split files (read-only fallback)
LEGACY = {
    "games": CONFIG_DIR / "games.json",
    "config": CONFIG_DIR / "config.json",
    "scoring": CONFIG_DIR / "scoring.json",
    "publish": CONFIG_DIR / "publish.json",
    "report_format": CONFIG_DIR / "report_format.json",
}

DEFAULTS: dict[str, dict] = {
    "project": {
        "_doc": (
            "自家产品画像——整个评分体系的参照系（知识库）。enabled=true 时，各评分"
            "维度 project_refs 声明的字段（如 core_systems）会自动展开为该维度的打分"
            "关键词；knowledge.summary 是提炼过的项目概要，供 AI 工具在分析/写报告时"
            "阅读理解项目背景（规则引擎不消费它）。产品方向调整时只需修改本节，"
            "评分口径自动跟随；改完建议重跑 score.py --rescore-all。"
        ),
        "enabled": False,
        "name": "",
        "genre": "",
        "stage": "",
        "platform": "",
        "core_systems": [],
        "target_users": {"persona": "", "pay_habits": "", "overlap_keywords": []},
        "mount_modules": [],
        "monetization": [],
        "calendar": {"rhythm": "", "key_nodes": []},
        "differentiation": [],
        "risk_redlines": [],
        "knowledge": {"summary": "", "docs": []},
    },
    "games": {"games": []},
    "sources": {
        "_doc": (
            "数据源配置与总开关。wcrss=微信公众号 RSS 聚合服务；official=游戏官网/论坛"
            "（更新 games 后 resolve.py 会自动为每个新竞品生成条目，无需手动配置）；"
            "extra_sources=可扩展的其他数据源列表（内置 hykb/taptap，可自由增删条目，"
            "新源需在 scripts/sources/ 下提供同名采集器脚本）。enabled=false 的渠道"
            "ingest.py/resolve.py 会自动跳过。"
        ),
        "wcrss": {"enabled": True, "feed_url": ""},
        "official": {"enabled": True},
        "extra_sources": [
            {
                "id": "hykb",
                "label": "好游快爆",
                "enabled": True,
                "automation": "full",
                "desc": "评分 + 更新公告。resolve.py 自动搜索定位 fid，零配置开箱即用。",
            },
            {
                "id": "taptap",
                "label": "TapTap",
                "enabled": True,
                "automation": "semi",
                "desc": "评分快照。resolve.py 生成占位并给出 _hint 搜索链接，需按提示补 app_id。",
            },
        ],
        "fetch_defaults": {"recent_days": 3, "num": 50},
    },
    "publish": {
        "tencent_docs": {"enabled": False},
        "wecom_bot": {"enabled": False},
        "local_backup": {"enabled": True, "dir": "data/reports"},
    },
    "report": {
        "defaults": {"timezone": "Asia/Shanghai", "max_articles_per_source": 20},
        "source_labels": {"wechat": "微信公众号", "official": "官方论坛/官网",
                          "hykb": "好游快爆", "taptap": "TapTap 评论"},
        "limits": {"summary_titles_per_competitor": 8, "summary_description_chars": 80,
                   "raw_description_chars": 160, "competitor_top_tags": 3},
        "section_order": {"default": ["overview", "summary", "raw"]},
        "section_titles": {"overview": "## 概览", "summary": "## 摘要",
                           "raw": "## 📆 {kind_zh} 原文清单（按 publish_time 严格过滤）"},
        "kind_titles": {"daily": "竞品日报", "weekly": "竞品周报",
                        "monthly": "竞品月报", "yearly": "竞品年报"},
    },
}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[config] warn: failed to read {path}: {e}", file=sys.stderr)
        return {}


def _load_unified_file() -> dict | None:
    if not UNIFIED_PATH.exists():
        return None
    doc = _read_json(UNIFIED_PATH)
    return doc or None


def _merge_legacy() -> dict:
    """Build a unified-shaped config from the legacy split files."""
    out: dict[str, Any] = {"_meta": {"schema_version": "2.0", "migrated_from": "legacy-split-files"}}

    games_doc = _read_json(LEGACY["games"])
    out["games"] = {"games": games_doc.get("games", [])}

    cfg_doc = _read_json(LEGACY["config"])
    out["sources"] = {
        "wcrss": {"feed_url": cfg_doc.get("wcrss_feed_url", "")},
        "fetch_defaults": cfg_doc.get("fetch_defaults",
                                      DEFAULTS["sources"]["fetch_defaults"]),
    }

    scoring_doc = _read_json(LEGACY["scoring"])
    if scoring_doc:
        out["scoring"] = scoring_doc

    out["publish"] = _read_json(LEGACY["publish"]) or DEFAULTS["publish"]

    fmt_doc = _read_json(LEGACY["report_format"])
    report = json.loads(json.dumps(DEFAULTS["report"]))  # deep copy
    report["defaults"] = cfg_doc.get("report_defaults", report["defaults"])
    for k, v in fmt_doc.items():
        if not k.startswith("_"):
            report[k] = v
    out["report"] = report
    return out


def _normalize_sources(sec: dict) -> dict:
    """Fill missing sources sub-keys (v2.0 → v2.1 migration, in memory only).

    Ensures wcrss.enabled / official.enabled / extra_sources exist so that
    older unified configs keep working without manual edits.
    """
    default = DEFAULTS["sources"]
    wcrss = sec.get("wcrss")
    if not isinstance(wcrss, dict):
        wcrss = {}
    if "enabled" not in wcrss:
        wcrss["enabled"] = True
    sec["wcrss"] = wcrss

    official = sec.get("official")
    if not isinstance(official, dict):
        official = {}
    if "enabled" not in official:
        official["enabled"] = True
    sec["official"] = official

    if not isinstance(sec.get("extra_sources"), list) or not sec["extra_sources"]:
        sec["extra_sources"] = json.loads(json.dumps(default["extra_sources"]))
    else:
        # per-entry defaults
        for e in sec["extra_sources"]:
            if isinstance(e, dict):
                e.setdefault("enabled", True)
                e.setdefault("automation", "semi")
                e.setdefault("desc", "")
                e.setdefault("label", e.get("id", ""))

    if "fetch_defaults" not in sec:
        sec["fetch_defaults"] = json.loads(json.dumps(default["fetch_defaults"]))
    return sec


def _normalize_project(sec: dict, scoring_sec: dict) -> dict:
    """Fill project-section defaults (v2.1 → v2.2 migration, in memory only).

    - missing sub-dicts (target_users / calendar / knowledge) get default shapes
    - enabled defaults to True when the section carries real content
    - legacy scoring.mount_points.modules migrate into mount_modules
    """
    default = DEFAULTS["project"]
    for k in ("target_users", "calendar", "knowledge"):
        if not isinstance(sec.get(k), dict):
            sec[k] = json.loads(json.dumps(default[k]))
    # legacy scoring.mount_points.modules migrate into mount_modules (before the
    # enabled check, so migrated content counts as "has content")
    if not sec.get("mount_modules"):
        legacy = ((scoring_sec.get("mount_points") or {}).get("modules")) or []
        if legacy:
            sec["mount_modules"] = list(legacy)
    if "enabled" not in sec:
        has_content = any(sec.get(k) for k in (
            "name", "genre", "core_systems", "mount_modules",
            "monetization", "differentiation", "risk_redlines",
        ))
        sec["enabled"] = bool(has_content)
    return sec


def load_full(refresh: bool = False) -> dict:
    """Load the whole unified config (unified file > legacy fallback)."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    doc = _load_unified_file()
    if doc is None:
        doc = _merge_legacy()
    # fill defaults for missing sections
    for sec, default in DEFAULTS.items():
        if sec not in doc or not isinstance(doc[sec], dict):
            doc[sec] = json.loads(json.dumps(default))
    # v2.1: fill sources sub-keys (wcrss.enabled / official / extra_sources)
    doc["sources"] = _normalize_sources(doc.get("sources") or {})
    # v2.2: normalize project section (incl. legacy mount_points migration)
    doc["project"] = _normalize_project(doc.get("project") or {},
                                        doc.get("scoring") or {})
    _CACHE = doc
    return doc


_CACHE: dict | None = None


def get_section(name: str) -> dict:
    """Return one section (games/sources/scoring/publish/report)."""
    return load_full().get(name) or {}


def save_section(name: str, data: dict) -> None:
    """Persist one section back into the unified config file.

    Other sections are preserved. If only legacy files existed so far, they
    are merged into the unified file first (auto-migration); legacy files are
    left untouched.
    """
    doc = load_full(refresh=True)
    doc[name] = data
    UNIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = UNIFIED_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(UNIFIED_PATH)
    global _CACHE
    _CACHE = doc


# ---------- convenience helpers used by scripts ----------

def load_games() -> list[dict]:
    return get_section("games").get("games", []) or []


def load_scoring() -> dict:
    return get_section("scoring")


def load_publish() -> dict:
    return get_section("publish")


def load_report_format() -> dict:
    """Flat report format dict (content_tags / limits / ... ), defaults included."""
    return get_section("report")


def load_sources_flat() -> dict:
    """Legacy-flat view of the sources section (for wcrss_client compatibility).

    Returns {"wcrss_feed_url": ..., "fetch_defaults": {...}}.
    """
    sec = get_section("sources")
    wcrss = sec.get("wcrss") or {}
    return {
        "wcrss_feed_url": wcrss.get("feed_url", "")
        or wcrss.get("wcrss_feed_url", ""),  # tolerate old key
        "fetch_defaults": sec.get("fetch_defaults",
                                  DEFAULTS["sources"]["fetch_defaults"]),
    }


def source_enabled(source_id: str) -> bool:
    """Is a data source enabled in the unified config?

    Recognized ids:
      - "wechat" / "wcrss"  -> sources.wcrss.enabled
      - "official"          -> sources.official.enabled
      - anything else       -> looked up in sources.extra_sources[] by id
    Unknown ids default to True (so ad-hoc `ingest.py --source x` still works).
    """
    sec = get_section("sources")
    if source_id in ("wechat", "wcrss"):
        return bool((sec.get("wcrss") or {}).get("enabled", True))
    if source_id == "official":
        return bool((sec.get("official") or {}).get("enabled", True))
    for e in sec.get("extra_sources") or []:
        if isinstance(e, dict) and e.get("id") == source_id:
            return bool(e.get("enabled", True))
    return True


def extra_source_ids() -> list[dict]:
    """The extra_sources list (each: id/label/enabled/automation/desc)."""
    return get_section("sources").get("extra_sources") or []


# ---------- project profile (scoring reference frame) ----------

def load_project() -> dict:
    return get_section("project")


def project_enabled() -> bool:
    return bool(get_section("project").get("enabled", False))


def project_keywords(field_path: str) -> list[str]:
    """Resolve a dot-path inside the project section into a flat keyword list.

    e.g. "core_systems" -> ["血盟", ...]
         "target_users.overlap_keywords" -> ["国风", ...]
    Returns [] when the section is disabled, the path is missing, or the
    target is not a list.
    """
    sec = get_section("project")
    if not sec.get("enabled"):
        return []
    node: Any = sec
    for part in field_path.split("."):
        if not isinstance(node, dict):
            return []
        node = node.get(part)
    if isinstance(node, list):
        return [str(x) for x in node if x]
    return []


if __name__ == "__main__":
    # CLI sanity check: python scripts/config_loader.py
    doc = load_full()
    print(f"unified file : {UNIFIED_PATH} ({'EXISTS' if UNIFIED_PATH.exists() else 'missing'})")
    print(f"games        : {len(doc.get('games', {}).get('games', []))} entries")
    proj = doc.get("project", {})
    if proj.get("enabled"):
        n_kw = sum(len(project_keywords(p)) for p in (
            "core_systems", "target_users.overlap_keywords", "mount_modules",
            "monetization", "calendar.key_nodes", "differentiation", "risk_redlines"))
        print(f"project      : ON ({proj.get('name', '')} · {proj.get('genre', '')}"
              f" · {proj.get('stage', '')}, {n_kw} ref keywords)")
    else:
        print("project      : OFF (scoring runs on generic keywords only)")
    src = doc.get("sources", {})
    print(f"sources      : wcrss={'on' if source_enabled('wcrss') else 'off'}"
          f" (feed_url={'set' if src.get('wcrss', {}).get('feed_url') else 'EMPTY'})"
          f", official={'on' if source_enabled('official') else 'off'}")
    for e in extra_source_ids():
        print(f"                extra: {e.get('id', '?')}({e.get('label', '')})"
              f" = {'on' if e.get('enabled', True) else 'off'}")
    print(f"scoring dims : {len(doc.get('scoring', {}).get('dimensions', {}))}")
    print(f"publish keys : {sorted(doc.get('publish', {}).keys())}")
    print(f"report keys  : {sorted(doc.get('report', {}).keys())}")
    print("OK")
