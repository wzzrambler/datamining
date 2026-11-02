# -*- coding: utf-8 -*-
# 把 tools/封面信息.json 里的组号、姓名、学号填进报告封面和组内评分表，
# 输出一份以「第N组_姓名」开头的正式文件。
#
#   copy tools\封面信息.示例.json tools\封面信息.json    # 先建配置，填好内容
#   python tools/填封面.py                              # 生成报告
#   python tools/填封面.py --check                      # 只校验配置，不动文件
#
# 待填的占位来自模板：封面表格里「第   组」「组长姓名/学号」「组员1姓名/学号」，
# 第二页的「组号：第   组」，以及组内评分表（模板只预置 2 个组员行，人多会自动加行）。
# 配置文件含真名学号，已被 .gitignore 排除，不会进仓库。

import copy
import glob
import json
import os
import re
import sys

import docx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "tools", "封面信息.json")
PLACEHOLDER = "第X组_组长姓名_《数据挖掘》探索性实验报告_项目1.docx"
OUTDIR = os.path.join(ROOT, "outputs")
SUFFIX = "_《数据挖掘》探索性实验报告_项目1.docx"


def load_conf():
    if not os.path.exists(CONF):
        raise SystemExit(
            "还没有 tools/封面信息.json（它含姓名学号，已被 .gitignore 排除）。\n"
            "先复制示例再填：\n"
            "  copy tools\\封面信息.示例.json tools\\封面信息.json\n"
            "或者在 bash 里：cp tools/封面信息.示例.json tools/封面信息.json")
    with open(CONF, encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except ValueError as e:
            raise SystemExit("封面信息.json 格式有误（常见是多了逗号或缺引号）：%s" % e)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def need(conf, key, where=""):
    v = conf.get(key)
    if not isinstance(v, str) or not v.strip():
        raise SystemExit("还差一项：%s%s，请在 tools/封面信息.json 里填上" % (where, key))
    return v.strip()


def validate(conf):
    gid = need(conf, "组号")
    if not gid.isdigit():
        raise SystemExit("组号只写数字，例如 3（现在是 %r）" % gid)

    leader = conf.get("组长") or {}
    lname = need(leader, "姓名", "组长")
    lnum = need(leader, "学号", "组长")
    if not re.fullmatch(r"\d{6,}", lnum):
        raise SystemExit("组长学号看着不像学号：%r（应为 6 位以上数字）" % lnum)

    members = conf.get("组员") or []
    if not members:
        raise SystemExit("组员至少要填 1 个人；如果是单人实验，请在组员里写上自己并说明")
    mem = []
    for i, m in enumerate(members, 1):
        name = need(m, "姓名", "第 %d 个组员" % i)
        mem.append({
            "姓名": name,
            "贡献": (m.get("贡献") or "").strip(),
            "评分": (m.get("评分") or "100").strip(),
        })

    teacher = need(conf, "授课教师")
    return {
        "组号": gid,
        "组长": {"姓名": lname, "学号": lnum,
                 "贡献": (leader.get("贡献") or "").strip(),
                 "评分": (leader.get("评分") or "100").strip()},
        "组员": mem,
        "授课教师": teacher,
        "前缀": (conf.get("压缩包前缀") or "").strip() or "第%s组_%s" % (gid, lname),
    }


def set_para_text(p, text):
    """保留第一个 run 的字体字号，把段落文字整体换掉。"""
    runs = p.runs
    if not runs:
        p.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r._r.getparent().remove(r._r)


def set_cell_lines(cell, lines):
    """把单元格设成若干行，行数不够就复制末段、多了就删，格式跟着原段落走。"""
    while len(cell.paragraphs) < len(lines):
        cell.paragraphs[-1]._p.addnext(copy.deepcopy(cell.paragraphs[-1]._p))
    while len(cell.paragraphs) > len(lines):
        p = cell.paragraphs[-1]._p
        p.getparent().remove(p)
    for p, text in zip(cell.paragraphs, lines):
        set_para_text(p, text)


def fill(doc, info):
    cover = doc.tables[0]
    # 封面：组号 / 组长 / 组员 / 授课教师
    set_cell_lines(cover.rows[2].cells[1], ["第 %s 组" % info["组号"]])
    set_cell_lines(cover.rows[3].cells[1],
                   ["%s / %s" % (info["组长"]["姓名"], info["组长"]["学号"])])
    set_cell_lines(cover.rows[4].cells[1],
                   [m["姓名"] for m in info["组员"]])
    set_cell_lines(cover.rows[6].cells[1], [info["授课教师"]])

    # 第二页「组号：第   组」
    para = None
    for p in doc.paragraphs[:40]:
        if p.text.strip().startswith("组号"):
            para = p
            break
    if para is None:
        raise SystemExit("没找到第二页的组号段落，报告结构可能被改过")
    set_para_text(para, "组号：第 %s 组" % info["组号"])

    # 组内评分表：1 行组长 + N 行组员，行的增减靠复制模板行
    score = doc.tables[2]
    if score.rows[0].cells[1].text.strip() != "角色":
        raise SystemExit("第 3 张表不像组内评分表，列头对不上")
    people = [info["组长"]] + info["组员"]
    want = 1 + len(people)
    while len(score.rows) < want:
        score._tbl.append(copy.deepcopy(score.rows[-1]._tr))
    while len(score.rows) > want:
        tr = score.rows[-1]._tr
        tr.getparent().remove(tr)

    for i, person in enumerate(people, 1):
        cells = score.rows[i].cells
        set_cell_lines(cells[0], [person["姓名"]])
        set_cell_lines(cells[1], ["组长" if i == 1 else "组员"])
        set_cell_lines(cells[2], [person["贡献"] or "—"])
        pct = "".join(c for c in person["评分"] if c.isdigit() or c == ".")
        set_cell_lines(cells[3], [(pct or "100") + "%"])


def main():
    info = validate(load_conf())
    print("配置校验通过：")
    print("  组号  第 %s 组" % info["组号"])
    print("  组长  %s / %s" % (info["组长"]["姓名"], info["组长"]["学号"]))
    for m in info["组员"]:
        print("  组员  %s" % m["姓名"])
    print("  教师  %s" % info["授课教师"])

    if "--check" in sys.argv:
        return

    src = os.path.join(OUTDIR, PLACEHOLDER)
    if not os.path.exists(src):
        raise SystemExit("找不到待填的模板报告：outputs/%s\n"
                         "先跑 python tools/fill_report.py 生成它" % PLACEHOLDER)

    doc = docx.Document(src)
    fill(doc, info)

    dst = os.path.join(OUTDIR, info["前缀"] + SUFFIX)
    if os.path.exists(dst):
        try:
            doc.save(dst)
        except PermissionError:
            raise SystemExit("目标文件被 Word 或预览面板占用，关掉后重跑：\n%s" % dst)
    else:
        doc.save(dst)
    print("\n已生成：outputs/%s" % os.path.basename(dst))

    others = [os.path.basename(f) for f in glob.glob(os.path.join(OUTDIR, "*" + SUFFIX))
              if os.path.basename(f) != os.path.basename(dst)]
    if others:
        print("outputs 里还有这些旧版本，打包时取最新修改的一份，建议删掉以免混淆：")
        for f in others:
            print("  " + f)

    print("\n接着按顺序跑：")
    print("  python tools/导出PDF.py     # 封面变了，PDF 要重导")
    print("  python tools/打包.py        # 生成两个提交 zip")


if __name__ == "__main__":
    main()
