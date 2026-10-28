# -*- coding: utf-8 -*-
# 采集脚本。
#
# 两块：官网的推荐路线直接抓；游记走搜狗微信检索，把跳转链接还原成
# mp.weixin.qq.com 的真实地址再抓正文。
#
# 断点续采：已经采到的不重复抓，跑一半断了可以直接再跑。
# 访问限制按对方的意思处理：403/429 就停在那个主机上，不换会话、不预热首页重试。
#
#   python src/crawler.py              增量补采
#   python src/crawler.py --area JZG   只采一个景区
#   python src/crawler.py --fresh      忽略已有结果全部重采

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extractors import EXTRACTORS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_HTML_DIR = os.path.join(ROOT, "data", "raw", "source_archive", "raw_html")
RAW_DIR = os.path.join(ROOT, "data", "raw")
RAW_JSONL = os.path.join(RAW_DIR, "raw_documents.jsonl")
SOURCES = os.path.join(ROOT, "config", "sources.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

NOTES_PER_AREA = 5
CANDIDATES_PER_AREA = 14
MIN_NOTE_CHARS = 800

# 明显不是游记的标题，直接跳过，省点请求
BAD_TITLE_WORDS = [
    "售楼", "楼盘", "招聘", "加盟", "招商", "股价", "公告", "声明", "辟谣",
    "门票预订", "特价机票", "酒店预订", "作文", "试题", "课件",
    "研学", "幼儿园", "小学", "中学", "学校", "喜报", "招生", "报名",
    "通知", "课程", "开学", "家长会", "党建", "表彰", "特惠", "抢票",
    "低至", "优惠购", "领券", "开业", "闭园",
]
# 游记/攻略类标题里多少会有这些词，用来把新闻、科普排到后面
HINT_WORDS = [
    "游记", "攻略", "游玩", "自由行", "亲子", "一日", "两日", "三天", "玩转",
    "打卡", "体验", "带你", "怎么玩", "路线", "遛娃",
]


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


class Crawler:
    """抓取公开页面。

    这里是访问策略，写清楚是为了可检查：403/429 表示对方不想让我们继续访问，
    就停掉本次运行对该主机的后续请求，不靠轮换会话、预热首页或换 IP 去绕。
    只有瞬时网络错误和服务器 5xx 才退避重试，401/404/410 直接记失败。
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.blocked_hosts = set()
        self.logs = []

    def get(self, url, referer=None, retries=3, timeout=20):
        host = (urlsplit(url).hostname or "").lower()
        if host in self.blocked_hosts:
            return 0, "HOST_BLOCKED: 本次运行已停止访问该主机"
        headers = {"Referer": referer} if referer else {}
        err = ""
        for i in range(max(1, retries)):
            try:
                r = self.session.get(url, headers=headers, timeout=timeout)
                r.encoding = r.apparent_encoding or "utf-8"
                if r.status_code == 200 and len(r.text) > 300:
                    return r.status_code, r.text
                if r.status_code in (403, 429):
                    self.blocked_hosts.add(host)
                    err = "HTTP %s：停止本次运行对该主机的后续请求" % r.status_code
                    log("  %s (%s)" % (err, host))
                    return r.status_code, err
                if r.status_code in (401, 404, 410):
                    return r.status_code, "HTTP %s：不重试" % r.status_code
                err = "HTTP %s / len=%d" % (r.status_code, len(r.text))
                if r.status_code < 500:
                    return r.status_code, err
            except requests.RequestException as e:
                err = "%s: %s" % (type(e).__name__, str(e)[:120])
            if i + 1 < max(1, retries):
                time.sleep(min(2 ** (i + 1), 8))
        return 0, err or "请求失败"

    def _record(self, doc, http_code, cost, err=""):
        doc.setdefault("crawl_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.logs.append({
            "doc_id": doc.get("doc_id", ""),
            "scenic_area": doc.get("scenic_area", ""),
            "text_type": doc.get("text_type", ""),
            "source_platform": doc.get("source_platform", ""),
            "source_url": doc.get("source_url", ""),
            "http_code": http_code,
            "cost_sec": round(cost, 2),
            "status": "ok" if not err else "failed",
            "error": err,
        })
        return doc

    def _save_html(self, doc_id, html):
        path = os.path.join(RAW_HTML_DIR, doc_id + ".html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return os.path.relpath(path, ROOT).replace("\\", "/")

    def crawl_official(self, area, page, seq):
        doc_id = "%s-OFF-%02d" % (area["code"], seq)
        url = page["url"]
        log("官方路线 %s -> %s" % (doc_id, url))
        t0 = time.time()
        code, html = self.get(url)
        cost = time.time() - t0

        base = {
            "doc_id": doc_id, "scenic_code": area["code"], "scenic_area": area["name"],
            "city": area["city"], "category": area["category"],
            "text_type": "official_route", "source_platform": "景区官方网站",
            "source_site": url.split("/")[2], "source_url": url,
            "title": "", "author": "", "publish_date": "",
            "raw_text": "", "block_count": 0, "http_status": code, "raw_html_path": "",
        }
        if not html or not html.lstrip().startswith("<"):
            return self._record(base, code, cost, err=html)

        extractor = EXTRACTORS["jiuzhai" if "jiuzhai" in url else "chimelong"]
        f = extractor(html)
        doc = dict(base)
        doc.update({
            "doc_subtype": page.get("doc_type", ""),
            "title": f["title"],
            "author": f["author"],
            "publish_date": f["publish_date"],
            "raw_text": "\n".join(f["text_blocks"]),
            "block_count": len(f["text_blocks"]),
            "html_tag_count": f.get("tag_count", 0),
            "extract_rule": f["extract_rule"],
            "raw_html_path": self._save_html(doc_id, html),
            "note": page.get("note", ""),
        })
        return self._record(doc, code, cost)

    def sogou_search(self, query):
        url = "https://weixin.sogou.com/weixin?type=2&query=" + requests.utils.quote(query)
        code, html = self.get(url, referer="https://weixin.sogou.com/")
        if code != 200:
            log("  检索失败 %s (%s)" % (query, html))
            return []

        soup = BeautifulSoup(html, "lxml")
        out = []
        for li in soup.select("ul.news-list li"):
            a = li.select_one("h3 a")
            if not a or not a.get("href"):
                continue
            title = re.sub(r"\s+", " ", a.get_text(" ")).strip()
            li_text = li.get_text(" ")
            account = ""
            for sel in [".account", ".s-p a", ".s2"]:
                el = li.select_one(sel)
                if el and el.get_text(strip=True):
                    account = el.get_text(strip=True)
                    break
            m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", li_text)
            out.append({
                "title": title,
                "account": account,
                "list_date": m.group(0) if m else "",
                "sogou_link": ("https://weixin.sogou.com" + a["href"])
                if a["href"].startswith("/") else a["href"],
            })

        if not out:
            log("  检索结果为空；记下并停下这条查询，不换会话重试：%s" % query)
        return out

    def resolve(self, link):
        """搜狗跳转页里真实地址是 JS 拼的：url += 'xxx'; 一段段拼起来。"""
        code, html = self.get(link, referer="https://weixin.sogou.com/")
        if code != 200:
            return None
        if "mp.weixin.qq.com" in html and "url +=" not in html:
            m = re.search(r"https?://mp\.weixin\.qq\.com/s\?[^\"'<>\s]+", html)
            return m.group(0).replace("@", "") if m else None
        parts = re.findall(r"url \+= '([^']*)'", html)
        if not parts:
            return None
        return "".join(parts).replace("@", "")

    def crawl_note(self, area, cand, seq, attempt=1):
        doc_id = "%s-NOTE-%02d" % (area["code"], seq)
        log("游记 %s [第%d次] <- %s" % (doc_id, attempt, cand["title"][:34]))
        t0 = time.time()

        base = {
            "doc_id": doc_id, "scenic_code": area["code"], "scenic_area": area["name"],
            "city": area["city"], "category": area["category"],
            "text_type": "travel_note", "source_platform": "微信公众号",
            "source_site": "mp.weixin.qq.com", "source_url": cand["sogou_link"],
            "title": cand["title"], "author": cand["account"], "publish_date": "",
            "raw_text": "", "block_count": 0, "http_status": 0, "raw_html_path": "",
        }

        real = None
        for _ in range(2):
            real = self.resolve(cand["sogou_link"])
            if real:
                break
            time.sleep(3)
        if not real:
            return self._record(dict(base), 0, time.time() - t0, err="跳转链接解析失败")

        base["source_url"] = real
        code, html = self.get(real, referer="https://weixin.sogou.com/")
        base["http_status"] = code
        f = EXTRACTORS["weixin"](html) if code == 200 else None

        if f is None or not f["text_blocks"]:
            # 微信有时会回一个"环境异常"的空壳页，隔几秒在同一会话里再要一次
            time.sleep(random.uniform(4, 6))
            code, html = self.get(real, referer="https://weixin.sogou.com/")
            base["http_status"] = code
            f = EXTRACTORS["weixin"](html) if code == 200 else None
        if f is None or not f["text_blocks"]:
            return self._record(dict(base), code, time.time() - t0, err="正文容器为空")

        doc = dict(base)
        doc.update({
            "doc_subtype": "",
            "search_title": cand["title"],
            "title": f["title"] or cand["title"],
            "author": f["author"] or cand["account"],
            "publish_date": f["publish_date"] or cand.get("list_date", ""),
            "raw_text": "\n".join(f["text_blocks"]),
            "block_count": len(f["text_blocks"]),
            "html_tag_count": f.get("tag_count", 0),
            "extract_rule": f["extract_rule"],
            "raw_html_path": self._save_html(doc_id, html),
            "note": "",
        })
        return self._record(doc, code, time.time() - t0)


def pick(cands, seen, want):
    out = []
    for c in cands:
        t = c["title"]
        if t in seen or len(t) < 6:
            continue
        if any(w in t for w in BAD_TITLE_WORDS):
            continue
        c["hint"] = any(w in t for w in HINT_WORDS)
        seen.add(t)
        out.append(c)
    out.sort(key=lambda x: not x["hint"])   # 像游记的排前面
    return out[:want]


def load_existing():
    if not os.path.exists(RAW_JSONL):
        return []
    docs = []
    with open(RAW_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="")
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()

    os.makedirs(RAW_HTML_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    cfg = json.load(open(SOURCES, encoding="utf-8"))
    docs = [] if args.fresh else load_existing()
    done = {d["doc_id"] for d in docs if d.get("raw_text")}
    log("已有可用文档 %d 篇" % len(done))

    cr = Crawler()

    for area in cfg["scenic_areas"]:
        code = area["code"]
        if args.area and code != args.area:
            continue

        n_off = sum(1 for d in docs if d["scenic_code"] == code
                    and d["text_type"] == "official_route" and d.get("raw_text"))
        n_note = sum(1 for d in docs if d["scenic_code"] == code
                     and d["text_type"] == "travel_note" and d.get("raw_text"))
        log("========== %s(%s)：已有 官方 %d / 游记 %d ==========" % (
            area["name"], code, n_off, n_note))

        for i, page in enumerate(area.get("official_pages", []), start=1):
            doc_id = "%s-OFF-%02d" % (code, i)
            if doc_id in done:
                continue
            d = cr.crawl_official(area, page, i)
            if d and d.get("raw_text"):
                docs.append(d)
                done.add(d["doc_id"])
            time.sleep(random.uniform(1, 2))

        if n_note >= NOTES_PER_AREA:
            log("  游记够了，跳过")
            continue

        cands = []
        for q in area.get("weixin_queries", []):
            got = cr.sogou_search(q)
            log("  搜「%s」拿到 %d 条" % (q, len(got)))
            cands.extend(got)
            time.sleep(random.uniform(2, 3.5))

        picked = pick(cands, set(), CANDIDATES_PER_AREA)
        log("  去重后 %d 篇候选，开始抓正文" % len(picked))

        seq = n_note
        for c in picked:
            if seq >= NOTES_PER_AREA:
                break
            for attempt in (1, 2):
                d = cr.crawl_note(area, c, seq + 1, attempt)
                if d.get("raw_text") and len(d["raw_text"]) >= MIN_NOTE_CHARS:
                    docs.append(d)
                    done.add(d["doc_id"])
                    seq += 1
                    break
                if attempt == 1:
                    time.sleep(random.uniform(3, 5))
                else:
                    log("  放弃 %s" % d["doc_id"])
            time.sleep(random.uniform(2.5, 4))

        # 搜狗被彻底限流时候用的兜底：正文已经留到本地了，从这里补进去。
        for m in area.get("manual_notes", []):
            if m["doc_id"] in done:
                continue
            path = os.path.join(ROOT, m["file"])
            if not os.path.exists(path):
                log("  缺文件：%s" % m["file"])
                continue
            text = open(path, encoding="utf-8").read().strip()
            if len(text) < MIN_NOTE_CHARS:
                log("  太短，跳过：%s" % m["doc_id"])
                continue
            doc = {
                "doc_id": m["doc_id"], "scenic_code": code, "scenic_area": area["name"],
                "city": area["city"], "category": area["category"],
                "text_type": "travel_note", "doc_subtype": "",
                "source_platform": "微信公众号（正文快照）",
                "source_site": "mp.weixin.qq.com", "source_url": m.get("url", ""),
                "title": m["title"], "author": m.get("author", ""),
                "publish_date": m.get("publish_date", ""),
                "raw_text": text, "block_count": len(text.split("\n")),
                "html_tag_count": 0,
                "extract_rule": "manual:已留存正文快照（data/raw/manual/）",
                "http_status": 200, "raw_html_path": m["file"],
                "note": m.get("note", ""),
            }
            cr._record(doc, 200, 0.0)
            docs.append(doc)
            done.add(doc["doc_id"])
            log("  补采 %s <- %s" % (doc["doc_id"], m["title"][:30]))

        log("  %s 完成：官方 %d / 游记 %d" % (
            code,
            sum(1 for d in docs if d["scenic_code"] == code and d["text_type"] == "official_route"),
            sum(1 for d in docs if d["scenic_code"] == code and d["text_type"] == "travel_note")))

    docs.sort(key=lambda d: d["doc_id"])
    with open(RAW_JSONL, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    with open(os.path.join(RAW_DIR, "crawl_log.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cr.logs[0].keys()) if cr.logs else ["doc_id"])
        w.writeheader()
        w.writerows(cr.logs)

    log("结束：共 %d 篇" % len(docs))


if __name__ == "__main__":
    main()
