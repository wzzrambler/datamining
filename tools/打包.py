# -*- coding: utf-8 -*-
# 打两个提交 zip：实验材料包（代码+数据+报告）和原始网页存档包。
# 报告取 outputs 下最新的一份，zip 名跟着报告文件名走（填过封面就是「第3组_张三_…」）。
#
#   python tools/打包.py

import glob
import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PKG = "提交包"


def newest_report():
    """取 outputs 下最新的报告，返回 (路径, 前缀)。

    前缀就是 zip 名和包内文件名的开头，比如「第3组_张三」。填过封面之后文名变了，
    所以这里按后缀通配，不再认死 NAME。
    """
    cands = [f for f in glob.glob("outputs/*《数据挖掘》探索性实验报告_项目1*.docx")
             if not os.path.basename(f).startswith("~$")]
    if not cands:
        raise SystemExit("outputs 下没有报告 docx，先跑 tools/fill_report.py")
    src = max(cands, key=os.path.getmtime)
    prefix = os.path.basename(src).split("_《数据挖掘》")[0]
    return src, prefix


REPORT, PREFIX = newest_report()

# 组装主包目录
if os.path.isdir(PKG):
    shutil.rmtree(PKG)
for d in ["/src", "/tools", "/config", "/data/raw/manual", "/data/processed",
          "/outputs/figures"]:
    os.makedirs(PKG + d, exist_ok=True)

shutil.copy(REPORT, os.path.join(PKG, os.path.basename(REPORT)))
print("报告用：%s" % os.path.basename(REPORT))
for f in glob.glob("src/*.py"):
    shutil.copy(f, PKG + "/src")
for f in glob.glob("tools/*.py"):
    if os.path.basename(f) != "打包.py":
        shutil.copy(f, PKG + "/tools")
shutil.copy("config/sources.json", PKG + "/config")
shutil.copy("README.md", PKG)
shutil.copy("requirements.txt", PKG)
for f in ["raw_documents.jsonl", "crawl_log.csv", "archive_inventory.csv", "README.md"]:
    shutil.copy("data/raw/" + f, PKG + "/data/raw")
for f in glob.glob("data/raw/manual/*"):
    shutil.copy(f, PKG + "/data/raw/manual")
for f in glob.glob("data/processed/*"):
    if os.path.isfile(f):
        shutil.copy(f, PKG + "/data/processed")
for f in glob.glob("outputs/figures/*.png"):
    shutil.copy(f, PKG + "/outputs/figures")
for f in ["quality_report.md", "quality_stats.json", "agent_runs.json", "agent_demo.txt"]:
    shutil.copy("outputs/" + f, PKG + "/outputs")

zp = "%s_《数据挖掘》实验材料_项目1.zip" % PREFIX
for old in glob.glob("*_《数据挖掘》实验材料_项目1.zip"):
    if old != zp:
        os.remove(old)
if os.path.exists(zp):
    os.remove(zp)
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(PKG):
        for f in files:
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, PKG))
print("主包 %.1f MB，%d 个文件" % (
    os.path.getsize(zp) / 1024 / 1024,
    sum(len(f) for _, _, f in os.walk(PKG))))

# 原始网页存档包：快照 html + 索引 + 来源说明
zp2 = "%s_《数据挖掘》原始网页存档_项目1.zip" % PREFIX
for old in glob.glob("*_《数据挖掘》原始网页存档_项目1.zip"):
    if old != zp2:
        os.remove(old)
if os.path.exists(zp2):
    os.remove(zp2)
with zipfile.ZipFile(zp2, "w", zipfile.ZIP_DEFLATED) as z:
    z.write("data/raw/source_archive/原始网页索引.csv", "原始网页索引.csv")
    for f in sorted(glob.glob("data/raw/source_archive/raw_html/*.html")):
        z.write(f, "raw_html/" + os.path.basename(f))
    z.write("data/raw/README.md", "原始数据来源说明.md")
print("存档包 %.1f MB，%d 个文件" % (
    os.path.getsize(zp2) / 1024 / 1024,
    len(zipfile.ZipFile(zp2).namelist())))
