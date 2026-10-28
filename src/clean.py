# -*- coding: utf-8 -*-
# 清洗与结构化。输入 raw_documents.jsonl，输出 csv/json/xlsx 三份 + 清洗样例。
#
# 规则编号 R1~R9 是写报告时定的，代码里按这个顺序走。核心原则只有一条：
# 不删掉承载"顺序"和"时间"的东西。第一站（10:00）、【上午】、A→B 这些一律保留，
# 只清模板文字和空白。

import json
import os
import re
import csv
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw", "raw_documents.jsonl")
OUT_DIR = os.path.join(ROOT, "data", "processed")

TYPE_CN = {"official_route": "官方推荐路线", "travel_note": "游客游记"}

# R1 零宽字符、软连字符、控制字符（从公众号页面复制下来常带这些）
INVISIBLE_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060\ufeff\u00ad\x00-\x08\x0b\x0c\x0e-\x1f]"
)

# R4 的规则库。这些行整行删掉，不能按词删 —— 正文里"关注""建议"都是正常词，
# 按词删会把项目5要用的条件句一起删了。
NOISE_LINE_PATTERNS = [
    # 关注/引流
    r"长按(识别)?(下方)?二维码",
    r"(扫码|扫描)(关注|二维码)",
    r"点击(上方)?蓝字.{0,10}关注",
    r"关注(我们|公众号|「|“|【)",
    r"设为星标",
    r"分享到(朋友圈|微博)",
    r"点(击)?(个)?(赞|在看)",
    r"阅读原文",
    r"微信号\s*[:：]",
    r"添加(客服|微信)",
    r"投稿(邮箱|请)",
    r"转载请",
    # 版权/编辑
    r"版权(归|所有)",
    r"(如|若)有侵权",
    r"来源\s*[:：]",
    r"^(编辑|责编|审核|校对|排版|撰文|摄影|图片|供图|文字|记者)\s*[:：]",
    r"本文(由|系).{0,20}(原创|编辑|整理)",
    # 图注。这条原来是 [:：]，后来发现"图源丨图虫网-xxx"这种用竖线分隔的漏了，补上 丨|·
    r"^(图|图片来源|图源|资料图|配图|摄影|供图)\s*[:：丨|·]",
    r"^[（(]?(图片|图)[^）)]{0,12}(来自|源于|摄)[^）)]{0,12}[）)]?$",
    # 购票/预订入口
    r"^(立即|马上)(购票|预订|抢购)",
    r"^客服(热线|电话)\s*[:：]?\s*400",
    r"^[>＞]{1,2}\s*$",
]

PURE_SYMBOL_RE = re.compile(r"^[\W_]+$")

# R5 链接和联系方式
URL_RE = re.compile(r"(https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
TEL_RE = re.compile(r"(?<!\d)(400[-\s]?\d{3}[-\s]?\d{4}|0\d{2,3}[-\s]?\d{7,8}|1[3-9]\d{9})(?!\d)")
WXID_RE = re.compile(r"(?i)(微信号|wechat|weixin)\s*[:：]?\s*[A-Za-z][-_A-Za-z0-9]{5,19}")

# R6
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F000-\U0001F2FF"
    "\U00002600-\U000026FF\U0000FE0F\U0001F900-\U0001F9FF]+"
)
# 列表符号统一成 "- "，文字不动。
# 这里最早把 —、-、→ 也算成列表符号，结果"——长隆野生动物世界情侣游攻略"被改成"- 长隆…"，
# 破折号和路线箭头都坏了，后来把这三个从字符类里去掉。
BULLET_RE = re.compile(r"^[√✓✔●○◆◇■□▪▫★☆※·•]{1,3}\s*")
REPEAT_PUNCT_RE = re.compile(r"([！？。，、；：,.!?;:])\1{2,}")

