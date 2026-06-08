"""官方论坛 / 官网 source — fully config-driven.

Each entry in `config/competitors.json -> 'official'` looks like:

  {
    "name": "原神",
    "url":  "https://ys.mihoyo.com/main/news/list",
    "rss":  "https://example.com/feed.xml",   // optional, if provided uses RSS path
    "selectors": {                              // optional HTML fallback selectors
      "item":  "ul.news-list li",
      "title": "a",
      "link":  "a@href",
      "date":  "span.date"
    }
  }

Path priority:
  1. If `rss` is set → parse RSS/Atom (stdlib only).
  2. Else if `selectors` is set → BeautifulSoup HTML scrape (requires bs4).
  3. Else → just record the URL itself as one "homepage snapshot" article (lowest fidelity).
"""
from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import connect, insert_article_if_new  # noqa: E402

from . import _common as C  # noqa: E402

SOURCE = "official"


def _parse_dt(text: str | None) -> int:
    if not text:
        return C.now_ts()
    text = text.strip()
    # try RFC 822 (RSS pubDate)
    try:
        return int(parsedate_to_datetime(text).timestamp())
    except Exception:  # noqa: BLE001
        pass
    # try ISO formats
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d",
                "%Y.%m.%d", "%Y年%m月%d日", "%Y年%m月%d"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    return C.now_ts()


def _try_parse_dt(text: str | None) -> int | None:
    """成功时返回时间戳，失败时返回 None（不 fallback now）。

    会先用 regex 在文本里抽取日期片段，再尝试解析。
    """
    if not text:
        return None
    # 抽取日期子串
    m = re.search(
        r"20\d{2}[-/.年]\s?\d{1,2}[-/.月]\s?\d{1,2}日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
        text,
    )
    if not m:
        # 尝试 RFC822
        try:
            return int(parsedate_to_datetime(text.strip()).timestamp())
        except Exception:  # noqa: BLE001
            return None
    candidate = m.group()
    # 标准化分隔符
    cand = candidate.replace("/", "-").replace(".", "-").replace("年", "-").replace("月", "-").replace("日", "")
    cand = re.sub(r"\s+", " ", cand).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d", "%Y-%m-%d-"):
        try:
            return int(datetime.strptime(cand, fmt).timestamp())
        except ValueError:
            continue
    return None


