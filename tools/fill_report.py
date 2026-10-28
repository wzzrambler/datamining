# -*- coding: utf-8 -*-
"""
把"实验项目1"章节内容写入报告模板，生成可提交的 docx。

    python tools/fill_report.py

输入：桌面上的报告模板 docx
输出：outputs/第X组_组长姓名_《数据挖掘》探索性实验报告_项目1.docx
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 模板放在桌面，用当前用户的家目录拼，别把本机路径写死进仓库
TEMPLATE = os.path.join(os.path.expanduser("~"), "Desktop",
                        "第X组_组长姓名_《数据挖掘》探索性实验报告（模板）.docx")
OUT = os.path.join(ROOT, "outputs", "第X组_组长姓名_《数据挖掘》探索性实验报告_项目1.docx")
FIG = os.path.join(ROOT, "outputs", "figures")

# 内容块的写法：("p", 文本) 正文、("ph", 文本) 加粗小标题、("code", 文本) 等宽代码块、
# ("tbl", 表题, 表头, 行) 表格、("fig", 文件名, 图题, 宽度cm) 插图。
# 章节内容放在单独模块里，改文字不用动排版逻辑
from content_project1 import CONTENT as C1  # noqa: E402
from content_p2p4 import CONTENT as C2  # noqa: E402
from content_p5p7 import CONTENT as C3  # noqa: E402

CONTENT = {**C1, **C2, **C3}

# 总结那几节在模板里是普通段落，不是 GuideH2，得按文本找
SUMMARY = [
    ("S1", "失败现象记录"),
    ("S2", "失败原因深度分析"),
    ("S3", "3、改进/优化方案"),
    ("S4", "梳理现有技术方案的局限性"),
    ("S5", "选定一个优先突破的技术点，并给出说明"),
    ("S6", "突破该技术点的研究思路或实施方案"),
]

# docx 操作辅助
def set_run_font(run, name="宋体", size=None, bold=None, color=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)


def add_para_after(doc, anchor, text, style="GuideBody", bold=False, size=10.5,
                   align=None, indent=True, space_before=0, space_after=3,
                   line_spacing=1.45, font="宋体"):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    set_run_font(run, font, size, bold or None)
    pf = p.paragraph_format
    pf.first_line_indent = Cm(0.74) if indent else Cm(0)
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line_spacing
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    anchor.addnext(p._p)
    return p._p


def add_code_after(doc, anchor, text):
    p = doc.add_paragraph()
    run = p.add_run(text)
    set_run_font(run, "Consolas", 9)
    pf = p.paragraph_format
    pf.left_indent = Cm(0.6)
    pf.space_before = Pt(2)
    pf.space_after = Pt(4)
    pf.line_spacing = 1.15
    # 浅灰底纹
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F3F4F6")
    p._p.get_or_add_pPr().append(shd)
    anchor.addnext(p._p)
    return p._p


def add_picture_after(doc, anchor, img_path, caption, width_cm):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(img_path, width=Cm(width_cm))
    anchor.addnext(p._p)

    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(8)
    r = cap.add_run(caption)
    set_run_font(r, "宋体", 9)
    p._p.addnext(cap._p)
    return cap._p


def _set_cell_border(cell):
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement("w:%s" % edge)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), "A6A6A6")
        borders.append(el)
    tcPr.append(borders)


def add_table_after(doc, anchor, caption, header, rows, widths=None, font_size=8.5):
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_before = Pt(6)
    cap.paragraph_format.space_after = Pt(2)
    r = cap.add_run(caption)
    set_run_font(r, "宋体", 9, bold=True)
    anchor.addnext(cap._p)

    table = doc.add_table(rows=1 + len(rows), cols=len(header))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = widths is None
    try:
        table.style = doc.styles["Table Grid"]
    except KeyError:
        pass

    if widths:
        tblPr = table._tbl.tblPr
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tblPr.append(layout)
        for j, w in enumerate(widths):
            for row in table.rows:
                row.cells[j].width = Cm(w)

    # 表头跨页重复
    trPr = table.rows[0]._tr.get_or_add_trPr()
    hdr = OxmlElement("w:tblHeader")
    hdr.set(qn("w:val"), "true")
    trPr.append(hdr)

    for j, h in enumerate(header):
        cell = table.rows[0].cells[j]
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rr = p.add_run(h)
        set_run_font(rr, "宋体", font_size, bold=True)
        # 表头浅蓝底纹
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "DDEBF7")
        cell._tc.get_or_add_tcPr().append(shd)
        _set_cell_border(cell)

    for i, row in enumerate(rows, start=1):
        for j, val in enumerate(row):
            cell = table.rows[i].cells[j]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if j == 0 or len(str(val)) <= 8 \
                else WD_ALIGN_PARAGRAPH.LEFT
            rr = p.add_run(str(val))
            set_run_font(rr, "宋体", font_size)
            _set_cell_border(cell)

    # 行内不断行、整表不跨页。必须放在填完单元格之后：
    # cell.text = "" 会重建段落，之前写的 keepNext 会被清掉
    for i, row in enumerate(table.rows):
        trpr = row._tr.get_or_add_trPr()
        trpr.append(OxmlElement("w:cantSplit"))
        if i < len(table.rows) - 1:
            for cell in row.cells:
                for para in cell.paragraphs:
                    para.paragraph_format.keep_with_next = True

    cap._p.addnext(table._tbl)

    # 表后留一点空
    tail = doc.add_paragraph()
    tail.paragraph_format.space_after = Pt(2)
    tail.paragraph_format.line_spacing = 1.0
    for r_ in tail.runs:
        r_.font.size = Pt(6)
    table._tbl.addnext(tail._p)
    return tail._p


def _p_style_id(el):
    pPr = el.find(qn("w:pPr"))
    if pPr is None:
        return None
    st = pPr.find(qn("w:pStyle"))
    return st.get(qn("w:val")) if st is not None else None


def find_heading(doc, prefix):
    for p in doc.paragraphs:
        if p.style.name == "GuideH2" and p.text.strip().startswith(prefix):
            return p
    return None


def section_end(doc, heading_p):
    """返回本章节结束处（下一个 GuideH2 标题，或项目2 的分页标题元素）。

    注意：docx 里段落的 pStyle 存的是 styleId（模板中 GuideH2 的 id 就是 "28"），
    所以必须先建立 styleId 集合，不能直接拿样式名比较。
    """
    gid = {st.style_id for st in doc.styles if st.name == "GuideH2"}
    for el in heading_p.itersiblings():
        if el.tag != qn("w:p"):
            continue
        if _p_style_id(el) in gid:
            return el
        if "".join(el.itertext()).strip().startswith("《数据挖掘》探索性实验报告"):
            return el
    return None


def clear_between(heading_p, stop_el):
    body = heading_p.getparent()
    cur = heading_p.getnext()
    while cur is not None and cur is not stop_el:
        nxt = cur.getnext()
        body.remove(cur)
        cur = nxt


def render_blocks(doc, anchor, blocks):
    """把一组内容块依次插到 anchor 之后，返回最后一个元素。"""
    for block in blocks:
        kind = block[0]
        if kind == "p":
            anchor = add_para_after(doc, anchor, block[1])
        elif kind == "ph":
            anchor = add_para_after(doc, anchor, block[1], bold=True,
                                    space_before=6, indent=False)
        elif kind == "code":
            anchor = add_code_after(doc, anchor, block[1])
        elif kind == "tbl":
            anchor = add_table_after(doc, anchor, block[1], block[2], block[3],
                                     block[4] if len(block) > 4 else None)
        elif kind == "fig":
            anchor = add_picture_after(doc, anchor,
                                       os.path.join(FIG, block[1]), block[2], block[3])
    return anchor


def find_plain(doc, text):
    for p in doc.paragraphs:
        if p.text.strip() == text:
            return p
    return None


def fill_summary(doc):
    heads = []
    for key, title in SUMMARY:
        p = find_plain(doc, title)
        if p is None:
            raise SystemExit("未找到总结小标题：%s" % title)
        heads.append((key, p))

    # 每节的边界是下一个小标题；最后一节到"附录"
    tail = find_plain(doc, "附录")
    for i, (key, p) in enumerate(heads):
        stop = heads[i + 1][1] if i + 1 < len(heads) else tail
        if stop is None:
            raise SystemExit("总结部分找不到结束边界")
        clear_between(p._p, stop._p)
        render_blocks(doc, p._p, CONTENT[key])
        print("  已填写 %s（%s）" % (key, p.text.strip()[:12]))


def main():
    doc = docx.Document(TEMPLATE)

    for key in sorted(CONTENT.keys()):
        if key.startswith("S"):
            continue
        h = find_heading(doc, key)
        if h is None:
            raise SystemExit("未找到标题：%s" % key)
        stop = section_end(doc, h._p)
        if stop is None:
            raise SystemExit("未找到 %s 的结束边界" % key)
        clear_between(h._p, stop)
        render_blocks(doc, h._p, CONTENT[key])
        print("  已填写 %s" % key)

    fill_summary(doc)

    out = OUT
    argv = sys.argv[1:]
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    save_with_fallback(doc, out)
    print("已生成：", out)


def save_with_fallback(doc, out):
    """目标文件被 Word/WPS 占用时改存「（新）」，别让整轮生成白跑。

    预览面板会一直握着 docx 的句柄，直接覆盖必然 PermissionError。
    先试几次原地保存，不行就写同目录的「（新）」，
    关掉预览后跑 tools/替换正式版.py 换回来。
    """
    import time

    for _ in range(3):
        try:
            doc.save(out)
            return
        except PermissionError:
            time.sleep(0.6)
    # 「（新）」也可能还开着，按序号往后找，总能找到一个没人占的
    stem, ext = os.path.splitext(out)
    for tag in ["（新）", "（新2）", "（新3）", "（新4）"]:
        alt = stem + tag + ext
        try:
            doc.save(alt)
        except PermissionError:
            continue
        print("目标文件被占用，已改存：", os.path.basename(alt))
        return
    raise SystemExit("outputs 下的报告都被打开了，关掉预览再跑一次")


if __name__ == "__main__":
    main()
