# -*- coding: utf-8 -*-
# 项目4：程序性知识的共指消解。
#
# 三步：找代词 -> 人工标注 -> 规则消解并评估。
#
#   python src/p4_coref.py --mode detect    抽候选，写 coref_candidates.json
#   python src/p4_coref.py --mode eval      跑消解，和人工标注比，算准确率
#
# 人工标注放在 data/processed/coref_gold.json，是看到候选句子之后手写的，
# 没用模型生成，所以评估结果才有意义。

import argparse
import json
import os
import re
from collections import Counter

import jieba
import jieba.posseg as pseg

import domain_dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "data", "processed", "documents.json")
ENTS = os.path.join(ROOT, "data", "processed", "entities_v2.json")
OUT = os.path.join(ROOT, "data", "processed")
FIG = os.path.join(ROOT, "outputs", "figures")
GOLD = os.path.join(OUT, "coref_gold.json")

# "其它"里也有个"它"，不排掉会多出一堆假样本
MASK_WORDS = ["其它", "其他", "其"]
PRON = ["它们", "他们", "她们", "它", "这里", "那里", "这儿", "那儿", "这边", "那边"]
PRON_RE = re.compile("|".join(sorted(PRON, key=len, reverse=True)))

# 指代人的代词单独处理，旅游文本里多是"我们/他们"这种泛指，不参与评估
SKIP_SENT = ("我们",)

NOUN_FLAG = set("n ns nz nr nt s ng an j")


def mask(text):
    for w in MASK_WORDS:
        text = text.replace(w, "○" * len(w))
    return text


def split_sents(para_text):
    """按句末标点切句，保留标点。返回 [(句子, 段内起始偏移)]。"""
    out = []
    pos = 0
    for m in re.finditer(r"[^。！？；]+[。！？；]?", para_text):
        s = m.group(0)
        if s.strip():
            out.append((s, pos))
        pos = m.end()
    return out