# R7 全角数字字母转半角，时间冒号统一并补零
FULLWIDTH_NUM_ALPHA = {chr(0xFF10 + i): str(i) for i in range(10)}
FULLWIDTH_NUM_ALPHA.update({chr(0xFF21 + i): chr(0x41 + i) for i in range(26)})
FULLWIDTH_NUM_ALPHA.update({chr(0xFF41 + i): chr(0x61 + i) for i in range(26)})
TIME_RE = re.compile(r"(\d{1,2})\s*[：:]\s*(\d{2})")

RULES = {
    "R1": "清除零宽字符、软连字符、控制字符，统一不间断空格/全角空格",
    "R2": "去掉残留 HTML 标签与 HTML 实体（&nbsp; &amp; 等）",
    "R3": "行内多空格压成单空格、去行首尾空白、合并连续空行",
    "R4": "整行删除模板噪声（关注引导、版权声明、图片说明、购票入口）",
    "R5": "删除正文中的网址、邮箱、电话、微信号",
    "R6": "删除 emoji，列表符号统一为 '-'，压缩重复标点",
    "R7": "全角数字/字母转半角，'10：00' 统一为 '10:00'",
    "R8": "文档内完全重复行/相邻重复段落去重，重建段落",
    "R9": "空值、过短、重复文档、异常字段校验（只标记不删除，保留可追溯性）",
}


