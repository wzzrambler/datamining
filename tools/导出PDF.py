"""把报告 docx 导成 PDF，供预览和提交包里用。

PowerShell 里走 Word COM 的 ExportAsFixedFormat 会卡死（DisplayAlerts=0 也不管用），
换成 win32com 的 DispatchEx 开独立进程，先复制到临时目录再转，就能稳定跑完。

需要装了 Word 和 pywin32。没装 Word 的机器上这个脚本跑不了，报告 docx 本身不受影响。
"""

import glob
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = os.path.join(ROOT, "outputs", "报告PDF预览.pdf")


def newest_report():
    """取 outputs 下最新的报告。

    正式文件常被预览面板锁着，这时脚本会另存出"（新）"副本；填过封面后文件名
    也变成了「第3组_张三_…」，所以按后缀通配，不认死某一个前缀。
    """
    cands = [f for f in glob.glob(os.path.join(ROOT, "outputs",
                                               "*《数据挖掘》探索性实验报告_项目1*.docx"))
             if not os.path.basename(f).startswith("~$")]
    if not cands:
        raise SystemExit("outputs 下没有报告 docx")
    return max(cands, key=os.path.getmtime)


def convert():
    """返回导出的 PDF 路径。"""
    src = newest_report()
    if not os.path.exists(src):
        raise SystemExit("找不到报告：%s" % src)
    tmp_doc = os.path.join(tempfile.gettempdir(), "rep_export.docx")
    tmp_pdf = os.path.join(tempfile.gettempdir(), "rep_export.pdf")
    shutil.copyfile(src, tmp_doc)
    if os.path.exists(tmp_pdf):
        os.remove(tmp_pdf)

    import win32com.client as win32

    app = win32.DispatchEx("Word.Application")
    app.Visible = False
    app.DisplayAlerts = 0
    try:
        doc = app.Documents.Open(tmp_doc, ReadOnly=True)
        doc.SaveAs2(tmp_pdf, FileFormat=17)
        doc.Close(False)
    finally:
        app.Quit()
    if not os.path.exists(tmp_pdf):
        raise SystemExit("导出没生成文件")
    return tmp_pdf


def page_count(path):
    try:
        import pymupdf
    except ImportError:
        return None
    with pymupdf.open(path) as d:
        return d.page_count


def replace(src, dst):
    """预览面板可能占着目标文件，重试几次再放弃。"""
    for _ in range(8):
        try:
            shutil.copyfile(src, dst)
            return True
        except PermissionError:
            time.sleep(1)
    return False


def main():
    pdf = convert()
    pages = page_count(pdf)
    if replace(pdf, DST):
        print("已生成 %s（%s 页，%.2f MB）" % (
            os.path.basename(DST), pages, os.path.getsize(DST) / 1024 / 1024))
    else:
        alt = os.path.join(ROOT, "outputs", "_new.pdf")
        shutil.copyfile(pdf, alt)
        print("目标被占用，已改存 %s（%s 页）" % (os.path.basename(alt), pages))
        sys.exit(1)


if __name__ == "__main__":
    main()
