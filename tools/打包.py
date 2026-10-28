# -*- coding: utf-8 -*-
# 打两个提交 zip：实验材料包（代码+数据+报告）和原始网页存档包。
# 报告优先用最新的「（新）」版本，正式文件被预览占用时也不影响打包。
#
#   python tools/打包.py

import glob
import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

NAME = "第X组_组长姓名_《数据挖掘》探索性实验报告_项目1"
PKG = "提交包"

# 组装主包目录
if os.path.isdir(PKG):
    shutil.rmtree(PKG)
for d in ["/src", "/tools", "/config", "/data/raw/manual", "/data/processed",
          "/outputs/figures"]:
    os.makedirs(PKG + d, exist_ok=True)

cands = sorted(glob.glob("outputs/%s*.docx" % NAME),
               key=os.path.getmtime, reverse=True)
shutil.copy(cands[0], os.path.join(PKG, NAME + ".docx"))
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

zp = "第X组_组长姓名_《数据挖掘》实验材料_项目1.zip"
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
zp2 = "第X组_组长姓名_《数据挖掘》原始网页存档_项目1.zip"
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