def clean_text(text):
    """按 R1~R8 洗一段文本。返回 (干净文本, 统计字典)。

    统计里每个规则单独计数，报告里那些"删除多少行/多少条"就是从这里来的。
    """
    st = {"raw_chars": len(text)}

    n = len(INVISIBLE_RE.findall(text))
    text = INVISIBLE_RE.sub("", text).replace("\u00a0", " ").replace("\u3000", " ")
    st["invisible_removed"] = n
    st["after_R1"] = len(text)

    n = len(re.findall(r"<[^>]{1,200}>", text))
    text = re.sub(r"<[^>]{1,200}>", "", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    st["html_tag_removed"] = n
    st["after_R2"] = len(text)

    kept = []
    noise_lines = short_lines = 0
    n_url = n_mail = n_tel = n_wx = n_emoji = 0

    for line in text.split("\n"):
        line = re.sub(r"[ \t\u3000]{2,}", " ", line).strip()
        if not line:
            continue

        # 整行命中模板就丢
        hit = False
        for pat in NOISE_LINE_PATTERNS:
            if re.search(pat, line):
                hit = True
                break
        if hit:
            noise_lines += 1
            continue

        # 纯符号行、一两个字那种碎行（"0" "1" "（"），公众号排版切出来的
        if PURE_SYMBOL_RE.match(line) and len(line) <= 3:
            short_lines += 1
            continue

        n_url += len(URL_RE.findall(line))
        n_mail += len(EMAIL_RE.findall(line))
        n_wx += len(WXID_RE.findall(line))
        n_tel += len(TEL_RE.findall(line))
        line = URL_RE.sub("", line)
        line = EMAIL_RE.sub("", line)
        line = WXID_RE.sub("", line)
        line = TEL_RE.sub("", line)

        n_emoji += len(EMOJI_RE.findall(line))
        line = EMOJI_RE.sub("", line)

        mark = ""
        if BULLET_RE.match(line) and len(line) > 4:
            mark = "- "
            line = BULLET_RE.sub("", line, count=1)
        line = REPEAT_PUNCT_RE.sub(r"\1", line)

        line = "".join(FULLWIDTH_NUM_ALPHA.get(ch, ch) for ch in line)
        line = TIME_RE.sub(lambda m: "%s:%s" % (m.group(1).zfill(2), m.group(2)), line)
        line = re.sub(r"\s{2,}", " ", line).strip(" \t")

        if len(line) < 2:
            short_lines += 1
            continue
        kept.append(mark + line)

    st["dropped_noise_lines"] = noise_lines
    st["dropped_short_lines"] = short_lines
    st["url_removed"] = n_url
    st["email_removed"] = n_mail
    st["tel_removed"] = n_tel
    st["wxid_removed"] = n_wx
    st["emoji_removed"] = n_emoji

    # R8 同一篇里重复的行去掉。公众号正文经常整段重复两遍，不去掉会影响后面词频。
    out, seen = [], set()
    for line in kept:
        key = line.strip("- ").strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    st["dup_lines_removed"] = len(kept) - len(out)

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    st["clean_chars"] = len(cleaned)
    st["noise_chars_removed"] = len(text) - len(cleaned)
    return cleaned, st


def shingles(s, k=3):
    s = re.sub(r"\s+", "", s)
    return {s[i:i + k] for i in range(0, max(0, len(s) - k + 1))}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def count_sentences(text):
    return len([s for s in re.split(r"[。！？；\n]", text) if len(s.strip()) >= 5])


def has_time(text):
    # 项目2/3 要用，先在这里标出来省得后面反复判断
    return bool(re.search(r"\d{1,2}[:：]\d{2}|上午|中午|下午|傍晚|晚上|早上|清晨|点\d*分", text))


def split_paras(text):
    """把正文切成段落，同时记下每段在全文里的字符区间。

    后面项目2要标实体的出现位置，如果只有全文，就得每篇自己数一遍偏移，
    所以这里直接算好：start 从 0 计，end 不含（左闭右开），text[start:end] 就是原段。
    段落之间用 \\n 连接，所以每段之后 pos 要 +1 跳过这个换行符。
    """
    out = []
    pos = 0
    idx = 0
    for line in text.split("\n"):
        s, e = pos, pos + len(line)
        if line.strip():
            idx += 1
            out.append({"i": idx, "start": s, "end": e, "text": line})
        pos = e + 1
    return out


def load_raw():
    docs = []
    with open(RAW, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    docs = load_raw()

    rows = []
    for d in docs:
        cleaned, st = clean_text(d.get("raw_text", ""))
        rows.append({
            "doc_id": d["doc_id"],
            "scenic_code": d["scenic_code"],
            "scenic_area": d["scenic_area"],
            "city": d.get("city", ""),
            "category": d.get("category", ""),
            "text_type": d["text_type"],
            "text_type_cn": TYPE_CN.get(d["text_type"], d["text_type"]),
            "doc_subtype": d.get("doc_subtype", ""),
            "source_platform": d["source_platform"],
            "source_site": d.get("source_site", ""),
            "source_url": d.get("source_url", ""),
            "title": d.get("title", ""),
            "author": d.get("author", ""),
            "publish_date": d.get("publish_date", ""),
            "crawl_time": d.get("crawl_time", ""),
            "extract_rule": d.get("extract_rule", ""),
            "raw_html_path": d.get("raw_html_path", ""),
            "raw_char_len": st["raw_chars"],
            "clean_char_len": st["clean_chars"],
            "html_tag_count": d.get("html_tag_count", 0),
            "noise_char_removed": st["noise_chars_removed"],
            "noise_line_removed": st["dropped_noise_lines"],
            "dup_line_removed": st["dup_lines_removed"],
            "invisible_removed": st["invisible_removed"],
            "html_tag_removed": st["html_tag_removed"],
            "url_removed": st["url_removed"],
            "email_removed": st["email_removed"],
            "tel_removed": st["tel_removed"],
            "wxid_removed": st["wxid_removed"],
            "emoji_removed": st["emoji_removed"],
            "paragraph_count": cleaned.count("\n") + 1 if cleaned else 0,
            "sentence_count": count_sentences(cleaned),
            "has_time_expr": int(has_time(cleaned)),
            "content": cleaned,
            "paragraphs": split_paras(cleaned),
            "raw_content": d.get("raw_text", ""),
        })

    # R9 文档级检查：只打标记，不删。删了就看不出数据本身的毛病了。
    sh = {r["doc_id"]: shingles(r["content"]) for r in rows}
    for r in rows:
        flags = []
        if not r["content"].strip():
            flags.append("空文本")
        elif r["clean_char_len"] < 200:
            flags.append("正文过短")
        if not r["title"].strip():
            flags.append("标题缺失")
        if not r["publish_date"]:
            flags.append("发布日期缺失")
        if r["has_time_expr"] == 0:
            flags.append("无时间表达")
        r["quality_flags"] = "；".join(flags)
        r["is_valid"] = int(bool(r["content"].strip()) and r["clean_char_len"] >= 200)

    ids = [r["doc_id"] for r in rows]
    dup_pairs = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            s = jaccard(sh[ids[i]], sh[ids[j]])
            if s >= 0.8:
                dup_pairs.append((ids[i], ids[j], round(s, 3)))
    dup_ids = {b for _, b, _ in dup_pairs}
    for r in rows:
        r["is_near_duplicate"] = int(r["doc_id"] in dup_ids)

    # csv 只放元数据和统计字段，不放正文。两千多字的正文塞进单元格，Excel 打开全是折行，
    # 翻都翻不动；要看内容去看 xlsx 的「正文」表或 paragraphs.csv。
    # paragraphs 是列表，也不适合塞单格，单独出 paragraphs.csv。
    fields = [k for k in rows[0] if k not in ("content", "raw_content", "paragraphs")]
    def write_csv(rows):
        path = os.path.join(OUT_DIR, "documents.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    def write_json(rows):
        # json 带正文和段落，供后续项目直接读；raw_content（清洗前）在 data/raw 里有原件，
        # 这里不再重复存一份，省一半体积。
        path = os.path.join(OUT_DIR, "documents.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{k: v for k, v in r.items() if k != "raw_content"} for r in rows],
                      f, ensure_ascii=False, indent=2)

    # 这两个文件最常被 Excel/预览打开，写不进去时重试几次，还不行就跳过，别中断整条流程
    keep(write_csv, rows)
    keep(write_json, rows)

    # 产物经常正被 Excel/预览打开，写不进去别让整个流程崩掉，重试几次再放过
    keep(write_paras, rows)
    keep(write_examples, rows)
    keep(write_excel, rows)

    ok = sum(r["is_valid"] for r in rows)
    print("清洗完成：%d 篇，有效 %d 篇，近重复 %d 对" % (len(rows), ok, len(dup_pairs)))
    print("输出： documents.csv | documents.json | dataset.xlsx")


def keep(fn, *args):
    for _ in range(4):
        try:
            return fn(*args)
        except PermissionError:
            time.sleep(1.5)
    print("  跳过 %s：文件正被打开" % fn.__name__)
    return None


def write_paras(rows):
    """段落扁平表：一行一段，带全文偏移。项目2标实体、项目3拆步骤都从这张表起。"""
    p = os.path.join(OUT_DIR, "paragraphs.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "para_id", "char_start", "char_end", "char_count",
                    "scenic_area", "text_type", "text"])
        for r in rows:
            for pa in r["paragraphs"]:
                w.writerow([r["doc_id"], pa["i"], pa["start"], pa["end"],
                            pa["end"] - pa["start"], r["scenic_area"],
                            r["text_type"], pa["text"]])
    return p


def write_examples(rows):
    # 报告里的"清洗前后对比"就是挑这里的内容改的，所以整篇都留着，方便挑。
    p = os.path.join(OUT_DIR, "cleaning_examples.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# 清洗前后对比样例\n\n")
        f.write("> 由 src/clean.py 自动生成。原文片段保留了页面噪声，右边是清洗后的样子。\n\n")
        for r in rows:
            f.write("## %s  %s（%s｜%s）\n\n" % (
                r["doc_id"], r["title"][:40], r["scenic_area"], r["text_type_cn"]))
            f.write("字符数 %d → %d，删噪声行 %d，删重复行 %d\n\n" % (
                r["raw_char_len"], r["clean_char_len"],
                r["noise_line_removed"], r["dup_line_removed"]))
            f.write("**清洗前（前 500 字）**\n\n```text\n%s\n```\n\n" % r["raw_content"][:500])
            f.write("**清洗后（前 500 字）**\n\n```text\n%s\n```\n\n" % r["content"][:500])
            f.write("---\n\n")


def _head(ws, ncol):
    from openpyxl.styles import Alignment, Font, PatternFill
    fill = PatternFill("solid", fgColor="DDEBF7")
    for c in range(1, ncol + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"


def write_excel(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    ws = wb.active
    ws.title = "文档清单"
    cols = ["doc_id", "scenic_area", "scenic_code", "category", "text_type_cn",
            "source_platform", "source_site", "title", "author", "publish_date",
            "raw_char_len", "clean_char_len", "paragraph_count", "sentence_count",
            "has_time_expr", "is_valid", "quality_flags", "source_url"]
    ws.append(cols)
    for r in rows:
        ws.append([r.get(c, "") for c in cols])
    _head(ws, len(cols))
    for i, w in enumerate([14, 22, 10, 10, 12, 14, 18, 40, 22, 12, 10, 10, 10, 10, 10, 8, 20, 46], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # 正文表：光有清单看不到内容，翻起来还得回头开 csv，索性把正文放进来一页看全
    wsA = wb.create_sheet("正文")
    wsA.append(["doc_id", "景区", "文本类型", "标题", "字数", "正文（清洗后）"])
    for r in rows:
        wsA.append([r["doc_id"], r["scenic_area"], r["text_type_cn"],
                    r["title"], r["clean_char_len"], r["content"]])
    _head(wsA, 6)
    for i, w in enumerate([14, 20, 12, 34, 8, 96], 1):
        wsA.column_dimensions[get_column_letter(i)].width = w
    for row in wsA.iter_rows(min_row=2, max_col=6):
        row[5].alignment = Alignment(wrap_text=True, vertical="top")
        row[3].alignment = Alignment(wrap_text=True, vertical="top")

    # 段落表：一行一段，带全文偏移，项目2标实体位置、项目3拆步骤直接读这张
    wsB = wb.create_sheet("段落")
    wsB.append(["doc_id", "段号", "起始", "结束", "字数", "段落文本"])
    for r in rows:
        for pa in r["paragraphs"]:
            wsB.append([r["doc_id"], pa["i"], pa["start"], pa["end"],
                        pa["end"] - pa["start"], pa["text"]])
    _head(wsB, 6)
    for i, w in enumerate([14, 6, 8, 8, 8, 100], 1):
        wsB.column_dimensions[get_column_letter(i)].width = w

    ws2 = wb.create_sheet("字段说明")
    ws2.append(["字段", "类型", "含义 / 取值"])
    desc = [
        ("doc_id", "string", "文档唯一编号：景区码-类型码-序号，如 GZCL-OFF-01"),
        ("scenic_code", "string", "景区代码：GZCL 广州长隆 / ZHCL 珠海长隆 / JZG 九寨沟"),
        ("scenic_area", "string", "景区全称"),
        ("category", "string", "景区类型：主题乐园 / 自然遗产"),
        ("text_type", "string", "official_route=官方推荐路线；travel_note=游客游记"),
        ("text_type_cn", "string", "text_type 的中文名称"),
        ("source_platform", "string", "来源平台：景区官方网站 / 微信公众号"),
        ("source_site", "string", "来源站点域名"),
        ("source_url", "string", "原始 URL（可复核）"),
        ("title", "string", "文章标题"),
        ("author", "string", "发布主体（官网栏目名或公众号名）"),
        ("publish_date", "string", "发布日期 YYYY-MM-DD，缺失留空"),
        ("crawl_time", "string", "采集时间"),
        ("raw_html_path", "string", "原始 HTML 缓存路径（本地证据）"),
        ("raw_char_len", "int", "清洗前字符数"),
        ("clean_char_len", "int", "清洗后字符数"),
        ("html_tag_count", "int", "抽取阶段从原始 HTML 中剥离的标签数"),
        ("noise_char_removed", "int", "删除的字符总数"),
        ("noise_line_removed", "int", "按 R4 删除的模板噪声行数"),
        ("dup_line_removed", "int", "按 R8 删除的文档内重复行数"),
        ("invisible_removed", "int", "按 R1 删除的零宽/控制字符数"),
        ("html_tag_removed", "int", "按 R2 删除的残留 HTML 标签数"),
        ("url_removed", "int", "按 R5 删除的网址数"),
        ("email_removed", "int", "按 R5 删除的邮箱数"),
        ("tel_removed", "int", "按 R5 删除的电话号码数"),
        ("wxid_removed", "int", "按 R5 删除的微信号数"),
        ("emoji_removed", "int", "按 R6 删除的 emoji 数"),
        ("paragraph_count", "int", "清洗后段落数"),
        ("sentence_count", "int", "清洗后句子数（按。！？；切分，长度≥5）"),
        ("has_time_expr", "int", "1=含时间表达（HH:MM 或 上午/中午/下午等），便于项目3使用"),
        ("quality_flags", "string", "R9 校验标记，如“无时间表达”“发布日期缺失”"),
        ("is_valid", "int", "1=可用于后续实验；0=空或过短"),
        ("content", "text", "清洗后正文（documents.csv/json 中提供）"),
        ("paragraphs", "list", "段落列表，每段记 i/start/end；不写 csv，见 paragraphs.csv 与「段落」表"),
    ]
    for row in desc:
        ws2.append(list(row))
    _head(ws2, 3)
    ws2.column_dimensions["A"].width = 20
    ws2.column_dimensions["B"].width = 10
    ws2.column_dimensions["C"].width = 90
    for row in ws2.iter_rows(min_row=2):
        row[2].alignment = Alignment(wrap_text=True, vertical="top")

    ws3 = wb.create_sheet("清洗统计")
    ws3.append(["doc_id", "raw_char_len", "clean_char_len", "删除率",
                "模板噪声行", "重复行", "段落数", "句子数"])
    for r in rows:
        rate = 0 if not r["raw_char_len"] else 1 - r["clean_char_len"] / r["raw_char_len"]
        ws3.append([r["doc_id"], r["raw_char_len"], r["clean_char_len"], round(rate, 3),
                    r["noise_line_removed"], r["dup_line_removed"],
                    r["paragraph_count"], r["sentence_count"]])
    _head(ws3, 8)
    for i, w in enumerate([14, 14, 14, 10, 12, 10, 10, 10], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    ws4 = wb.create_sheet("景区汇总")
    ws4.append(["景区", "官方路线", "游客游记", "合计", "平均清洗后字符数"])
    agg = defaultdict(lambda: {"official_route": 0, "travel_note": 0, "n": 0, "ch": 0})
    for r in rows:
        a = agg[r["scenic_area"]]
        a[r["text_type"]] += 1
        a["n"] += 1
        a["ch"] += r["clean_char_len"]
    for k, v in agg.items():
        ws4.append([k, v["official_route"], v["travel_note"],
                    v["official_route"] + v["travel_note"], round(v["ch"] / max(1, v["n"]))])
    _head(ws4, 5)
    for i, w in enumerate([26, 12, 12, 10, 20], 1):
        ws4.column_dimensions[get_column_letter(i)].width = w

    # 规则统计是后加的：写报告要说明"每条规则到底删了多少"，光有总数不够。
    ws5 = wb.create_sheet("规则统计")
    ws5.append(["规则", "全量删除量", "涉及文档数"])
    pairs = [
        ("R1 零宽/控制字符", "invisible_removed"),
        ("R2 残留HTML标签", "html_tag_removed"),
        ("R4 模板噪声行", "noise_line_removed"),
        ("R5 网址", "url_removed"),
        ("R5 邮箱", "email_removed"),
        ("R5 电话", "tel_removed"),
        ("R5 微信号", "wxid_removed"),
        ("R6 emoji", "emoji_removed"),
        ("R8 文档内重复行", "dup_line_removed"),
    ]
    for label, key in pairs:
        ws5.append([label, sum(r[key] for r in rows), sum(1 for r in rows if r[key] > 0)])
    _head(ws5, 3)
    ws5.column_dimensions["A"].width = 22
    ws5.column_dimensions["B"].width = 14
    ws5.column_dimensions["C"].width = 14

    wb.save(os.path.join(OUT_DIR, "dataset.xlsx"))


if __name__ == "__main__":
    main()
