# -*- coding: utf-8 -*-
"""填完封面后的最后一步：同步正式报告、重打实验材料包、按组号命名两个交付 zip。

    python tools/填封面.py          # 生成第N组_姓名_《数据挖掘》探索性实验报告.docx
    python tools/导出PDF.py          # 封面变了，PDF 要重导
    python tools/打包.py             # 真正执行
    python tools/打包.py --check     # 只检查前置条件和将做的动作，不改文件

支持两种布局：
  1) 完整工作副本（根目录有 交付包/）：交付目录 = 交付包/，两个 zip 输出到根目录；
  2) 解压后的交付包自身：交付目录 = 自身，zip 输出到上一级目录，避免把自己打包进去。

旧命名的 zip 不会被删除，只加「【废弃】」前缀；占位报告会从交付目录移除，
因为它在主项目 outputs/报告/ 里仍有原件，且正式报告会同时放回该目录。
"""

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
HAS_SUBDIR = (ROOT / "交付包").is_dir()
DELIVERY = (ROOT / "交付包") if HAS_SUBDIR else ROOT
ZIPDIR = ROOT if HAS_SUBDIR else ROOT.parent
CONF = ROOT / "tools" / "封面信息.json"
REPORT_DIR = ROOT / "outputs" / "报告"
SUFFIX = "_《数据挖掘》探索性实验报告.docx"
MATERIAL_SUFFIX = "_《数据挖掘》实验材料_项目1.zip"
RAW_SUFFIX = "_《数据挖掘》原始网页存档_项目1.zip"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".work", ".cache",
             ".test_tmp", ".test-tmp"}
# 封面配置含真名学号，属于本地私有文件，任何布局下都不能进交付 zip
SKIP_FILES = {"封面信息.json"}


def fail(msg):
    raise SystemExit("【打包中止】" + msg)


