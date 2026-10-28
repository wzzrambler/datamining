# -*- coding: utf-8 -*-
# 项目2：游览步骤的实体识别。
# 三类实体：景点 POI、交通方式、时间节点。
#
# 做法是 jieba 分词 + 自定义词典 + 正则，没用 LLM。
# 词典按三个景区分别整理，识别时合并使用；时间靠正则，因为时刻格式比较固定。
#
#   python src/p2_entities.py --version v1    先跑一版，看噪声
#   python src/p2_entities.py --version v2    加停用词后重跑
#
# 输出：data/processed/entities_v1.json / entities_v2.json
#       outputs/figures/p2_freq_v1.png / p2_freq_v2.png

import argparse
import json
import os
import re
from collections import Counter, defaultdict

import jieba

import domain_dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "data", "processed", "documents.json")
OUT = os.path.join(ROOT, "data", "processed")
FIG = os.path.join(ROOT, "outputs", "figures")

# 词典
# 景点。只放"能被当成一个地点去走、去看的东西"，
# 园区、景区、酒店这类泛称不算，它们在第一版里会跑出来，靠停用词压掉。
POI = {
    "GZCL": [
        "长隆野生动物世界", "长隆欢乐世界", "长隆水上乐园", "长隆飞鸟乐园",
        "长隆国际大马戏", "野生动物世界", "欢乐世界", "水上乐园", "飞鸟乐园",
        "小火车游览区", "青龙山", "侏罗纪森林", "雨林仙踪", "金蛇秘境", "百虎山",
        "白虎山", "考拉园", "熊猫乐园", "熊猫村", "熊猫餐厅", "亚洲象园",
        "长颈鹿广场", "空中缆车", "火烈鸟岛", "天鹅湖", "天鹅美食", "花果山",
        "金猴王国", "儿童动物园", "南门", "北门", "纪念品商场", "4D影院",
        "大熊猫中心", "考拉馆", "长隆酒店", "熊猫酒店", "香江酒店",
    ],
    "ZHCL": [
        "长隆海洋王国", "海洋王国", "鲸鲨馆", "企鹅馆", "海豚馆", "白鲸馆",
        "海牛馆", "北极熊馆", "海底世界", "海洋大街", "横琴海", "中心湖",
        "雨林飞翔", "冰山过山车", "鹦鹉过山车", "海象山", "英雄岛", "极地探险",
        "海豚湾", "横琴湾酒店", "企鹅酒店", "马戏酒店", "飞船酒店",
        "长隆剧院", "卡卡剧场", "长隆飞船乐园", "飞船乐园",
    ],
    "JZG": [
        "九寨沟", "长海", "五彩池", "五花海", "镜海", "诺日朗瀑布", "诺日朗群海",
        "诺日朗中心站", "诺日朗", "珍珠滩", "珍珠滩瀑布", "熊猫海", "熊猫海瀑布",
        "箭竹海", "箭竹海瀑布", "芳草海", "天鹅海", "原始森林", "树正沟", "日则沟",
        "则查洼沟", "则渣洼沟", "树正瀑布", "树正群海", "树正寨", "犀牛海", "老虎海",
        "卧龙海", "双龙海", "火花海", "芦苇海", "盆景滩", "季节海", "上季节海",
        "下季节海", "扎如沟", "沟口", "神仙池", "长海观景台", "老虎嘴",
    ],
}
# 三个景区都通用的地点词
POI_COMMON = ["景区", "园区", "度假区", "游客中心", "停车场", "检票口", "入口",
              "餐厅", "酒店", "商店", "观景台", "栈道", "索道站"]


def _base_terms():
    """手写词典里的全部景点名，给 domain_dict 判断"景点+设施"用。

    这块早先是读上一轮的 entities_v2.json，等于让输出反过来喂输入，
    每跑一次合成词就在 38、39 之间跳，报告里的数也跟着变。
    改成从字面量直接取，同样的正文永远得到同样的补充词。
    """
    out = set(POI_COMMON)
    for terms in POI.values():
        out.update(terms)
    return out

TRANSPORT = [
    "地铁", "地铁站", "公交", "公交车", "巴士", "大巴", "穿梭巴", "免费巴士",
    "观光车", "环保车", "小火车", "缆车", "空中缆车", "索道", "步行", "徒步",
    "打车", "出租车", "自驾", "高铁", "动车", "火车", "飞机", "航班",
    "轮渡", "游船", "摆渡车", "电瓶车", "接驳车", "电梯", "扶梯", "专线",
]

