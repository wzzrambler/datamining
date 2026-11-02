# -*- coding: utf-8 -*-
# 从页面 HTML 里把正文取出来。三种页面模板：长隆官网、九寨沟官网、微信公众号。
#
# 这里只负责"定位容器 + 取文本块"，不做清洗。取出来的东西是脏的（带引导语、图注、
# emoji、空行），故意的 —— 后面 clean.py 才有东西可洗，报告里的清洗前后对比也是拿它比的。

import re
from bs4 import BeautifulSoup

DROP = ["script", "style", "noscript", "iframe", "svg", "form", "button"]


def _flat(s):
    return re.sub(r"[\s\u3000]+", " ", s).strip()


def to_blocks(node):
    """把容器切成一行一段。

    原来直接 get_text() 拿一坨，段落全糊在一起，后面按行过滤噪声就没法做了。
    改成先把 <br> 换成换行，再按换行切。
    """
    blocks = []
    if node is None:
        return blocks

    for t in node.find_all(DROP):
        t.decompose()
    for br in node.find_all("br"):
        br.replace_with("\n")

    for line in node.get_text("\n").split("\n"):
        line = _flat(line)
        if line:
            blocks.append(line)
    return blocks


def _title(soup):
    for sel in ["h1", ".article-title", ".title", "title"]:
        el = soup.select_one(sel)
        if el:
            t = _flat(el.get_text(" "))
            if t:
                return t
    return ""


def extract_chimelong(html):
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one(".c--static-flow") or soup.select_one("[class*='static-flow']")
    blocks = to_blocks(node)

    # 页面第一个 h1 是导航里的"主题乐园"，不是标题。正文第一行才是，用它。
    title = ""
    if blocks and len(blocks[0]) <= 40 and not re.search(r"[。！？；]", blocks[0]):
        title = blocks[0]
    if not title:
        title = _title(soup)

    date = ""
    for b in blocks[:4]:
        m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", b)
        if m:
            date = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
            break

    blocks = [b for b in blocks if b != title]

    return {
        "title": title,
        "author": "长隆旅游度假区官网",
        "publish_date": date,
        "text_blocks": blocks,
        "tag_count": len(soup.find_all()),
        "extract_rule": "css:.c--static-flow + 按行分块 + 标题/日期定位",
    }


def extract_jiuzhai(html):
    soup = BeautifulSoup(html, "lxml")

    # 这个站页面里有一堆 div.g-grid，导航、页脚都是，正文是文字最多的那个
    best, best_len = None, -1
    for g in soup.select("div.g-grid"):
        n = len(_flat(g.get_text(" ")))
        if n > best_len:
            best, best_len = g, n

    blocks = to_blocks(best)

    return {
        "title": _title(soup),
        "author": "九寨沟风景名胜区官方网站",
        "publish_date": "",
        "text_blocks": blocks,
        "tag_count": len(soup.find_all()),
        "extract_rule": "css:div.g-grid[max-text] + 按行分块",
    }


def extract_weixin(html):
    soup = BeautifulSoup(html, "lxml")
    blocks = to_blocks(soup.select_one("#js_content"))

    el = soup.select_one("#activity-name")
    title = _flat(el.get_text(" ")) if el else ""
    if not title:
        title = _title(soup)

    author = ""
    for sel in ["#js_name", ".rich_media_meta_nickname", ".profile_nickname"]:
        el = soup.select_one(sel)
        if el and _flat(el.get_text(" ")):
            author = _flat(el.get_text(" "))
            break

    date = ""
    for sel in ["#publish_time", "em#publish_time", ".rich_media_meta_text"]:
        el = soup.select_one(sel)
        if el:
            m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", el.get_text(" "))
            if m:
                date = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
                break
    if not date:
        # 新版模板里日期不在 HTML 里，藏在脚本变量 var ct = "1690..."（秒级时间戳）
        m = re.search(r'var\s+ct\s*=\s*"(\d{10})"', html)
        if m:
            import datetime
            date = datetime.datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d")

    return {
        "title": title,
        "author": author,
        "publish_date": date,
        "text_blocks": blocks,
        "tag_count": len(soup.find_all()),
        "extract_rule": "css:#js_content + 按行分块；元数据取 #activity-name/#js_name/#publish_time",
    }


EXTRACTORS = {
    "chimelong": extract_chimelong,
    "jiuzhai": extract_jiuzhai,
    "weixin": extract_weixin,
}
