# -*- coding: utf-8 -*-
# 项目5：条件性游览建议的抽取。
#
# 文本里的条件句大概三种：
#   1 如果带老人，建议走南门          有"如果"，逗号前后就是条件和建议
#   2 下雨天记得带伞                  没有"如果"，靠"记得/最好/别"这类词切
#   3 建议提前买票                    只有建议，没有条件，条件记为"通用"
#
# 规则按优先级匹配，避免一句话被切好几次。
#
#   python src/p5_conditions.py
#
# 输出：data/processed/conditions.json、conditions.csv
#       outputs/figures/p5_types.png

import csv
import json
import os
import re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "data", "processed", "documents.json")
OUT = os.path.join(ROOT, "data", "processed")
FIG = os.path.join(ROOT, "outputs", "figures")

# 条件触发词，长的放前面，先匹配"如果"再匹配"若"
COND = ["如果", "假如", "若是", "要是", "万一", "若"]
# 建议触发词
ADV = ["建议", "最好", "记得", "千万别", "别忘", "不要", "别", "推荐", "一定要", "可以", "记得要"]

# 分类用的关键词
TYPES = {
    "人群-老人": ["老人", "长辈", "父母", "爸妈", "爷爷", "奶奶", "老年"],
    "人群-儿童": ["孩子", "小孩", "宝宝", "小朋友", "亲子", "带娃", "儿童", "婴儿"],
    "天气": ["下雨", "雨天", "晴", "晒", "台风", "冷", "热", "温差", "天气", "防晒"],
    "时间": ["早上", "上午", "中午", "下午", "傍晚", "晚上", "旺季", "淡季", "节假日", "周末", "提前"],
    "体力": ["体力", "累", "休息", "走不动", "爬山", "徒步", "排队"],
    "装备": ["伞", "水", "防晒", "驱蚊", "充电宝", "鞋", "外套", "零食", "现金"],
    "交通": ["地铁", "巴士", "停车", "车位", "自驾", "打车", "高铁", "接驳", "北门", "南门"],
    "票务": ["门票", "预约", "购票", "身份证", "取票", "年卡", "票价", "块钱", "价格", "便宜", "贵"],
    "餐饮": ["餐厅", "吃饭", "用餐", "午饭", "自带", "餐", "食物", "零食", "吃的", "干粮"],
}
BAD_TAIL = ("吗", "呢", "吧", "？", "?")


def split_sents(text):
    return [s.strip() for s in re.split(r"(?<=[。！？；])", text) if s.strip()]


def classify(text):
    for name, kws in TYPES.items():
        if any(k in text for k in kws):
            return name
    return "其他"


# "别"在"别的/特别/区别"里不是祈使；"可以"在"不可以/没可以"里已经被否掉。
# 第一版这两处没管，结果把"不允许带别的食物"切成了"不"＋"可以带别的食物"，意思正好反了。
NOT_ADV = ["别的", "别人", "特别", "区别", "分别", "告别", "个别", "类别",
           "级别", "性别", "道别"]
NEG_BEFORE = "不没就都也还才"


def cut_one(sent):
    """把一句话切成（条件, 建议）。切不动就返回 None。"""
    # 1 有"如果/假如/若"，以第一个逗号分界
    for w in COND:
        i = sent.find(w)
        if i < 0:
            continue
        m = re.search(r"[，,]", sent[i:])
        if not m:
            continue
        cond = sent[i:i + m.end()].strip("，, ")
        rest = sent[i + m.end():].strip("，, ")
        if len(cond) >= 3 and len(rest) >= 4:
            return trim_cond(cond), rest, "if_pattern"

    # 2 没有"如果"，用建议词切前后
    for w in ADV:
        for i in [m.start() for m in re.finditer(w, sent)]:
            if w in ("别", "千万别"):
                prev = sent[i - 1] if i > 0 else ""
                nxt = sent[i + 1:i + 3]
                # "特别好看"的"别"、"别的食物"的"别"都不是祈使
                if prev in "特分区级类性个告道" or nxt.startswith(("的", "人")):
                    continue
            if w == "可以" and i > 0 and sent[i - 1] in NEG_BEFORE:
                continue
            head = sent[:i].strip("，, ")
            tail = sent[i:].strip("，, ")
            if len(head) > 25:
                head = head[-25:]
            if len(head) >= 4 and len(tail) >= 5 and not head.endswith(BAD_TAIL):
                return trim_cond(head), tail, "adv_pattern"
            if len(tail) >= 6:
                return "", tail, "adv_only"
    return None