def load_prefix():
    if not CONF.is_file():
        fail("没有 tools/封面信息.json。先复制 tools/封面信息.示例.json 并填好，\n"
             "再跑 python tools/填封面.py 生成正式报告。")
    try:
        conf = json.loads(CONF.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("tools/封面信息.json 不是合法 JSON：%s" % e)
    gid = str(conf.get("组号") or "").strip()
    name = str((conf.get("组长") or {}).get("姓名") or "").strip()
    if not gid.isdigit() or not name:
        fail("封面信息里缺组号或组长姓名，没法确定交付包前缀。")
    return (str(conf.get("压缩包前缀") or "").strip()
            or "第%s组_%s" % (gid, name))


def find_formal(prefix):
    for p in (REPORT_DIR / (prefix + SUFFIX), ROOT / (prefix + SUFFIX)):
        if p.is_file():
            return p
    fail("没找到正式报告 %s%s。\n请先跑 python tools/填封面.py。" %
         (prefix, SUFFIX))


def md5_file(path):
    h = hashlib.md5()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def collect():
    """交付目录里所有该进 zip 的文件，返回相对路径 -> 绝对路径。"""
    out = {}
    for p in DELIVERY.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(DELIVERY)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if (p.suffix in (".pyc", ".zip") or p.name in SKIP_FILES
                or p.name.startswith("~$")):
            continue
        out[rel.as_posix()] = p
    return out


def same(a, b):
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def sync_report(formal, prefix, dry):
    print("\n[1/4] 同步正式报告" + ("（预演，不改文件）" if dry else ""))
    for p in sorted(DELIVERY.glob("*.docx")):
        if p.name.startswith("~$") or same(p, formal):
            continue
        print("  移除顶层旧报告  %s" % p.name)
        if not dry:
            p.unlink()
    top = DELIVERY / (prefix + SUFFIX)
    if not same(top, formal):
        print("  复制到交付顶层  %s" % top.name)
        if not dry:
            shutil.copy2(formal, top)
    sub = DELIVERY / "outputs" / "报告"
    if not dry:
        sub.mkdir(parents=True, exist_ok=True)
    for p in sorted(sub.glob("*.docx")):
        if "第X组" in p.name or "组长姓名" in p.name:
            print("  移除占位报告    outputs/报告/%s" % p.name)
            if not dry:
                p.unlink()
    inner = sub / (prefix + SUFFIX)
    if not same(inner, formal):
        print("  放回 outputs/报告/%s" % inner.name)
        if not dry:
            shutil.copy2(formal, inner)

    pdf = ROOT / "outputs" / "报告PDF预览.pdf"
    if pdf.is_file():
        dst = DELIVERY / "outputs" / "报告PDF预览.pdf"
        if not same(dst, pdf):
            print("  同步 PDF 预览")
            if not dry:
                shutil.copy2(pdf, dst)
        if pdf.stat().st_mtime < formal.stat().st_mtime:
            print("  WARN: PDF 比正式报告旧，先跑 python tools/导出PDF.py 重导")
    else:
        print("  WARN: 没有 outputs/报告PDF预览.pdf，跳过（不影响 docx 交付）")


def build_zip(target, dry):
    print("\n[2/4] 重打实验材料包" + ("（预演，不改文件）" if dry else ""))
    files = collect()
    print("  交付目录文件数  %d" % len(files))
    print("  目标            %s" % target.name)
    if dry:
        return files
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel, p in sorted(files.items()):
            z.write(p, rel)
    return files


def unique_deprecated(src):
    base = ZIPDIR / ("【废弃】" + src.name)
    if not base.exists():
        return base
    for i in range(2, 100):
        cand = ZIPDIR / ("【废弃】%d_%s" % (i, src.name))
        if not cand.exists():
            return cand
    return base


def deprecate_others(target, dry):
    print("\n[3/4] 处理旧命名 zip 与原始网页存档" + ("（预演，不改文件）" if dry else ""))
    for p in sorted(ZIPDIR.glob("*《数据挖掘》实验材料_项目1.zip")):
        if p == target or p.name.startswith("【废弃】"):
            continue
        dst = unique_deprecated(p)
        print("  旧实验材料包标记废弃  %s -> %s" % (p.name, dst.name))
        if not dry:
            p.rename(dst)

    raw_target = ZIPDIR / (prefix_from_raw_target_name(target))
    srcs = [p for p in sorted(ZIPDIR.glob("*《数据挖掘》原始网页存档_项目1.zip"))
            if p != raw_target and not p.name.startswith("【废弃】")]
    if raw_target.is_file():
        print("  原始网页存档已是正式命名  %s" % raw_target.name)
    elif not srcs:
        print("  未找到原始网页存档 zip（若单独交付可忽略）")
    else:
        src = srcs[0]
        print("  原始网页存档重命名  %s -> %s" % (src.name, raw_target.name))
        if not dry:
            src.rename(raw_target)


def prefix_from_raw_target_name(target):
    # target = {前缀}_《数据挖掘》实验材料_项目1.zip
    return target.name.replace(MATERIAL_SUFFIX, RAW_SUFFIX)


def verify(target, files, formal, prefix, dry):
    print("\n[4/4] 校验")
    if dry:
        print("  --check 模式跳过（不生成 zip）")
        return
    with zipfile.ZipFile(target) as z:
        bad = z.testzip()
        names = set(z.namelist())
        missing = sorted(set(files) - names)
        extra = sorted(names - set(files))
        diffs = [rel for rel in sorted(names & set(files))
                 if hashlib.md5(z.read(rel)).hexdigest() != md5_file(files[rel])]
        formal_name = prefix + SUFFIX
        report_ok = (formal_name in names and
                     hashlib.md5(z.read(formal_name)).hexdigest() == md5_file(formal))
        print("  条目            %d" % len(names))
        print("  完整性          %s" % ("通过" if bad is None else "损坏：" + str(bad)))
        print("  缺失/多出/差异  %d / %d / %d" % (len(missing), len(extra), len(diffs)))
        print("  包内报告与正式报告一致  %s" % ("是" if report_ok else "否"))
        if bad is not None or missing or extra or diffs or not report_ok:
            fail("校验没过，先别交这个 zip：%s%s" %
                 (missing[:3], diffs[:3]))
        print("  大小            %.1f MB" % (target.stat().st_size / 1024 / 1024))
    print("\n完成。交给老师的是：")
    print("  %s" % target)
    raw = ZIPDIR / prefix_from_raw_target_name(target)
    if raw.is_file():
        print("  %s" % raw)


def main():
    ap = argparse.ArgumentParser(description="填完封面后生成最终命名与内容的交付包")
    ap.add_argument("--check", action="store_true", help="只检查，不改动任何文件")
    args = ap.parse_args()
    prefix = load_prefix()
    formal = find_formal(prefix)
    target = ZIPDIR / (prefix + MATERIAL_SUFFIX)
    print("布局        %s" % ("完整工作副本" if HAS_SUBDIR else "交付包自身"))
    print("交付目录    %s" % DELIVERY)
    print("zip 输出    %s" % ZIPDIR)
    print("前缀        %s" % prefix)
    print("正式报告    %s" % formal)
    if args.check:
        print("\n--check：前置条件齐全，实际执行会同步报告、重打包并按前缀命名两个 zip。")
    sync_report(formal, prefix, args.check)
    files = build_zip(target, args.check)
    deprecate_others(target, args.check)
    verify(target, files, formal, prefix, args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
