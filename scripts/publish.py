"""Publish a generated report to Tencent Docs (and optional 企微 group bot).

Workflow:
  1. Read local Markdown report (data/reports/<kind>/<label>.md).
  2. Push to Tencent Docs (one document per <kind, label>):
     - daily: 每天一份新文档（label=YYYY-MM-DD）
     - weekly/monthly/yearly: 每期一份新文档
     - file_id 和 url 缓存在 publish.json 的 doc_ids/doc_urls 字典里（按 label 索引），
       同一 label 重跑会复用并"覆盖刷新"内容。
  3. 可选 catch-up：扫过去 N 天，把缺失的 daily 都推一遍。
  4. Optionally push Markdown summary card to 企微 group bot.

Usage:
  python scripts/publish.py daily                       # 默认 publish 昨天的 daily（工作日 12:00 跑昨天）
  python scripts/publish.py daily --date 2026-05-29     # 指定日期
  python scripts/publish.py daily --catch-up 7          # 扫过去 7 天，缺哪天补哪天
  python scripts/publish.py weekly                      # 上周
  python scripts/publish.py monthly                     # 上月
  python scripts/publish.py yearly                      # 去年

Token discovery (priority):
  1. ENV WCRSS_TDOC_TOKEN / TDOC_ACCESS_TOKEN
  2. config/competitor-watch.json -> publish.tencent_docs.access_token
  3. ~/.mcporter/credentials.json (entries with serverName=tencent-docs)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import config_loader  # noqa: E402

REPORT_DIR = ROOT / "data" / "reports"

CST = dt.timezone(dt.timedelta(hours=8))


# ---------- helpers ----------

def load_publish_config() -> dict:
    """Publish section of the unified config (config/competitor-watch.json)."""
    return config_loader.load_publish()


def save_publish_config(cfg: dict) -> None:
    """Write the publish section back into the unified config (other sections kept)."""
    config_loader.save_section("publish", cfg)


def discover_tdoc_token(cfg: dict) -> str | None:
    env = os.environ.get("WCRSS_TDOC_TOKEN") or os.environ.get("TDOC_ACCESS_TOKEN")
    if env:
        return env.strip()
    cfg_token = (cfg.get("tencent_docs", {}).get("access_token") or "").strip()
    if cfg_token:
        return cfg_token
    cred_path = Path(os.path.expanduser("~/.mcporter/credentials.json"))
    if cred_path.exists():
        try:
            cred = json.loads(cred_path.read_text(encoding="utf-8"))
            for entry in (cred.get("entries") or {}).values():
                if entry.get("serverName") == "tencent-docs":
                    tok = ((entry.get("tokens") or {}).get("access_token") or "").strip()
                    if tok:
                        return tok
        except Exception as e:  # noqa: BLE001
            print(f"[publish] warn: failed to read mcporter credentials: {e}", file=sys.stderr)
    return None


# ---------- date / label ----------

def today_cst() -> dt.date:
    return dt.datetime.now(CST).date()


def yesterday_cst() -> dt.date:
    return today_cst() - dt.timedelta(days=1)


def label_for(kind: str, anchor: dt.date | None = None) -> str:
    """Compute the report label string for filename / title.

    daily 默认 anchor = 昨天（"工作日 12:00 跑昨天"）。
    """
    if kind == "daily":
        d = anchor or yesterday_cst()
        return d.strftime("%Y-%m-%d")
    today = anchor or today_cst()
    if kind == "weekly":
        weekday = today.weekday()
        last_monday = today - dt.timedelta(days=weekday + 7)
        iso_year, iso_week, _ = last_monday.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if kind == "monthly":
        first = today.replace(day=1)
        last_of_prev = first - dt.timedelta(days=1)
        return last_of_prev.strftime("%Y-%m")
    if kind == "yearly":
        return str(today.year - 1)
    raise ValueError(f"unknown kind: {kind}")


# ---------- Tencent Docs MCP via HTTP ----------

def _mcp_call(endpoint: str, token: str, tool: str, arguments: dict, *, timeout: int = 90) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"tdoc HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"tdoc network error: {e.reason}")

    data = json.loads(text)
    if "error" in data and data["error"]:
        raise RuntimeError(f"tdoc rpc error: {data['error']}")
    result = data.get("result") or {}
    structured = result.get("structuredContent") or {}
    if structured.get("error"):
        raise RuntimeError(f"tdoc tool error: {structured['error']}")
    return structured


def tdoc_create(endpoint: str, token: str, title: str, mdx: str) -> dict:
    """Returns {file_id, url, ...}."""
    return _mcp_call(endpoint, token, "create_smartcanvas_by_mdx", {
        "title": title,
        "mdx": mdx,
    })


def tdoc_replace(endpoint: str, token: str, file_id: str, mdx: str) -> dict:
    """Replace whole document body via DELETE all top-level pages + INSERT_AFTER append."""
    # 1) 取所有顶层 block 的 id
    res = _mcp_call(endpoint, token, "smartcanvas.get_top_level_pages", {"file_id": file_id})
    pages = res.get("top_level_pages") or []
    # 把每个顶层节点的 children 全部摊平成一份待删 id 列表
    ids: list[str] = []
    for p in pages:
        if isinstance(p, dict):
            ids.extend(p.get("children") or [])
        elif isinstance(p, str):
            ids.append(p)
    # 2) 逐个删除
    for bid in ids:
        try:
            _mcp_call(endpoint, token, "smartcanvas.edit", {
                "file_id": file_id,
                "action": "DELETE",
                "id": bid,
            })
        except Exception as e:  # noqa: BLE001
            print(f"[publish] warn: delete block {bid} failed: {e}", file=sys.stderr)
    # 3) 追加新内容到末尾（id 留空）
    _mcp_call(endpoint, token, "smartcanvas.edit", {
        "file_id": file_id,
        "action": "INSERT_AFTER",
        "id": "",
        "content": mdx,
    })
    return {"file_id": file_id}


def tdoc_smart_publish(endpoint: str, token: str, file_id: str | None,
                        title: str, mdx: str) -> dict:
    """If file_id is provided, try replace; on failure fall back to creating a new doc."""
    if file_id:
        try:
            tdoc_replace(endpoint, token, file_id, mdx)
            # 注意：更新成功只返回 file_id，url 不再重新拿
            # （url 由调用方从 doc_urls 缓存里取，避免 raw file_id 拼出错误链接）
            return {"file_id": file_id, "mode": "updated"}
        except Exception as e:  # noqa: BLE001
            print(f"[publish] replace failed ({e}); falling back to create new", file=sys.stderr)
    res = tdoc_create(endpoint, token, title, mdx)
    res["mode"] = "created"
    return res


# ---------- folder organization ----------

def tdoc_list_folder(endpoint: str, token: str, folder_id: str) -> list[dict]:
    """Return folder children: [{id, title, is_folder, url}, ...]. Handles paging."""
    out: list[dict] = []
    start = 0
    while True:
        args: dict = {"folder_id": folder_id}
        if start:
            args["start"] = start
        res = _mcp_call(endpoint, token, "manage.folder_list", args)
        items = res.get("list") or []
        out.extend(items)
        if res.get("finish") is True or not items:
            break
        start += len(items)
        if start > 500:
            break
    return out


def tdoc_create_folder(endpoint: str, token: str, parent_id: str, title: str) -> str:
    res = _mcp_call(endpoint, token, "manage.create_file", {
        "file_type": "folder",
        "title": title,
        "parent_id": parent_id,
    })
    # different shapes seen
    fid = res.get("file_id") or res.get("id") or (res.get("file") or {}).get("id")
    if not fid:
        raise RuntimeError(f"create_folder returned no id: {res}")
    return fid


def tdoc_move_file(endpoint: str, token: str, file_id: str, target_folder_id: str) -> None:
    _mcp_call(endpoint, token, "manage.move_file", {
        "file_id": file_id,
        "target_folder_id": target_folder_id,
    })


def month_folder_name(label: str, kind: str) -> str:
    """daily=YYYY-MM-DD -> YYYY-M; weekly/monthly/yearly 复用同样逻辑（基于 anchor 月）."""
    if kind == "daily":
        d = dt.datetime.strptime(label, "%Y-%m-%d").date()
    elif kind == "weekly":
        # ISO week label like 2026-W22 → 取该周第一天的月份
        m = re.match(r"(\d{4})-W(\d{1,2})", label)
        if not m:
            return label
        y, w = int(m.group(1)), int(m.group(2))
        d = dt.date.fromisocalendar(y, w, 1)
    elif kind == "monthly":
        # YYYY-MM
        m = re.match(r"(\d{4})-(\d{1,2})", label)
        if not m:
            return label
        return f"{int(m.group(1))}-{int(m.group(2))}"
    elif kind == "yearly":
        return label  # 直接 YYYY
    else:
        return label
    return f"{d.year}-{d.month}"


def ensure_month_folder(endpoint: str, token: str, root_id: str, month_name: str,
                         cache: dict) -> str:
    """Return the folder id for `month_name` under `root_id`. Uses + updates cache."""
    if month_name in cache and cache[month_name]:
        return cache[month_name]
    # 列举 root 下的子文件夹
    children = tdoc_list_folder(endpoint, token, root_id)
    for c in children:
        if c.get("is_folder") and c.get("title") == month_name:
            cache[month_name] = c["id"]
            return c["id"]
    # 不存在 → 创建
    print(f"    [folder] create month folder '{month_name}' under {root_id}")
    fid = tdoc_create_folder(endpoint, token, root_id, month_name)
    cache[month_name] = fid
    return fid


# ---------- WeCom group bot ----------

def push_wecom_markdown(webhook_url: str, content: str, mention_all: bool = False) -> bool:
    if mention_all:
        content = "<@all>\n" + content
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content[:4000]},  # 企微 markdown 上限 4096
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=body,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            txt = resp.read().decode("utf-8", "ignore")
            return '"errcode":0' in txt
    except Exception as e:  # noqa: BLE001
        print(f"[publish] wecom push failed: {e}", file=sys.stderr)
        return False


def build_wecom_summary(md: str, kind: str, label: str, doc_url: str, max_chars: int) -> str:
    # 提取 概览 + 评分快照 / TOP 事件
    title_map = {"daily": "竞品日报", "weekly": "竞品周报",
                  "monthly": "竞品月报", "yearly": "竞品年报"}
    head = f"# 📊 {title_map[kind]} {label}\n\n"
    parts = re.split(r"\n## ", "\n" + md, maxsplit=2)
    snippet = parts[1] if len(parts) > 1 else md[:600]
    snippet = "## " + snippet
    body = (head + snippet).rstrip()
    if len(body) > max_chars - 100:
        body = body[: max_chars - 100].rstrip() + "..."
    body += f"\n\n👉 [查看完整报告]({doc_url})"
    return body


# ---------- main ----------

def _get_kind_dict(cfg: dict, key: str, kind: str) -> dict:
    """Return / init nested dict: cfg.tencent_docs[<key>][<kind>] = {label: value}."""
    tdocs = cfg.setdefault("tencent_docs", {})
    container = tdocs.setdefault(key, {})
    bucket = container.get(kind)
    # backward compat: if it was a single string from older versions, migrate to dict
    if isinstance(bucket, str):
        legacy = bucket
        bucket = {}
        if legacy:
            # 没法知道当时是哪个 label 的，只能丢进 _legacy_ 桶里方便人工识别
            bucket["_legacy_"] = legacy
        container[kind] = bucket
    elif bucket is None:
        bucket = {}
        container[kind] = bucket
    return bucket


def publish_one(cfg: dict, kind: str, label: str, *,
                 dry_run: bool = False, no_wecom: bool = False,
                 force_new: bool = False,
                 auto_refresh_dirty: bool = True) -> tuple[bool, str]:
    """Publish a single (kind,label). Returns (success, doc_url).

    auto_refresh_dirty: 当本地 md 标记 dirty（或 label==今天）时，先调 report.py
                       的 generate_one 重生再发，避免发出过期版本。
    """
    md_path = REPORT_DIR / kind / f"{label}.md"

    if auto_refresh_dirty and not dry_run:
        try:
            from importlib import import_module
            r = import_module("report")
            anchor_d = (dt.datetime.strptime(label, "%Y-%m-%d").date()
                        if kind == "daily" else None)
            is_dirty_label = r.is_dirty_label(kind, label)
            disk_dirty = r.file_is_dirty(md_path) if md_path.exists() else False
            if is_dirty_label or disk_dirty:
                _, n, status = r.generate_one(kind, anchor_d, None, force=False)
                if status != "skipped":
                    print(f"  [refresh] {kind} {label} -> {status} ({n} 篇)")
        except Exception as e:  # noqa: BLE001
            print(f"  [refresh] warn: {e}", file=sys.stderr)

    if not md_path.exists():
        print(f"  [skip] {kind} {label}: report not found at {md_path}")
        return False, ""

    md = md_path.read_text(encoding="utf-8")
    print(f"  [{kind} {label}] size={len(md)} chars")

    doc_url = ""
    file_id = ""

    tdoc_cfg = cfg.get("tencent_docs", {})
    if tdoc_cfg.get("enabled"):
        token = discover_tdoc_token(cfg)
        if not token:
            print("    tdoc token not found; skip tdoc step")
        else:
            endpoint = tdoc_cfg.get("endpoint", "https://docs.qq.com/openapi/mcp")
            ids_bucket = _get_kind_dict(cfg, "doc_ids", kind)
            urls_bucket = _get_kind_dict(cfg, "doc_urls", kind)
            existing_id = "" if force_new else ids_bucket.get(label, "")
            cached_url = urls_bucket.get(label, "")

            title = tdoc_cfg.get("title_template", {}).get(
                kind, "{label}").format(label=label)

            if dry_run:
                action = "update" if existing_id else "create"
                print(f"    [dry-run] would {action} title={title!r} (existing_id={existing_id or '-'})")
                return True, cached_url
            else:
                if existing_id:
                    res = tdoc_smart_publish(endpoint, token, existing_id, title, md)
                else:
                    res = tdoc_create(endpoint, token, title, md)
                    res["mode"] = "created"
                file_id = res.get("file_id") or existing_id
                doc_url = res.get("url") or cached_url or (
                    f"https://docs.qq.com/aio/{file_id}" if file_id else "")
                print(f"    tdoc {res.get('mode')}: file_id={file_id}")
                print(f"    tdoc url:    {doc_url}")

                if file_id:
                    ids_bucket[label] = file_id
                    if res.get("url"):
                        urls_bucket[label] = res["url"]
                    save_publish_config(cfg)

                # 把新文档归档到「月份子文件夹」（仅 created 模式需要 move；
                # updated 模式假定上次已归档好，不重复 move）
                root_folder_id = (tdoc_cfg.get("root_folder_id") or "").strip()
                if root_folder_id and file_id and res.get("mode") == "created":
                    try:
                        cache = tdoc_cfg.setdefault("month_folders", {})
                        month_name = month_folder_name(label, kind)
                        target = ensure_month_folder(endpoint, token, root_folder_id,
                                                     month_name, cache)
                        tdoc_move_file(endpoint, token, file_id, target)
                        print(f"    [folder] moved -> {month_name} ({target})")
                        save_publish_config(cfg)
                    except Exception as e:  # noqa: BLE001
                        print(f"    [folder] warn: archive failed: {e}", file=sys.stderr)

    we_cfg = cfg.get("wecom_bot", {})
    if (not no_wecom) and we_cfg.get("enabled") and we_cfg.get("webhook_url"):
        summary = build_wecom_summary(
            md, kind, label,
            doc_url or "(no doc url)",
            we_cfg.get("summary_max_chars", 1500),
        )
        if dry_run:
            print("    [dry-run] would push WeCom markdown:")
            print(summary[:500])
        else:
            ok = push_wecom_markdown(we_cfg["webhook_url"], summary)
            print(f"    wecom push: {'ok' if ok else 'failed'}")
    elif we_cfg.get("enabled") and not we_cfg.get("webhook_url"):
        print("    wecom enabled but no webhook_url; skip")

    return True, doc_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish report to Tencent Docs / WeCom")
    parser.add_argument("kind", choices=["daily", "weekly", "monthly", "yearly"])
    parser.add_argument("--date",
                        help="anchor date YYYY-MM-DD. daily 默认=昨天；其它默认=今天回看")
    parser.add_argument("--catch-up", type=int, metavar="N", default=0,
                        help="只对 daily 有效：扫过去 N 天，缺哪天的本地 md 就跳过、有 md 但还没推过的（doc_ids 里没缓存）就推一份。已推过的同 label 默认不重推（用 --force-new 强制重推为新文档）。")
    parser.add_argument("--force-new", action="store_true",
                        help="即使 doc_ids 已缓存也忽略，重新创建新文档（不推荐）")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen")
    parser.add_argument("--no-wecom", action="store_true", help="skip wecom push even if enabled")
    args = parser.parse_args()

    cfg = load_publish_config()
    anchor = dt.datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    if args.kind != "daily" or args.catch_up <= 0:
        label = label_for(args.kind, anchor)
        print(f"[publish] kind={args.kind} label={label}")
        ok, _ = publish_one(cfg, args.kind, label,
                            dry_run=args.dry_run, no_wecom=args.no_wecom,
                            force_new=args.force_new)
        print("[publish] done." if ok else "[publish] failed.")
        return 0 if ok else 1

    # daily catch-up：扫过去 N 天
    base = anchor or yesterday_cst()
    print(f"[publish] catch-up daily: 扫 {args.catch_up} 天 (anchor={base})")
    succ = miss = 0
    for offset in reversed(range(args.catch_up)):  # 旧到新
        d = base - dt.timedelta(days=offset)
        label = d.strftime("%Y-%m-%d")
        ok, _ = publish_one(cfg, "daily", label,
                            dry_run=args.dry_run, no_wecom=args.no_wecom,
                            force_new=args.force_new)
        if ok:
            succ += 1
        else:
            miss += 1
    print(f"[publish] catch-up done: 成功={succ}, 缺 md={miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