BAD_ADVICE = ["公众号", "二维码", "关注", "留言", "转发", "加群",
              "点赞", "收藏", "在看", "阅读原文", "长按"]

# 条件两端的残留：切分点在句中时，尾巴上常挂着半截话
# （"这样我们不""在这里"），头上常挂着"从/在/当"。逐层剥掉。
TRIM_HEAD = ("从", "在", "当", "对于", "关于", "如果", "假如", "要是", "因为")
TRIM_TAIL = ("这样我们不", "这样我们", "在这里", "在那里", "在这里我", "的话",
             "但是", "但", "所以", "因为", "然后", "而且", "并且", "而",
             "我们", "你们", "他们", "我", "你", "他", "就", "才", "也", "还",
             "不仅", "不只", "不但", "还会", "还能", "会", "能", "要", "想",
             "的时候", "时", "之后", "之前", "以后", "的话", "来", "去")


def trim_cond(cond):
    """把条件两端的连词、代词、方位残留剥掉，剥完太短就返回原值。"""
    if not cond:
        return cond
    old = None
    while old != cond:
        old = cond
        for h in TRIM_HEAD:
            if cond.startswith(h) and len(cond) - len(h) >= 3:
                cond = cond[len(h):]
                break
        for t in TRIM_TAIL:
            if cond.endswith(t) and len(cond) - len(t) >= 3:
                cond = cond[:-len(t)]
                break
    return cond.strip("，, ")


def norm_text(t):
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[，。！？；、：]", "", t)
    return t


def dedup(items):
    """先去完全相同，再去近重复（去掉标点后前 20 字相同就算重复）。"""
    seen = set()
    out = []
    for it in items:
        k = norm_text(it["advice"])[:24]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def main():
    docs = json.load(open(DOCS, encoding="utf-8"))
    items = []
    n = 0
    for d in docs:
        for para in d["paragraphs"]:
            for sent in split_sents(para["text"]):
                if len(sent) < 8:
                    continue
                r = cut_one(sent)
                if not r:
                    continue
                cond, adv, how = r
                if len(adv) < 5:
                    continue
                # "别有一番韵味""别处很难见到"这类是在写景，不是建议
                if re.match(r"^(别有一番|别处|别人|别的|另外)", adv):
                    continue
                # 公众号文末的互动引导，不是游览建议
                if any(w in adv for w in BAD_ADVICE):
                    continue
                n += 1
                items.append({
                    "id": "K%03d" % n,
                    "doc_id": d["doc_id"],
                    "scenic_area": d["scenic_area"],
                    "text_type": d["text_type"],
                    "condition": cond,
                    "advice": adv[:80],
                    "rule": how,
                    "cond_type": classify(cond) if cond else "通用",
                    "sentence": sent[:120],
                })

    before = len(items)
    items = dedup(items)
    for i, it in enumerate(items, 1):
        it["id"] = "K%03d" % i

    json.dump(items, open(os.path.join(OUT, "conditions.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "conditions.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "doc_id", "scenic_area", "text_type",
                                          "condition", "advice", "rule", "cond_type"])
        w.writeheader()
        for it in items:
            w.writerow({k: it[k] for k in w.fieldnames})

    ct = Counter(it["cond_type"] for it in items)
    rt = Counter(it["rule"] for it in items)
    print("抽出条件-建议对 %d 条（去重前 %d，去掉 %d）" % (len(items), before, before - len(items)))
    print("规则命中：", dict(rt))
    print("条件类型：", dict(ct))
    for it in items[:12]:
        print("  [%s] %-6s 条件「%s」→ 建议「%s」" % (
            it["id"], it["cond_type"], it["condition"][:16], it["advice"][:34]))

    draw(items)
    return items


def draw(items):
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

    ct = Counter(it["cond_type"] for it in items)
    names = [k for k, _ in ct.most_common()]
    vals = [ct[k] for k in names]
    fig, ax = plt.subplots(figsize=(8.4, 4.2), dpi=200)
    bars = ax.bar(names, vals, color="#2F6DB5")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.15, str(v), ha="center", fontsize=9)
    ax.set_ylabel("条数")
    plt.xticks(rotation=25, ha="right", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "p5_types.png"))
    plt.close(fig)


if __name__ == "__main__":
    main()
