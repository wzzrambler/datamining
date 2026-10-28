# -*- coding: utf-8 -*-
# 一把跑完三步：采集 -> 清洗 -> 质检。
# 采集比较慢（还容易被限流），只想改清洗的话加 --skip-crawl。

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(script, extra=None):
    cmd = [sys.executable, os.path.join(ROOT, "src", script)] + (extra or [])
    print("\n>>> " + " ".join(cmd), flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit("失败：%s" % script)
    print("<<< %.1fs" % (time.time() - t0), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="重采，忽略已有结果")
    ap.add_argument("--skip-crawl", action="store_true", help="跳过采集，只跑清洗和质检")
    args = ap.parse_args()

    if not args.skip_crawl:
        run("crawler.py", ["--fresh"] if args.fresh else [])
    run("clean.py")
    run("quality_check.py")

    print("\n好了，结果在：")
    for p in ["data/raw", "data/processed", "outputs"]:
        print("  " + os.path.join(ROOT, p))


if __name__ == "__main__":
    main()
