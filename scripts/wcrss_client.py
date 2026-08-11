"""WeChat RSS client.

Points to a configurable aggregated RSS endpoint that includes ALL of the
user's subscriptions. Set via config/config.json → wcrss_feed_url or
env WCRSS_FEED_URL. The previous sapi/articles endpoint is deprecated.

Output shape stays the same as before so the rest of the skill (db.py /
sources/wechat.py / report.py) does not need to change:

    {
        "articles":   [ {mp_id, title, url, description, content_html, publish_time}, ... ],
        "publishers": [ {mp_id, nickname, mp_cover}, ... ]
    }
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"config not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_feed_url(cfg: dict[str, Any]) -> str:
    """Return the aggregated RSS feed URL (env var > config)."""
    env = os.environ.get("WCRSS_FEED_URL")
    if env:
        return env.strip()
    url = (cfg.get("wcrss_feed_url") or "").strip()
    if not url or url.startswith("<"):
        raise RuntimeError(
            "WCRSS_FEED_URL not configured. "
            "Set env WCRSS_FEED_URL or fill 'wcrss_feed_url' in config/config.json"
        )
    return url


def _http_get(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "ignore")
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:500]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"feed HTTP {e.code}: {body}")
    except URLError as e:
        raise RuntimeError(f"feed network error: {e.reason}")


_TITLE_PREFIX = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


def _stable_mp_id(nickname: str) -> str:
    """Synthetic stable id for a publisher when feed doesn't expose one."""
    return "MP_WMTS_" + hashlib.sha1(nickname.encode("utf-8")).hexdigest()[:16].upper()


def _parse_date(text: str | None) -> int:
    if not text:
        return 0
    try:
        return int(parsedate_to_datetime(text).timestamp())
    except Exception:  # noqa: BLE001
        return 0


def _parse_feed(xml_text: str, recent_days: int, num: int) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"feed parse error: {e}")

    articles: list[dict[str, Any]] = []
    pubs_by_name: dict[str, dict[str, Any]] = {}

    cutoff = 0
    if recent_days > 0:
        import time
        cutoff = int(time.time()) - recent_days * 86400

    for item in root.iter("item"):
        title_raw = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not (title_raw and url):
            continue
        # 取出 [公众号] 前缀作为 publisher 名
        m = _TITLE_PREFIX.match(title_raw)
        if m:
            nickname = m.group(1).strip()
            title = m.group(2).strip() or title_raw
        else:
            nickname = "(未知公众号)"
            title = title_raw

        pub_ts = _parse_date(item.findtext("pubDate"))
        if cutoff and pub_ts and pub_ts < cutoff:
            continue

        mp_id = _stable_mp_id(nickname)
        pubs_by_name.setdefault(nickname, {
            "mp_id": mp_id,
            "nickname": nickname,
            "mp_cover": "",
        })

        desc_html = item.findtext("description") or ""
        # 简易截掉 HTML 取前 240 字符做 description
        desc_text = re.sub(r"<[^>]+>", " ", desc_html)
        desc_text = re.sub(r"\s+", " ", desc_text).strip()

        articles.append({
            "mp_id": mp_id,
            "title": title,
            "url": url,
            "description": desc_text[:280],
            "content_html": desc_html,
            "publish_time": pub_ts,
        })
        if len(articles) >= num:
            break

    return {
        "articles": articles,
        "publishers": list(pubs_by_name.values()),
    }


def fetch_articles(recent_days: int = 3, num: int = 50) -> dict[str, Any]:
    """Compatibility wrapper: returns dict with articles[] + publishers[]."""
    cfg = load_config()
    feed_url = _resolve_feed_url(cfg)
    xml_text = _http_get(feed_url)
    return _parse_feed(xml_text, recent_days, num)
