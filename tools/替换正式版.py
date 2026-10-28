# -*- coding: utf-8 -*-
# 报告被预览面板或 Word/WPS 打开时没法直接覆盖，fill_report.py 只能另存成
# 「（新）」「（新2）」这类名字。关掉文档后跑这个脚本，把最新的那份
# 换成正式文件名，其余候选清掉。
#
#   python tools/替换正式版.py

import glob
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "第X组_组长姓名_《数据挖掘》探索性实验报告_项目1"
OUT = os.path.join(ROOT, "outputs")
main = os.path.join(OUT, NAME + ".docx")

# 同名前缀的候选都算上，按修改时间取最新（就是最后一次生成的那份）
cands = [p for p in glob.glob(os.path.join(OUT, NAME + "*.docx")) if p != main]
if not cands:
    print("没有候选文件，可能已经替换过了")
    raise SystemExit
new = max(cands, key=os.path.getmtime)

for i in range(20):
    try:
        os.replace(new, main)
        print("已用 %s 替换为：%s.docx" % (os.path.basename(new), NAME))
        break
    except PermissionError:
        time.sleep(1)
else:
    print("正式文件还开着，请先关掉预览/Word/WPS 里的报告，再运行一次。")
