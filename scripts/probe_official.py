"""自动探测官网新闻列表的 BeautifulSoup selectors。

针对 config/derived/official.json 的每个 entry：
  1. 抓 HTML
  2. 暴力搜索：找含日期(YYYY-MM-DD/YYYY/MM/DD)且含 <a href> 的最近共同祖先
  3. 推荐 selectors（item / title / link / date）

结果写入 _probe_result.json，供人工核实后手工合并到 official.json。
"""
from __future__ import annotations
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources._common import http_get_text  # noqa: E402

DATE_RE = re.compile(r"(20\d{2}[-/.]\s?\d{1,2}[-/.]\s?\d{1,2}|20\d{2}年\s?\d{1,2}月\s?\d{1,2}日)")


def find_list_pattern(html: str) -> dict | None:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # 找所有含日期文本的元素
    date_nodes = []
    for el in soup.find_all(string=DATE_RE):
        parent = el.parent
        if parent and parent.name not in ("script", "style"):
            date_nodes.append(parent)
    if len(date_nodes) < 2:
        return None

    # 沿着每个日期节点向上找含 <a href> 的最近祖先（候选 item）
    candidates = []
    for dn in date_nodes:
        for anc in dn.parents:
            if anc.name in ("body", "html", "[document]"):
                break
            a = anc.find("a", href=True)
            if a and DATE_RE.search(anc.get_text(" ", strip=True)):
                candidates.append((anc.name, _signature(anc)))
                break

    # 投票出最常见的 (tag, signature) → 即列表项模式
    if not candidates:
        return None
    counter = Counter(candidates)
    (tag, sig), count = counter.most_common(1)[0]
    if count < 2:
        return None

    # 反推 selector
    item_sel = sig

    # 在 item 内找 title / link / date selector
    sample = None
    for dn in date_nodes:
        for anc in dn.parents:
            if anc.name == tag and _signature(anc) == sig:
                sample = anc
                break
        if sample:
            break
    if not sample:
        return None

    a = sample.find("a", href=True)
    title_sel = "a"
    link_sel = "a@href"
    # 找日期所在子标签
    date_sel = None
    for child in sample.find_all():
        if DATE_RE.search(child.get_text(" ", strip=True)) and not child.find(string=DATE_RE).parent.find_all():
            date_sel = _short_selector(child, sample)
            break

    return {
        "item": item_sel,
        "title": title_sel,
        "link": link_sel,
        "date": date_sel,
        "_sample_count": count,
        "_sample_title": (a.get_text(" ", strip=True) if a else "")[:60],
        "_sample_href": a["href"] if a else "",
    }


def _signature(el) -> str:
    """生成最简定位 selector：tag + class（取最区分度）。"""
    tag = el.name
    classes = el.get("class") or []
    if classes:
        # 取第一个看起来不是布局工具类的
        for c in classes:
            if not re.match(r"^(col|row|w-|m-|p-|d-|flex|grid)\d*$", c):
                return f"{tag}.{c}"
        return f"{tag}.{classes[0]}"
    if el.get("id"):
        return f"{tag}#{el['id']}"
    return tag


def _short_selector(child, ancestor) -> str:
    """child 相对 ancestor 的简短 selector。"""
    tag = child.name
    classes = child.get("class") or []
    if classes:
        return f"{tag}.{classes[0]}"
    return tag


def main():
    cfg = json.loads(Path("config/derived/official.json").read_text(encoding="utf-8"))
    out = []
    for e in cfg["entries"]:
        name = e["name"]
        url = e["url"]
        rec = {"name": name, "url": url}
        try:
            html = http_get_text(url)
        except Exception as ex:
            rec["status"] = "FETCH_FAIL"
            rec["error"] = str(ex)[:80]
            out.append(rec)
            continue
        rec["html_size"] = len(html)
        sel = find_list_pattern(html)
        if sel:
            rec["status"] = "AUTO_OK"
            rec["selectors"] = {k: v for k, v in sel.items() if not k.startswith("_")}
            rec["_probe"] = {k: v for k, v in sel.items() if k.startswith("_")}
        else:
            rec["status"] = "NO_PATTERN"
        out.append(rec)
        print(f"  {name:<14} {rec['status']:<12}  size={rec.get('html_size','-')}  "
              f"sel={rec.get('selectors')}")

    Path("data/_probe_result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nWritten data/_probe_result.json  ({len(out)} entries)")


if __name__ == "__main__":
    main()
