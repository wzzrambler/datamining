# -*- coding: utf-8 -*-
# 一把跑完项目1~7。默认不联网：先从随工程提交的网页存档重建原始正文，再往下跑。
#
#   python src/run_all.py              离线跑（推荐）
#   python src/run_all.py --crawl      真的去抓网页（会被限流，一般不需要）
#   python src/run_all.py --from N     从第 N 步开始跑

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STEPS = [
    ("rebuild_from_archive.py", []),          # 从存档重建原始正文
    ("clean.py", []),                          # 清洗 + 结构化
    ("quality_check.py", []),                  # 质量检查 + 图
    ("p2_entities.py", ["--version", "v1"]),   # 实体识别基线
    ("p2_entities.py", ["--version", "v2"]),   # 实体识别正式版 + 词云
    ("p3_route.py", []),                       # 路线解析 + 官方游客对比
    ("p4_coref.py", ["--mode", "detect"]),     # 代词检测
    ("p4_coref.py", ["--mode", "eval"]),       # 消解 + 评估
    ("p5_conditions.py", []),                  # 条件建议抽取
    ("p6_graph.py", []),                       # 知识图谱
    ("p7_agent.py", []),                       # ReAct Agent
]


def run(script, extra):
    cmd = [sys.executable, os.path.join(ROOT, "src", script)] + extra
    print("\n>>> " + " ".join(cmd), flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit("失败：%s" % script)
    print("<<< %.1fs" % (time.time() - t0), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl", action="store_true", help="联网抓取，不用存档")
    ap.add_argument("--from", dest="start", type=int, default=1, help="从第几步开始")
    args = ap.parse_args()

    if args.crawl:
        run("crawler.py", [])
    for i, (script, extra) in enumerate(STEPS, 1):
        if args.crawl and script == "rebuild_from_archive.py":
            continue
        if i < args.start:
            continue
        run(script, extra)

    print("\n全部跑完，产物在：")
    for p in ["data/raw", "data/processed", "outputs/figures"]:
        print("  " + os.path.join(ROOT, p))
    print("\n报告用 python tools/fill_report.py 重新生成。")


if __name__ == "__main__":
    main()