def detect():
    docs = json.load(open(DOCS, encoding="utf-8"))
    cands = []
    sid = 0
    for d in docs:
        if d["text_type"] != "travel_note":
            continue
        for para in d["paragraphs"]:
            sents = split_sents(para["text"])
            for k, (sent, off) in enumerate(sents):
                masked = mask(sent)
                ms = list(PRON_RE.finditer(masked))
                if not ms:
                    continue
                # 前一句当上下文
                prev = sents[k - 1][0] if k > 0 else ""
                for m in ms:
                    pron = sent[m.start():m.end()]
                    sid += 1
                    cands.append({
                        "id": "C%03d" % sid,
                        "doc_id": d["doc_id"],
                        "scenic_area": d["scenic_area"],
                        "para_id": para["i"],
                        "sent_no": k + 1,
                        "pronoun": pron,
                        "pron_start": para["start"] + off + m.start(),
                        "pron_end": para["start"] + off + m.end(),
                        "prev_sent": prev,
                        "sentence": sent,
                    })
    json.dump(cands, open(os.path.join(OUT, "coref_candidates.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("检出含代词句子 %d 处，涉及 %d 篇游记" % (
        len(cands), len({c["doc_id"] for c in cands})))
    cnt = Counter(c["pronoun"] for c in cands)
    print("代词分布：", "，".join("%s %d" % (k, v) for k, v in cnt.most_common()))
    return cands


def load_poi():
    data = json.load(open(ENTS, encoding="utf-8"))
    s = set()
    for r in data:
        for e in r["entities"]:
            if e["type"] == "POI":
                s.add(e["text"])
    # 项目2 认出来的景点名要喂回分词，否则"黑颈天鹅"还是会被切开
    for w in s:
        if len(w) >= 3:
            jieba.add_word(w)
    return s


# 指人的代词（他们/她们）多半指人、指动物，不指地点。
# 这张表是手写的基础动物名，用来给这类代词换一套打分：不再给景点加分，改给动物名加分。
PERSONAL_PRON = {"他们", "她们", "他", "她"}
ANIMALS = set("""大象 熊猫 考拉 长颈鹿 老虎 白虎 天鹅 火烈鸟 海豚 鲸鲨 企鹅 猴子 犀牛
河马 斑马 羊驼 狮子 狼 黑熊 秃鹫 北极熊 白鲸 海牛 海狮 鹦鹉 孔雀 袋鼠 猩猩 豹
梅花鹿 鸵鸟 鳄鱼 乌龟 海龟 鱼 鸟 兽 恐龙""".split())


def candidates_for(cand, poi_set, window=60):
    """候选先行词：代词前 60 字里的名词，加上项目2识别出的 POI。"""
    sent = cand["sentence"]
    left = sent[:max(0, sent.find(cand["pronoun"]))]
    ctx = cand["prev_sent"][-window:] + left

    out = []
    for w, flag in pseg.cut(ctx):
        if flag in NOUN_FLAG and len(w) >= 2 and w not in ("我们", "他们", "你们"):
            out.append((w, w in poi_set))
    for p in poi_set:
        if p in ctx:
            out.append((p, True))
    # jieba 切碎的复合专名（"黑颈""天鹅"）补一次合并，这类词多半只出现一次，
    # 不会进项目2 的词典，但作为候选必须出现，否则只能在碎片里挑
    for w, _ in domain_dict.merge_spans([w for w, _ in out] + [w for w, flag in pseg.cut(ctx)]):
        if w in ctx:
            out.append((w, True))

    # 去重保留最后一次出现，距离近的优先
    seen = {}
    for w, is_poi in out:
        idx = ctx.rfind(w)
        if w not in seen or idx > seen[w][0]:
            seen[w] = (idx, is_poi)
    pron = cand["pronoun"]
    personal = pron in PERSONAL_PRON
    ranked = []
    for w, (idx, is_poi) in seen.items():
        dist = len(ctx) - idx
        bonus = 0
        if personal:
            # 指人的代词：动物名加分，景点不加分
            if w in ANIMALS or any(a in w for a in ANIMALS if len(a) >= 2):
                bonus = 3
        elif is_poi:
            bonus = 3
        score = bonus + 1.0 / (dist + 1)
        ranked.append({"text": w, "is_poi": is_poi, "dist": dist, "score": round(score, 3)})
    # 同分时取名字更完整的那个："天鹅湖缆车"比"天鹅湖"更贴近原句说的东西
    ranked.sort(key=lambda x: (-x["score"], -len(x["text"]), x["dist"]))
    return ranked[:5]


def resolve(cands, gold_map):
    poi_set = load_poi()
    rows = []
    for c in cands:
        if c["id"] not in gold_map:
            continue
        cand_list = candidates_for(c, poi_set)
        pred = cand_list[0]["text"] if cand_list else ""
        g = gold_map[c["id"]]
        rows.append({
            "id": c["id"], "doc_id": c["doc_id"], "pronoun": c["pronoun"],
            "sentence": c["sentence"],
            "candidates": cand_list,
            "pred": pred, "gold": g["antecedent"], "gold_type": g.get("type", ""),
            "no_antecedent": g.get("no_antecedent", False),
            "correct": int(pred == g["antecedent"]),
        })
    return rows


def evaluate(rows):
    # 无明确先行词的样本单独算，不混进准确率
    clear = [r for r in rows if not r["no_antecedent"]]
    none_ = [r for r in rows if r["no_antecedent"]]
    hit = sum(r["correct"] for r in clear)
    none_hit = sum(1 for r in none_ if r["pred"] and False)  # 规则不会主动拒答，先记 0
    acc = hit / max(1, len(clear))
    cov = len(clear) / max(1, len(rows))

    # 消融：去掉"景点优先"的类型加分，只在同一批候选里按距离选最近的那个
    base_hit = 0
    for r in clear:
        if not r["candidates"]:
            continue
        nearest = min(r["candidates"], key=lambda c: c["dist"])["text"]
        base_hit += int(nearest == r["gold"])
    base_acc = base_hit / max(1, len(clear))

    print("\n评估（%d 例人工标注）" % len(rows))
    print("  有明确先行词 %d 例，规则答对 %d 例，准确率 %.2f" % (len(clear), hit, acc))
    forced = sum(1 for r in none_ if r["pred"])
    print("  无明确先行词 %d 例：%d 例被硬指了一个先行词，%d 例恰好留空" % (
        len(none_), forced, len(none_) - forced))
    print("  覆盖率 %.2f（能给出答案的样本占比按有确定的算）" % cov)
    print("  消融：只按距离选最近的，答对 %d 例，准确率 %.2f" % (base_hit, base_acc))
    for r in rows:
        flag = "✓" if r["correct"] else ("？" if r["no_antecedent"] else "✗")
        print("  %s %s %-4s 预测 %-8s 标注 %-8s | %s" % (
            flag, r["id"], r["pronoun"], r["pred"], r["gold"], r["sentence"][:36]))
    return {"total": len(rows), "clear": len(clear), "correct": hit,
            "accuracy": round(acc, 3), "no_antecedent": len(none_),
            "coverage": round(cov, 3),
            "baseline_correct": base_hit, "baseline_accuracy": round(base_acc, 3)}


def draw_eval(rows, stats):
    """左边看核对错怎么分布，右边看错在哪一类。

    两条约束：一是 3 例本来就没有先行词，混进准确率会把"拒答"和"答错"算成一码事，
    所以图上分开画；二是这张图最后要缩到 14 厘米宽印在 A4 上，
    所以画布按 6.2 英寸建，字号 7 起步，不然后面缩完就看不清了。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for name in ["Microsoft YaHei", "SimHei"]:
        try:
            font_manager.findfont(name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [name]
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False

    clear = [r for r in rows if not r["no_antecedent"]]
    none_ = [r for r in rows if r["no_antecedent"]]
    hit = sum(r["correct"] for r in clear)
    miss = len(clear) - hit
    blank = sum(1 for r in none_ if not r["pred"])
    forced = len(none_) - blank

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.3, 2.95), dpi=300,
                                   gridspec_kw={"width_ratios": [1.1, 1]})

    ax1.barh([1], [hit], height=0.44, color="#2F6DB5", label="答对 %d" % hit)
    ax1.barh([1], [miss], left=[hit], height=0.44, color="#C0504D", label="答错 %d" % miss)
    ax1.barh([0], [blank], height=0.44, color="#9AA5B1", label="空着（恰好对）%d" % blank)
    ax1.barh([0], [forced], left=[blank], height=0.44, color="#E8833A",
             label="硬指了一个 %d" % forced)
    for y, left, v in [(1, 0, hit), (1, hit, miss), (0, 0, blank), (0, blank, forced)]:
        if v:
            ax1.text(left + v / 2, y, str(v), ha="center", va="center",
                     color="white", fontsize=8.5)
    ax1.set_yticks([1, 0])
    ax1.set_yticklabels(["有先行词 %d" % len(clear),
                         "无先行词 %d" % len(none_)], fontsize=6.4)
    ax1.set_xlabel("例数", fontsize=6.8)
    ax1.set_xlim(0, len(rows) + 0.6)
    ax1.set_ylim(-0.55, 1.55)
    ax1.set_title("13 例标注的判定结果", fontsize=7.6, pad=4)
    ax1.legend(fontsize=5.8, loc="lower right", frameon=False, ncol=2,
               handlelength=1.0, columnspacing=0.8, borderaxespad=0.2)

    # 右图改成消融：完整规则和"只按距离"各答对几例
    base = stats.get("baseline_correct", 0)
    bars = [("完整规则", hit), ("只按距离", base)]
    for y, (name, v) in zip([1, 0], bars):
        ax2.barh(y, v, height=0.36, color="#2F6DB5" if y == 1 else "#B0B0B0")
        ax2.text(v + 0.15, y, str(v), fontsize=8, va="center")
    ax2.set_yticks([1, 0])
    ax2.set_yticklabels([b[0] for b in bars], fontsize=6.6)
    ax2.set_xlim(0, 12)
    ax2.set_ylim(-0.5, 1.5)
    ax2.set_xlabel("答对例数（共 10 例）", fontsize=6.8)
    ax2.set_title("消融：去掉类型约束之后", fontsize=7.6, pad=4)
    # 具体是哪几条放正文和表4-2 里说，图上只留两个类别，免得小字挤成一团

    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=6.6, length=2.2, pad=5)

    fig.tight_layout(pad=0.4)
    os.makedirs(FIG, exist_ok=True)
    fig.savefig(os.path.join(FIG, "p4_eval.png"))
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="detect", choices=["detect", "eval"])
    args = ap.parse_args()

    if args.mode == "detect":
        detect()
        return

    cands = json.load(open(os.path.join(OUT, "coref_candidates.json"), encoding="utf-8"))
    gold = json.load(open(GOLD, encoding="utf-8"))
    gold_map = {g["id"]: g for g in gold}
    rows = resolve(cands, gold_map)
    stats = evaluate(rows)
    json.dump({"stats": stats, "rows": rows},
              open(os.path.join(OUT, "coref_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    draw_eval(rows, stats)
    print("\n结果写入 coref_result.json，图写到 outputs/figures/p4_eval.png")


if __name__ == "__main__":
    main()
