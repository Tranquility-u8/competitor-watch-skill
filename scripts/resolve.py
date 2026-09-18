"""Resolve the unified config's game list into derived configs.

User maintains ONLY: config/competitor-watch.json → games（顶层游戏列表）.
Agent auto-maintains (each gated by its sources.*.enabled switch):
  - config/derived/wcrss.json    (mp_id mapping; reflects current wcrss subscriptions filtered by games)
  - config/derived/hykb.json     (auto-resolved fid via m.3839.com search)
  - config/derived/taptap.json   (manual; resolve fills in placeholders for missing entries)
  - config/derived/official.json (auto placeholder per game — the official source is
                                  AUTO-CONFIGURED whenever the games list changes; the user
                                  never has to add URLs by hand, they can optionally upgrade
                                  an entry with rss / selectors later)

For wcrss subscription deltas (games-without-mp_id), this script PRINTS a plan.
It does NOT subscribe automatically — the agent must ask the user for confirmation.

Usage:
  python scripts/resolve.py            # plan + write derived/* (no wcrss subscribe)
  python scripts/resolve.py --check    # only print what would change
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import config_loader  # noqa: E402
from wcrss_client import fetch_articles  # noqa: E402

CONFIG_DIR = ROOT / "config"
DERIVED_DIR = CONFIG_DIR / "derived"

UA_PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
UA_M  = "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0) AppleWebKit/605.1.15 Mobile"


# ---------- helpers ----------

def http_get(url: str, ua: str = UA_PC, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": ua, "Accept-Encoding": "identity"},
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return f"__ERR__:{e}"


def load_games() -> dict:
    """Games section of the unified config (config/competitor-watch.json)."""
    return {"games": config_loader.load_games()}


def write_derived(name: str, payload) -> None:
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    out = DERIVED_DIR / f"{name}.json"
    with out.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  wrote {out}")


# ---------- resolvers ----------

def resolve_wcrss(games: list[dict]) -> tuple[list[dict], list[dict]]:
    """Match game.name (or aliases) against current wcrss subscriptions.

    Returns: (resolved_entries, missing_games_to_subscribe).
    Each resolved entry: {"name": <game.name>, "mp_id": ..., "nickname": ..., "priority": ...}.
    """
    print("[wcrss] fetching publishers from RSS feed ...")
    # 新版 wcrss_client 通过解析 RSS item 推断 publishers，需要拉到足够多 items 才能覆盖全部公众号
    payload = fetch_articles(recent_days=365, num=2000)
    pubs = payload.get("publishers", []) or []
    by_nick = {p["nickname"]: p for p in pubs}
    print(f"[wcrss] publishers seen in feed: {len(pubs)}")

    resolved: list[dict] = []
    missing: list[dict] = []
    used_mp_ids: set[str] = set()  # 一个公众号至多被一个游戏占用
    for g in games:
        keys = [g["name"]] + list(g.get("aliases") or [])
        match: dict | None = None
        # 1) 精确昵称匹配
        for k in keys:
            if k in by_nick and by_nick[k]["mp_id"] not in used_mp_ids:
                match = by_nick[k]; break
        # 2) 子串匹配（仅当还未占用的公众号）
        if not match:
            for k in keys:
                for nick, p in by_nick.items():
                    if p["mp_id"] in used_mp_ids:
                        continue
                    if k and (k in nick or nick in k):
                        match = p; break
                if match:
                    break
        if match:
            used_mp_ids.add(match["mp_id"])
            resolved.append({
                "name": g["name"],
                "mp_id": match["mp_id"],
                "nickname": match["nickname"],
                "priority": g.get("priority", "normal"),
            })
        else:
            missing.append(g)
    return resolved, missing


def hykb_search(name: str) -> tuple[str | None, str | None, bool]:
    """Returns (fid, label, high_confidence)."""
    url = f"https://m.3839.com/search/?ac=search_result&q={urllib.parse.quote(name)}"
    html = http_get(url, ua=UA_M)
    if html.startswith("__ERR__"):
        return None, html, False
    blocks = re.findall(
        r'<a[^>]*href="[^"]*?/a/(\d+)\.html?[^"]*"[^>]*>(.*?)</a>',
        html, re.S,
    )
    NOISE = ("捏脸", "数据库", "工具", "助手", "攻略", "盒子", "礼包")

    # 把 name 拆成"骨干"和可选后缀；只要骨干 + 至少一个非噪声 label 命中就算 OK
    stem = name
    for suf in ("手游", "(官服)", "（官服）", "·"):
        stem = stem.replace(suf, "")
    stem = stem.strip()

    def clean(body: str) -> str:
        return re.sub(r"<[^>]+>", "", body)

    # 1) 含 stem + 官服/官方 + 不含 noise → 强匹配
    for fid, body in blocks:
        text = clean(body)
        if stem in text and ("官服" in text or "官方" in text) and not any(n in text for n in NOISE):
            return fid, _normalize(text), True
    # 2) 含 stem + 不含 noise → 中等
    for fid, body in blocks:
        text = clean(body)
        if stem in text and not any(n in text for n in NOISE):
            return fid, _normalize(text), True
    # 3) 含 stem 但全是 noise → 跳过
    if blocks:
        text = clean(blocks[0][1])
        return None, _normalize(text) + "  (置信不足，可能噪声)", False
    return None, None, False


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:60]


def resolve_hykb(games: list[dict]) -> list[dict]:
    print("[hykb] resolving via m.3839.com search ...")
    out: list[dict] = []
    for g in games:
        fid, label, ok = hykb_search(g["name"])
        if fid:
            out.append({
                "name": g["name"],
                "fid": int(fid),
                "match_label": label,
                "priority": g.get("priority", "normal"),
            })
            print(f"  {g['name']:14s} -> fid={fid} ({label})")
        else:
            out.append({
                "name": g["name"],
                "fid": None,
                "match_label": label,
                "priority": g.get("priority", "normal"),
                "_hint": (
                    f"hykb 自动搜索置信不足。请到 https://m.3839.com/search/?ac=search_result"
                    f"&q={urllib.parse.quote(g['name'])} 手动找到游戏，把 URL 里的 fid 填入此处。"
                ),
            })
            print(f"  {g['name']:14s} -> 跳过 ({label or '无搜索结果'})")
        time.sleep(1.2)
    return out


def resolve_official(games: list[dict], existing: dict[str, dict] | None = None) -> list[dict]:
    """Generate or preserve official entries.

    幂等策略：若 derived/official.json 中已有同名 entry 且填了 url（人工/agent 已配置过），
    直接保留所有字段（url / selectors / rss / _note ...），仅更新 priority。
    新游戏仍创建空的 snapshot 占位。
    """
    print("[official] generating placeholder entries (mode C: link snapshot) ...")
    existing = existing or {}
    out: list[dict] = []
    preserved = 0
    for g in games:
        prev = existing.get(g["name"])
        if prev and prev.get("url"):
            # 已配置过，保留全部字段，仅同步 priority
            merged = dict(prev)
            merged["priority"] = g.get("priority", prev.get("priority", "normal"))
            out.append(merged)
            preserved += 1
        else:
            out.append({
                "name": g["name"],
                "url": "",
                "priority": g.get("priority", "normal"),
                "mode": "snapshot",
                "_hint": "把官网 / 官方公告页 URL 填到 url 字段；如果站点有 rss 字段或 selectors，加上即可（参考 SKILL.md）",
            })
    print(f"  preserved {preserved} existing entries (with url/selectors), "
          f"created {len(out) - preserved} new placeholders")
    return out


def resolve_taptap_skeleton(games: list[dict], existing: dict[str, dict]) -> list[dict]:
    """TapTap requires user-provided app_id (search API not stable).

    Strategy: keep existing mappings; for new games, write entry with app_id=null
    and a hint URL the user can click to find the id.
    """
    out: list[dict] = []
    for g in games:
        prev = existing.get(g["name"])
        if prev and prev.get("app_id"):
            out.append(prev)
        else:
            search_url = (
                "https://www.taptap.cn/search/" + urllib.parse.quote(g["name"])
            )
            out.append({
                "name": g["name"],
                "app_id": None,
                "priority": g.get("priority", "normal"),
                "_hint": f"打开 {search_url} ，点进游戏详情页，URL 末段数字就是 app_id",
            })
    return out


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve unified-config games into derived configs")
    parser.add_argument("--check", action="store_true",
                        help="only print delta plan, do not write derived/*")
    args = parser.parse_args()

    games_doc = load_games()
    games = games_doc.get("games", []) or []
    games_only = [g for g in games if g.get("type", "game") == "game"]
    industry = [g for g in games if g.get("type") == "industry"]
    print(f"games (unified config) -> {len(games)} entries ({len(games_only)} game / {len(industry)} industry)\n")

    # 各渠道按统一配置 sources.*.enabled 开关决定是否解析；
    # 关闭的渠道保留现有 derived 文件不动，重新开启后重跑本脚本即可。
    wcrss_on = config_loader.source_enabled("wcrss")
    official_on = config_loader.source_enabled("official")
    hykb_on = config_loader.source_enabled("hykb")
    taptap_on = config_loader.source_enabled("taptap")

    # 1) wcrss — 开关关闭或未配置 RSS 服务时跳过并保留现有 derived 映射
    wcrss_resolved: list[dict] = []
    wcrss_missing: list[dict] = list(games)
    if not wcrss_on:
        print("[wcrss] disabled in config (sources.wcrss.enabled=false), skip — derived/wcrss.json 保持不变")
    else:
        try:
            wcrss_resolved, wcrss_missing = resolve_wcrss(games)
        except Exception as e:  # noqa: BLE001
            print(f"[wcrss] skip ({e})")
            prev_path = DERIVED_DIR / "wcrss.json"
            if prev_path.exists():
                try:
                    prev = json.loads(prev_path.read_text(encoding="utf-8"))
                    wcrss_resolved = prev.get("entries", []) or []
                    covered = {x["name"] for x in wcrss_resolved}
                    wcrss_missing = [g for g in games if g["name"] not in covered]
                    print(f"[wcrss] kept {len(wcrss_resolved)} existing entries from derived/wcrss.json")
                except Exception:  # noqa: BLE001
                    pass
            else:
                print("[wcrss] no existing derived/wcrss.json either; all games treated as missing")
        print(f"[wcrss] resolved: {len(wcrss_resolved)}, missing subscription: {len(wcrss_missing)}")
        if wcrss_missing:
            print("[wcrss] === Subscription Plan (NEEDS USER CONFIRMATION) ===")
            for g in wcrss_missing:
                print(f"  + 需要订阅公众号: {g['name']}（aliases: {g.get('aliases', [])}）")
            print("    → 请在 RSS 服务后台手动添加订阅，或回复 agent 让其引导。\n")

    # 2) hykb — 只对 type=game 走，行业号没有评分
    if hykb_on:
        hykb_resolved = resolve_hykb(games_only)
        print(f"[hykb] resolved: {len(hykb_resolved)}/{len(games_only)}\n")
    else:
        hykb_resolved = None
        print("[hykb] disabled in config (sources.extra_sources[].enabled=false), skip — derived/hykb.json 保持不变\n")

    # 3) official — 只对 type=game；幂等保留已配置的 url/selectors
    #    官网源在游戏列表更新后自动配置（生成占位条目），用户无需手动填 URL
    if official_on:
        existing_official_path = DERIVED_DIR / "official.json"
        existing_official: dict[str, dict] = {}
        if existing_official_path.exists():
            try:
                doc = json.loads(existing_official_path.read_text(encoding="utf-8"))
                for e in doc.get("entries", []):
                    existing_official[e["name"]] = e
            except Exception as ex:  # noqa: BLE001
                print(f"[official] warn: existing config 读取失败 ({ex})，将重新生成")
        official_resolved = resolve_official(games_only, existing_official)
    else:
        official_resolved = None
        print("[official] disabled in config (sources.official.enabled=false), skip — derived/official.json 保持不变")

    # 4) taptap — 只对 type=game
    if taptap_on:
        existing_taptap_path = DERIVED_DIR / "taptap.json"
        existing_taptap: dict[str, dict] = {}
        if existing_taptap_path.exists():
            for it in json.loads(existing_taptap_path.read_text(encoding="utf-8")).get("entries", []):
                existing_taptap[it["name"]] = it
        taptap_resolved = resolve_taptap_skeleton(games_only, existing_taptap)
        print(f"[taptap] entries: {len(taptap_resolved)} "
              f"({sum(1 for x in taptap_resolved if x.get('app_id'))} 已配)")
    else:
        taptap_resolved = None
        print("[taptap] disabled in config (sources.extra_sources[].enabled=false), skip — derived/taptap.json 保持不变")

    if args.check:
        print("\n[check] dry-run, not writing derived/*. Run without --check to apply.")
        return 0

    # write —— 保留顶层注释字段（_doc / _strategy / _probe_status 等），仅替换 entries；
    #          关闭的渠道不写（保留旧文件）
    print("\n[write] persisting derived/*.json")

    def _merge_keep_meta(path_name: str, new_data: dict) -> dict:
        """读取已有文件的顶层 _xxx 字段并合并到新 data 上方。"""
        path = DERIVED_DIR / f"{path_name}.json"
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                meta = {k: v for k, v in old.items() if k.startswith("_")}
                return {**meta, **new_data}
            except Exception:  # noqa: BLE001
                pass
        return new_data

    if wcrss_on:
        write_derived("wcrss", _merge_keep_meta("wcrss", {"entries": wcrss_resolved, "missing": [g["name"] for g in wcrss_missing]}))
    if hykb_resolved is not None:
        write_derived("hykb", _merge_keep_meta("hykb", {"entries": hykb_resolved}))
    if official_resolved is not None:
        write_derived("official", _merge_keep_meta("official", {"entries": official_resolved}))
    if taptap_resolved is not None:
        write_derived("taptap", _merge_keep_meta("taptap", {"entries": taptap_resolved}))

    print("\n[done] derived configs ready. Run `python scripts/ingest.py` to fetch.")
    if official_on and official_resolved is not None:
        n_placeholder = sum(1 for e in official_resolved if not e.get("url"))
        if n_placeholder:
            print(f"[official] {n_placeholder} 个竞品的官网条目已自动生成占位（无需手动配置）。")
            print("            想升级为 RSS/结构化抓取时，再把 rss / selectors 填进 derived/official.json 即可。")
    if wcrss_missing and wcrss_on:
        print(f"\n⚠️  {len(wcrss_missing)} 个游戏的微信公众号尚未订阅（见上方 Subscription Plan），")
        print("   是否需要我引导你添加订阅？请明确回复同意/不同意。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