# 第二版加的停用词。这些在第一版里都排到了前面，但它们不是"要去的地方"，
# 是园区泛称、动作或修饰词，混在词频表里会把真正的景点挤下去。
STOP_POI = {
    "景区", "园区", "度假区", "乐园", "主题公园", "动物园", "森林", "丛林",
    "雨林", "冰山", "深海", "沙滩", "大桥", "山林", "热带雨林", "游览区",
    "展馆", "场馆", "食街", "大街", "广场", "酒店", "餐厅", "商店",
    "入园", "出园", "开园", "闭园", "进园", "出沟", "中心站",
    "第一站", "第二站", "第三站", "第四站", "第五站", "一站", "站",
    "珠海", "澳门", "广州", "九寨", "四川", "成都",
    "幼儿园", "象山", "花果山",
}
# 后缀兜底：词典没收录但有地点后缀的，也算 POI（第一版开，第二版仍开但过滤停用词）
SUFFIX = ["园", "馆", "海", "沟", "山", "湖", "塔", "桥", "寺", "宫", "殿",
          "瀑布", "寨", "街", "镇", "湾", "岛", "峰", "池", "滩", "洞", "楼",
          "广场", "餐厅", "酒店", "观景台", "栈道"]

STOP_TRANSPORT = {"可以", "需要", "建议", "然后", "直接"}

# 时间正则
TIME_PATTERNS = [
    (r"\d{1,2}:\d{2}", "clock"),                       # 10:00
    (r"\d{1,2}\s*点\s*\d{1,2}?\s*分?", "clock_cn"),     # 10点 / 10点30分
    (r"\d{1,3}\s*个?\s*(?:分钟|小时)", "duration"),      # 30分钟 / 2个小时
    (r"(?:上午|中午|下午|傍晚|晚上|早上|清晨|凌晨|夜里|白天)", "period"),
    (r"第[一二三四五六七八九十\d]{1,3}(?:天|日|站)", "ordinal"),
    (r"Day\s*\d{1,2}", "day_en"),
    (r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]", "date"),
]


def load_docs():
    return json.load(open(DOCS, encoding="utf-8"))


def build_dict(version, extra=()):
    """按景区建词典。v1 只加手写词典，v2 再补停用词和自动发现的合成词。"""
    for code, terms in POI.items():
        for t in terms:
            jieba.add_word(t, freq=1000)
    for t in POI_COMMON + TRANSPORT:
        jieba.add_word(t, freq=1000)
    if version == "v2":
        for t in STOP_POI:
            jieba.del_word(t)
        for t in extra:
            jieba.add_word(t, freq=1000)


def find_terms(text, terms):
    """长词优先匹配，已经占用的位置跳过，避免"长海"吃掉"长海观景台"。"""
    terms = sorted(terms, key=len, reverse=True)
    taken = [False] * len(text)
    hits = []
    for t in terms:
        start = 0
        while True:
            i = text.find(t, start)
            if i < 0:
                break
            if not any(taken[i:i + len(t)]):
                hits.append((i, i + len(t), t))
                for k in range(i, i + len(t)):
                    taken[k] = True
            start = i + 1
    return hits


def suffix_candidates(para):
    """jieba 切出来的、以地点后缀结尾的词，作为词典外补充。"""
    out = []
    pos = 0
    for w in jieba.cut(para):
        i = para.find(w, pos)
        if i < 0:
            continue
        pos = i + len(w)
        if len(w) < 2 or len(w) > 8:
            continue
        if not re.fullmatch(r"[\u4e00-\u9fa5]+", w):
            continue
        if any(w.endswith(s) for s in SUFFIX):
            out.append((i, i + len(w), w))
    return out


