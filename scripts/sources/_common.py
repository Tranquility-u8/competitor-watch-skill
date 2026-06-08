"""Shared helpers for source plugins."""
from __future__ import annotations

import gzip
import io
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "config.json"
GAMES_PATH = ROOT / "config" / "games.json"
DERIVED_DIR = ROOT / "config" / "derived"
DB_PATH = ROOT / "data" / "articles.db"
RAW_DIR = ROOT / "data" / "raw"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict[str, Any]:
    return load_json(CONFIG_PATH)


def load_games() -> list[dict]:
    """Top-level user-maintained list."""
    doc = load_json(GAMES_PATH)
    return doc.get("games", []) or []


def game_priority_order(games: list[dict] | None = None) -> list[str]:
    """Return game names sorted by priority (high → normal → low) for filter/sort."""
    games = games if games is not None else load_games()
    rank = {"high": 0, "normal": 1, "low": 2}
    return [g["name"] for g in sorted(games, key=lambda g: rank.get(g.get("priority", "normal"), 1))]


def load_derived(name: str) -> dict[str, Any]:
    """Load config/derived/<name>.json (auto-maintained by resolve.py)."""
    return load_json(DERIVED_DIR / f"{name}.json")


def http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> bytes:
    # 不申请 gzip/br，避免某些站点错误响应也压缩导致解码乱码
    h = {"User-Agent": DEFAULT_UA, "Accept-Encoding": "identity"}
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            enc = resp.headers.get("Content-Encoding", "")
            if enc == "gzip":
                data = gzip.decompress(data)
            return data
    except HTTPError as e:
        body = ""
        try:
            raw = e.read()
            body = raw.decode("utf-8", "ignore")[:500]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"HTTP {e.code} on {url}: {body}")
    except URLError as e:
        raise RuntimeError(f"network error on {url}: {e.reason}")


def http_get_json(url: str, **kwargs: Any) -> Any:
    raw = http_get(url, **kwargs)
    return json.loads(raw.decode("utf-8"))


def http_get_text(url: str, **kwargs: Any) -> str:
    """智能解码：从 HTML <meta charset> / Content-Type 推断真实编码。

    腾讯（gicp）/网易（163）一些老站使用 GBK，强制 utf-8 会导致中文乱码。
    """
    raw = http_get(url, **kwargs)
    # 先用 latin-1 quick-peek 找 meta charset
    head = raw[:4096].decode("latin-1", "ignore").lower()
    enc = None
    import re as _re
    m = _re.search(r'<meta[^>]+charset=["\']?([\w\-]+)', head)
    if m:
        enc = m.group(1)
    if not enc:
        m = _re.search(r'<meta[^>]+content=["\'][^"\']*charset=([\w\-]+)', head)
        if m:
            enc = m.group(1)
    if not enc:
        enc = "utf-8"
    enc = enc.lower().replace("gb2312", "gbk")  # gb2312 实际多含 GBK 字符
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def now_ts() -> int:
    return int(time.time())