def _parse_rss(xml_text: str, base_url: str | None = None) -> list[dict]:
    """Parse RSS 2.0 or Atom into a list of {title, url, description, publish_time}."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[dict] = []
    # RSS 2.0
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag == "item":
            title = it.findtext("title", default="").strip()
            link = it.findtext("link", default="").strip()
            desc = (it.findtext("description", default="") or "").strip()
            date = it.findtext("pubDate", default="")
            if title and link:
                items.append({
                    "title": title,
                    "url": link if link.startswith("http") else urljoin(base_url or "", link),
                    "description": re.sub(r"<[^>]+>", "", desc)[:400],
                    "publish_time": _parse_dt(date),
                })
        elif tag == "entry":  # Atom
            title = it.findtext("{http://www.w3.org/2005/Atom}title", default="").strip()
            link_el = it.find("{http://www.w3.org/2005/Atom}link")
            link = (link_el.attrib.get("href") if link_el is not None else "").strip()
            summary = (it.findtext("{http://www.w3.org/2005/Atom}summary", default="") or "").strip()
            date = it.findtext("{http://www.w3.org/2005/Atom}updated", default="") or \
                   it.findtext("{http://www.w3.org/2005/Atom}published", default="")
            if title and link:
                items.append({
                    "title": title,
                    "url": link,
                    "description": re.sub(r"<[^>]+>", "", summary)[:400],
                    "publish_time": _parse_dt(date),
                })
    return items


def _select(soup, selector: str):
    """selector with optional `@attr` suffix (e.g. 'a@href')."""
    if "@" in selector:
        css, attr = selector.split("@", 1)
        nodes = soup.select(css)
        return [n.get(attr, "").strip() if n else "" for n in nodes]
    return [n.get_text(" ", strip=True) for n in soup.select(selector)]


def _scrape_html(html: str, base_url: str, selectors: dict) -> list[dict]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        print("  [official] bs4 not installed; skip HTML mode (pip install beautifulsoup4)")
        return []
    soup = BeautifulSoup(html, "html.parser")
    item_sel = selectors.get("item")
    if not item_sel:
        return []
    items: list[dict] = []
    for node in soup.select(item_sel):
        try:
            # title: 若 selector 为空字符串，使用 item 自身文本
            title_sel = selectors.get("title", "")
            title_el = node.select_one(title_sel) if title_sel else node
            # link: 若 css 段为空（如 "@href"），从 item 自身取 attr
            link_attr = selectors.get("link", "a@href")
            link_css, link_attr_name = (link_attr.split("@", 1) + ["href"])[:2]
            if link_css:
                link_el = node.select_one(link_css)
                # 兼容 item 自身就是 <a> 的情况
                if link_el is None and node.name == link_css:
                    link_el = node
            else:
                link_el = node
            date_el = (node.select_one(selectors["date"]) if selectors.get("date") else None)
            # 若 date selector 找不到，尝试在 item 自身文本里 regex 抽取
            if date_el is None and node:
                m = re.search(
                    r"20\d{2}[-/.]\s?\d{1,2}[-/.]\s?\d{1,2}(?:\s+\d{1,2}:\d{2})?",
                    node.get_text(" ", strip=True),
                )
                date_text = m.group() if m else None
            else:
                date_text = date_el.get_text(strip=True) if date_el else None

            title = title_el.get_text(" ", strip=True) if title_el else ""
            link = link_el.get(link_attr_name, "") if link_el else ""
            if link and not link.startswith("http"):
                link = urljoin(base_url, link)
            if not (title and link):
                continue
            pt = _try_parse_dt(date_text)
            # require_date=True 时，没解析到日期的项丢弃（剔除 nav/无效项）
            if pt is None and selectors.get("require_date"):
                continue
            items.append({
                "title": title,
                "url": link,
                "description": "",
                "publish_time": pt if pt is not None else C.now_ts(),
            })
        except Exception as e:  # noqa: BLE001
            print(f"  [official] selector parse warn: {e}")
            continue
    return items


def run(**_: object) -> tuple[int, int]:
    entries = C.load_derived("official").get("entries", []) or []
    if not entries:
        print("  no official sources configured (run resolve.py to generate config/derived/official.json)")
        return 0, 0
    order = {n: i for i, n in enumerate(C.game_priority_order())}
    entries.sort(key=lambda e: order.get(e["name"], 999))

    new = 0
    dup = 0
    with connect(C.DB_PATH) as conn:
        for entry in entries:
            label = entry["name"]
            articles: list[dict] = []
            url = (entry.get("url") or "").strip()
            if not url and not entry.get("rss"):
                # 占位条目（resolve.py 生成的 mode=snapshot 但 url 还没填）
                print(f"  [official:{label}] skip (no url; fill config/derived/official.json)")
                continue
            try:
                if entry.get("rss"):
                    text = C.http_get_text(entry["rss"])
                    articles = _parse_rss(text, base_url=url)
                elif entry.get("selectors"):
                    text = C.http_get_text(url)
                    articles = _scrape_html(text, url, entry["selectors"])
                else:
                    articles = [{
                        "title": f"[官网快照] {label}",
                        "url": url,
                        "description": "未配置 rss 或 selectors，仅记录官网链接快照",
                        "publish_time": C.now_ts(),
                    }]
            except Exception as e:  # noqa: BLE001
                print(f"  [official:{label}] FAILED: {e}")
                continue

            game_new = 0
            for art in articles:
                if not art.get("url") or not art.get("title"):
                    continue
                art["mp_id"] = ""
                inserted = insert_article_if_new(
                    conn,
                    art,
                    None,
                    source=SOURCE,
                    competitor=label,
                    extra_json=json.dumps({"site": entry.get("url")}, ensure_ascii=False),
                )
                if inserted:
                    new += 1
                    game_new += 1
                else:
                    dup += 1
            print(f"  [official:{label}] items={len(articles)} new={game_new}")
            time.sleep(0.5)
    return new, dup
