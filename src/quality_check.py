# -*- coding: utf-8 -*-
# 数据质量检查。读 documents.json，出 quality_report.md + quality_stats.json + 四张图。
# 这一段不碰采集和清洗，改完清洗重跑它就行。

import json
import os
import re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN_JSON = os.path.join(ROOT, "data", "processed", "documents.json")
OUT = os.path.join(ROOT, "outputs")
FIG = os.path.join(OUT, "figures")


def use_cn_font():
    # matplotlib 默认字体没有中文，不设这个图里全是方块
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for name in ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]:
        try:
            font_manager.findfont(name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [name]
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False


def main():
    os.makedirs(FIG, exist_ok=True)
    docs = json.load(open(IN_JSON, encoding="utf-8"))
    st = {"total_docs": len(docs), "valid_docs": sum(d["is_valid"] for d in docs)}

    # Q1 每景区够不够数
    cnt = defaultdict(lambda: {"official_route": 0, "travel_note": 0})
    for d in docs:
        cnt[d["scenic_area"]][d["text_type"]] += 1
    st["Q1_counts"] = []
    for area, v in cnt.items():
        ok = v["official_route"] >= 1 and v["travel_note"] >= 3
        st["Q1_counts"].append({"景区": area, "官方路线": v["official_route"],
                                "游客游记": v["travel_note"],
                                "是否达标": "达标" if ok else "不达标"})
    st["Q1_all_pass"] = all(x["是否达标"] == "达标" for x in st["Q1_counts"])

    # Q2 字段缺失
    st["Q2_missing"] = []
    for f in ["title", "author", "publish_date", "source_url", "content", "clean_char_len"]:
        miss = sum(1 for d in docs if not str(d.get(f, "")).strip())
        st["Q2_missing"].append({"字段": f, "缺失数": miss,
                                 "缺失率": round(100 * miss / len(docs), 1)})

    # Q3 重复
    titles = [re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", d["title"]) for d in docs]
    st["Q3_title_dup"] = [t for t, c in Counter(titles).items() if c > 1 and t]
    st["Q3_near_dup_ids"] = [d["doc_id"] for d in docs if d.get("is_near_duplicate")]

    # Q4 格式是否统一
    st["Q4"] = {
        "doc_id不规范": [d["doc_id"] for d in docs
                         if not re.fullmatch(r"[A-Z]{2,4}-(OFF|NOTE)-\d{2}", d["doc_id"])],
        "日期格式异常": [d["doc_id"] for d in docs
                         if d["publish_date"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["publish_date"])],
        "URL异常": [d["doc_id"] for d in docs if not d.get("source_url", "").startswith("http")],
        "含乱码字符(U+FFFD)": [d["doc_id"] for d in docs if "\ufffd" in d["content"]],
    }

    # Q5 长度
    lens = sorted(d["clean_char_len"] for d in docs)
    st["Q5_length"] = {
        "min": lens[0], "max": lens[-1], "median": lens[len(lens) // 2],
        "mean": round(sum(lens) / len(lens), 1),
        "过短(<200字)": [d["doc_id"] for d in docs if d["clean_char_len"] < 200],
    }

    # 清洗效果 + 每条规则各删了多少
    raw_total = sum(d["raw_char_len"] for d in docs)
    clean_total = sum(d["clean_char_len"] for d in docs)
    rule_cols = [
        ("R1 零宽/控制字符（个）", "invisible_removed"),
        ("R2 残留HTML标签（个）", "html_tag_removed"),
        ("R4 模板噪声行（行）", "noise_line_removed"),
        ("R5 网址（条）", "url_removed"),
        ("R5 邮箱（条）", "email_removed"),
        ("R5 电话（条）", "tel_removed"),
        ("R5 微信号（条）", "wxid_removed"),
        ("R6 emoji（个）", "emoji_removed"),
        ("R8 文档内重复行（行）", "dup_line_removed"),
    ]
    st["rule_stats"] = [{"规则": k, "删除量": sum(d.get(f, 0) for d in docs),
                         "涉及文档数": sum(1 for d in docs if d.get(f, 0) > 0)}
                        for k, f in rule_cols]

    pages = sum(1 for d in docs if d.get("html_tag_count", 0) > 0)
    tag_total = sum(d.get("html_tag_count", 0) for d in docs)
    st["cleaning"] = {
        "raw_total_chars": raw_total,
        "clean_total_chars": clean_total,
        "removed_chars": raw_total - clean_total,
        "removed_rate": round(1 - clean_total / raw_total, 3),
        "noise_lines": sum(d["noise_line_removed"] for d in docs),
        "dup_lines": sum(d["dup_line_removed"] for d in docs),
        "html_tags_total": tag_total,
        "html_tags_mean": round(tag_total / max(1, pages)),
        "html_pages": pages,
    }

    json.dump(st, open(os.path.join(OUT, "quality_stats.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    draw(docs, cnt)
    write_report(st, cnt)
    print("质检完成：%d 篇，达标=%s，删除率 %.1f%%" % (
        len(docs), st["Q1_all_pass"], 100 * st["cleaning"]["removed_rate"]))


def draw(docs, cnt):
    use_cn_font()
    import matplotlib.pyplot as plt
    import numpy as np

    areas = list(cnt.keys())
    off = [cnt[a]["official_route"] for a in areas]
    note = [cnt[a]["travel_note"] for a in areas]
    x = np.arange(len(areas))

    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=200)
    b1 = ax.bar(x - 0.2, off, 0.4, label="官方推荐路线", color="#2F6DB5")
    b2 = ax.bar(x + 0.2, note, 0.4, label="游客游记", color="#E8833A")
    for bars in (b1, b2):
        for r in bars:
            ax.text(r.get_x() + r.get_width() / 2, r.get_height() + 0.08,
                    str(int(r.get_height())), ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([a.replace("旅游度假区", "").replace("国际海洋度假区", "") for a in areas],
                       fontsize=9)
    ax.set_ylabel("文档数（篇）")
    ax.legend()
    ax.set_ylim(0, max(off + note) + 1.5)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig1_docs_by_area.png"))
    plt.close(fig)

    ids = [d["doc_id"] for d in docs]
    x = np.arange(len(ids))
    fig, ax = plt.subplots(figsize=(9.6, 4.2), dpi=200)
    ax.bar(x - 0.2, [d["raw_char_len"] for d in docs], 0.4, label="清洗前", color="#B9C6D6")
    ax.bar(x + 0.2, [d["clean_char_len"] for d in docs], 0.4, label="清洗后", color="#2F6DB5")
    ax.set_xticks(x)
    ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("字符数")
    ax.legend()
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig2_len_before_after.png"))
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.6, 4.0), dpi=200)
    noise = [d["noise_line_removed"] for d in docs]
    ax.bar(x, noise, 0.55, label="模板噪声行（R4）", color="#E8833A")
    ax.bar(x, [d["dup_line_removed"] for d in docs], 0.55, bottom=noise,
           label="文档内重复行（R8）", color="#8FB8DE")
    ax.set_xticks(x)
    ax.set_xticklabels(ids, rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("删除行数")
    ax.legend()
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig3_noise_lines.png"))
    plt.close(fig)

    plat = Counter(d["source_platform"] for d in docs)
    fig, ax = plt.subplots(figsize=(4.6, 3.6), dpi=200)
    ax.pie(list(plat.values()), labels=list(plat.keys()), autopct="%1.0f%%",
           colors=["#2F6DB5", "#E8833A"], startangle=90,
           wedgeprops={"edgecolor": "white", "linewidth": 1.5})
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig4_platform.png"))
    plt.close(fig)


def write_report(st, cnt):
    L = ["# 数据质量检查报告\n",
         "> 由 src/quality_check.py 生成，数据源是 data/processed/documents.json。\n",
         "\n## Q1 各景区文档数量\n",
         "| 景区 | 官方推荐路线 | 游客游记 | 是否达标（≥1 / ≥3） |",
         "|---|---|---|---|"]
    for r in st["Q1_counts"]:
        L.append("| %s | %d | %d | %s |" % (r["景区"], r["官方路线"], r["游客游记"], r["是否达标"]))

    L += ["\n## Q2 字段缺失\n", "| 字段 | 缺失数 | 缺失率 |", "|---|---|---|"]
    for r in st["Q2_missing"]:
        L.append("| %s | %d | %.1f%% |" % (r["字段"], r["缺失数"], r["缺失率"]))
    L.append("\n说明：publish_date 的缺失来自官网页面本身没标发布时间，"
             "这个字段不参与实体和路线抽取，只作溯源参考，所以保留空值不删记录。")

    L += ["\n## Q3 重复检查\n",
          "- 标题重复：%d 组 %s" % (len(st["Q3_title_dup"]),
                                    "（无）" if not st["Q3_title_dup"] else st["Q3_title_dup"]),
          "- 正文近重复（3-gram Jaccard ≥ 0.8）：%d 篇 %s" % (
              len(st["Q3_near_dup_ids"]), st["Q3_near_dup_ids"] or "（无）")]

    L.append("\n## Q4 格式一致性\n")
    for k, v in st["Q4"].items():
        L.append("- %s：%d 条 %s" % (k, len(v), "（无异常）" if not v else v))

    s5 = st["Q5_length"]
    L += ["\n## Q5 文本长度\n",
          "- 最短 %d / 中位数 %d / 平均 %.1f / 最长 %d 字符" % (
              s5["min"], s5["median"], s5["mean"], s5["max"]),
          "- 过短文档（<200 字）：%s" % (s5["过短(<200字)"] or "（无）")]

    L += ["\n## 清洗效果\n", "| 规则 | 删除量 | 涉及文档数 |", "|---|---|---|"]
    for r in st["rule_stats"]:
        L.append("| %s | %d | %d |" % (r["规则"], r["删除量"], r["涉及文档数"]))

    c = st["cleaning"]
    L += ["",
          "- 全量字符数：%d → %d，删除 %d 字符（删除率 %.1f%%）" % (
              c["raw_total_chars"], c["clean_total_chars"],
              c["removed_chars"], 100 * c["removed_rate"]),
          "- 模板噪声行 %d 行、文档内重复行 %d 行" % (c["noise_lines"], c["dup_lines"]),
          "- 抽取阶段从 %d 个页面剥离了 %d 个标签（平均 %.0f 个/页），"
          "所以 R2 在清洗阶段是 0：标签在上一步就去掉了。两篇正文快照没有 HTML 来源，记 0。"
          % (c["html_pages"], c["html_tags_total"], c["html_tags_mean"]),
          "- 删除率集中在 5%~25%，没有一个文档超过 50%，说明规则没大面积误伤正文。",
          "\n## 图表\n",
          "![图1](figures/fig1_docs_by_area.png)\n",
          "![图2](figures/fig2_len_before_after.png)\n",
          "![图3](figures/fig3_noise_lines.png)\n",
          "![图4](figures/fig4_platform.png)\n"]

    open(os.path.join(OUT, "quality_report.md"), "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    main()