def extract_doc(doc, version, extra=()):
    area = doc["scenic_code"]
    poi_terms = POI.get(area, []) + POI_COMMON + list(extra)
    ents = []
    for para in doc["paragraphs"]:
        text = para["text"]
        base = para["start"]

        # POI：词典 + 后缀兜底。v1 不过滤，v2 用停用词压掉泛称
        cand = find_terms(text, poi_terms)
        occupied = [(s, e) for s, e, _ in cand]
        for s, e, w in suffix_candidates(text):
            if version == "v2" and w in STOP_POI:
                continue
            if any(not (e <= s2 or s >= e2) for s2, e2 in occupied):
                continue
            cand.append((s, e, w))
            occupied.append((s, e))

        for s, e, w in cand:
            if version == "v2" and w in STOP_POI:
                continue
            ents.append({"type": "POI", "text": w, "para_id": para["i"],
                         "start": base + s, "end": base + e})

        # 交通：词典
        for s, e, w in find_terms(text, TRANSPORT):
            if w in STOP_TRANSPORT:
                continue
            ents.append({"type": "TRANSPORT", "text": w, "para_id": para["i"],
                         "start": base + s, "end": base + e})

        # 时间：正则
        for pat, kind in TIME_PATTERNS:
            for m in re.finditer(pat, text):
                ents.append({"type": "TIME", "text": m.group(0),
                             "para_id": para["i"], "start": base + m.start(),
                             "end": base + m.end(), "subtype": kind})

    ents.sort(key=lambda x: x["start"])
    return {
        "doc_id": doc["doc_id"],
        "scenic_area": doc["scenic_area"],
        "text_type": doc["text_type"],
        "entity_count": len(ents),
        "entities": ents,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v1", choices=["v1", "v2"])
    args = ap.parse_args()

    docs = load_docs()
    # 合成词只在 v2 用：v1 是迭代前的基线，改动都算在 v2 头上，对比才清楚
    extra = list(domain_dict.discover(docs, known=_base_terms())) if args.version == "v2" else []
    build_dict(args.version, extra)

    result = [extract_doc(d, args.version, extra) for d in docs]
    path = os.path.join(OUT, "entities_%s.json" % args.version)
    json.dump(result, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 统计
    by_type = defaultdict(Counter)
    for r in result:
        for e in r["entities"]:
            by_type[e["type"]][e["text"]] += 1

    total = sum(len(r["entities"]) for r in result)
    print("版本 %s：%d 篇，实体 %d 个（合成词补充 %d 个）" % (
        args.version, len(result), total, len(extra)))
    for t, c in by_type.items():
        print("  %-10s 不同实体 %3d 个，出现 %4d 次" % (t, len(c), sum(c.values())))
        print("   ", "，".join("%s(%d)" % (k, v) for k, v in c.most_common(12)))

    draw(by_type, args.version)
    if args.version == "v2":
        draw_cloud(by_type)
    return result


def draw(by_type, version):
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

    titles = {"POI": "景点 POI", "TRANSPORT": "交通方式", "TIME": "时间节点"}
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), dpi=200)
    for ax, t in zip(axes, ["POI", "TRANSPORT", "TIME"]):
        items = by_type[t].most_common(15)
        if not items:
            continue
        names = [i[0] for i in items][::-1]
        vals = [i[1] for i in items][::-1]
        ax.barh(names, vals, color={"POI": "#2F6DB5", "TRANSPORT": "#E8833A", "TIME": "#5B8C5A"}[t])
        ax.set_title(titles[t], fontsize=11)
        for i, v in enumerate(vals):
            ax.text(v + 0.2, i, str(v), va="center", fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "p2_freq_%s.png" % version))
    plt.close(fig)


def draw_cloud(by_type):
    """三类实体各一张词云。柱状图看排名，词云看整体形状，两种一起用。"""
    from wordcloud import WordCloud
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    try:
        font_manager.findfont("Microsoft YaHei", fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
    except Exception:
        pass

    font = "C:/Windows/Fonts/msyh.ttc"
    titles = {"POI": "景点 POI（%d 个）", "TRANSPORT": "交通方式（%d 个）", "TIME": "时间节点（%d 个）"}
    fig, axes = plt.subplots(1, 3, figsize=(6.9, 2.4), dpi=300)
    for ax, t in zip(axes, ["POI", "TRANSPORT", "TIME"]):
        freq = dict(by_type[t])
        if t == "TIME":
            # 具体钟点几十个，字号全被它们占了，时段和时长就看不见了
            freq = {k: v for k, v in freq.items() if not re.fullmatch(r"\d{1,2}:\d{2}", k)}
        wc = WordCloud(font_path=font, width=520, height=360, background_color="white",
                       max_words=80, prefer_horizontal=0.92,
                       colormap={"POI": "Blues", "TRANSPORT": "Oranges", "TIME": "Greens"}[t],
                       relative_scaling=0.45).generate_from_frequencies(freq)
        ax.imshow(wc, interpolation="bilinear")
        ax.set_title(titles[t] % len(freq), fontsize=7.4, pad=3)
        ax.axis("off")
    fig.tight_layout(pad=0.3)
    fig.savefig(os.path.join(FIG, "p2_cloud_v2.png"))
    plt.close(fig)


if __name__ == "__main__":
    main()
