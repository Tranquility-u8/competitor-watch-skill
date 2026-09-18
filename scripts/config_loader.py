"""Unified configuration loader for competitor-watch (schema v2).

All configuration lives in ONE file: config/competitor-watch.json

    {
      "_meta":   { "schema_version": "2.0", ... },
      "games":   { "games": [ {name, aliases, genre, priority, type, notes}, ... ] },
      "sources": { "wcrss": {"feed_url": ...}, "fetch_defaults": {"recent_days":3,"num":50} },
      "scoring": { "weights", "tiers", "dimensions", "source_weight_bias",
                   "industry_filter", "mount_points" },
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
    "games": {"games": []},
    "sources": {
        "wcrss": {"feed_url": ""},
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


if __name__ == "__main__":
    # CLI sanity check: python scripts/config_loader.py
    doc = load_full()
    print(f"unified file : {UNIFIED_PATH} ({'EXISTS' if UNIFIED_PATH.exists() else 'missing'})")
    print(f"games        : {len(doc.get('games', {}).get('games', []))} entries")
    print(f"scoring dims : {len(doc.get('scoring', {}).get('dimensions', {}))}")
    print(f"publish keys : {sorted(doc.get('publish', {}).keys())}")
    print(f"report keys  : {sorted(doc.get('report', {}).keys())}")
    print("OK")
