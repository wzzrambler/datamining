# -*- coding: utf-8 -*-
# 不联网，从随工程提交的原始网页存档里重建项目1的输入数据。
#
# 读 data/raw/source_archive/ 下的 HTML 和索引，加上 config/sources.json 里的景区元数据，
# 再补上 data/raw/manual/ 里两份正文快照，拼出 data/raw/raw_documents.jsonl。
#
# 顺带产出两张盘点表：
#   archive_inventory.csv  19 个页面逐条：解析标题、正文字数、纳入还是排除、理由
#   crawl_log.csv          17 份数据的状态。这是归档盘点用的，不是当时的 HTTP 请求日志
#
#   python src/rebuild_from_archive.py

import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extractors import EXTRACTORS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
ARCHIVE = os.path.join(RAW, "source_archive")
HTML_DIR = os.path.join(ARCHIVE, "raw_html")
INDEX = os.path.join(ARCHIVE, "原始网页索引.csv")
CONFIG = os.path.join(ROOT, "config", "sources.json")
JSONL = os.path.join(RAW, "raw_documents.jsonl")

MIN_CHARS = 100          # 低于这个字数说明这个页面没有正文，不是攻略


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pick_extractor(fn):
    if fn.startswith("JZG-") and "-OFF-" in fn:
        return EXTRACTORS["jiuzhai"]
    if "-OFF-" in fn:
        return EXTRACTORS["chimelong"]
    return EXTRACTORS["weixin"]


def main():
    cfg = read_json(CONFIG)
    areas = {a["code"]: a for a in cfg["scenic_areas"]}
    official_meta = {}
    for a in cfg["scenic_areas"]:
        for p in a.get("official_pages", []):
            official_meta[p["url"]] = p

    with open(INDEX, encoding="utf-8-sig", newline="") as f:
        index = list(csv.DictReader(f))

    seen = {}          # 正文指纹 -> 第一个出现的文件名，用来找重复页
    docs, inventory = [], []
    for row in index:
        fn = row["文件名"]
        path = os.path.join(HTML_DIR, fn)
        html = open(path, encoding="utf-8", errors="replace").read()
        feat = pick_extractor(fn)(html)
        text = "\n".join(feat["text_blocks"])
        fp = hashlib.md5(text.encode("utf-8")).hexdigest()[:12] if text else ""

        did = row["对应doc_id"]
        if did == "（未使用）" or not did:
            reason = "存档里已有同一篇正文的页面，未重复纳入" if (fp and fp in seen) else "未对应数据集文档"
            if len(text) < MIN_CHARS:
                reason = "只提取到 %d 个字符，页面没有正文" % len(text)
            inventory.append({"文件": fn, "解析标题": feat["title"][:50], "正文字数": len(text),
                              "决定": "排除", "理由": reason})
            continue
        if fp and fp in seen:
            inventory.append({"文件": fn, "解析标题": feat["title"][:50], "正文字数": len(text),
                              "决定": "排除", "理由": "正文与 %s 完全重复" % seen[fp]})
            continue

        seen[fp] = fn
        code = did.split("-")[0]
        area = areas[code]
        is_official = did.endswith(tuple("-OFF-%02d" % i for i in range(1, 100)))
        url = row["原始链接"]
        docs.append({
            "doc_id": did, "scenic_code": code, "scenic_area": area["name"],
            "city": area.get("city", ""), "category": area.get("category", ""),
            "text_type": "official_route" if is_official else "travel_note",
            "doc_subtype": "", "source_platform": "景区官方网站" if is_official else "微信公众号",
            "source_site": (url.split("/")[2] if url else "mp.weixin.qq.com"),
            "source_url": url,
            "search_title": feat["title"], "title": feat["title"],
            "author": feat.get("author", ""), "publish_date": feat.get("publish_date", ""),
            "raw_text": text, "block_count": len(feat["text_blocks"]),
            "html_tag_count": feat.get("tag_count", 0),
            "extract_rule": feat.get("extract_rule", ""),
            "http_status": 200, "raw_html_path": os.path.relpath(path, ROOT).replace("\\", "/"),
            "note": (official_meta.get(url, {}) or {}).get("note", ""),
            "crawl_time": "", "source_origin": "原始网页存档",
        })
        inventory.append({"文件": fn, "解析标题": feat["title"][:50], "正文字数": len(text),
                          "决定": "纳入", "理由": "对应 %s" % did})

    # 两份正文快照：原始 TXT 不在网页存档里，单独从 data/raw/manual 读
    for a in cfg["scenic_areas"]:
        for m in a.get("manual_notes", []):
            txt = os.path.join(RAW, "manual", m["doc_id"] + ".txt")
            text = open(txt, encoding="utf-8").read().strip()
            docs.append({
                "doc_id": m["doc_id"], "scenic_code": a["code"], "scenic_area": a["name"],
                "city": a.get("city", ""), "category": a.get("category", ""),
                "text_type": "travel_note", "doc_subtype": "",
                "source_platform": "微信公众号（正文快照）", "source_site": "mp.weixin.qq.com",
                "source_url": m["url"], "search_title": m["title"], "title": m["title"],
                "author": m.get("author", ""), "publish_date": m.get("publish_date", ""),
                "raw_text": text, "block_count": len([x for x in text.split("\n") if x.strip()]),
                "html_tag_count": 0, "extract_rule": "manual:已留存正文快照（data/raw/manual/）",
                "http_status": 0, "raw_html_path": os.path.relpath(txt, ROOT).replace("\\", "/"),
                "note": "搜狗微信检索限流后改用检索服务返回的正文快照，原始 TXT 不在网页存档中",
                "crawl_time": "", "source_origin": "正文快照",
            })
            inventory.append({"文件": os.path.basename(txt), "解析标题": m["title"][:50],
                              "正文字数": len(text), "决定": "纳入",
                              "理由": "对应 %s（正文快照，非网页存档）" % m["doc_id"]})

    docs.sort(key=lambda d: d["doc_id"])
    with open(JSONL, "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    with open(os.path.join(RAW, "archive_inventory.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["文件", "解析标题", "正文字数", "决定", "理由"])
        w.writeheader()
        w.writerows(inventory)

    with open(os.path.join(RAW, "crawl_log.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "scenic_area", "text_type", "source_platform",
                    "source_url", "http_code", "status", "error"])
        for d in docs:
            w.writerow([d["doc_id"], d["scenic_area"], d["text_type"], d["source_platform"],
                        d["source_url"], d["http_status"],
                        "ok" if d["raw_text"] else "failed", d.get("note", "")])

    ok = sum(1 for x in inventory if x["决定"] == "纳入")
    print("重建完成：%d 份文档写入 %s" % (len(docs), os.path.basename(JSONL)))
    print("存档盘点：%d 个页面，纳入 %d，排除 %d" % (len(inventory), ok, len(inventory) - ok))
    return docs


if __name__ == "__main__":
    main()
