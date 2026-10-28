# -*- coding: utf-8 -*-
# 项目3：游览路线的层级结构挖掘。
#
# 官方路线不是一种写法，得分开处理：
#   A 第X站（10:00）：小火车游览        —— 带时刻的编号站（广州长隆野生动物世界）
#   B 第一站：长隆欢乐世界               —— 只有编号没有时刻（3天2夜、横琴）
#   C 沟口→原始森林（07:00-08:10 …）    —— 带时刻区间的箭头（九寨沟，最规整）
#   D 北门——小火车——青龙山——…——出园    —— 一串箭头，没有时刻
#   E 【上午】 / 上午——尖叫地带          —— 时段分块，块内再挂步骤
#
# 解析完统一成 step 列表，再按时间归到上午/中午/下午/晚上。
#
#   python src/p3_route.py
#
# 输出：data/processed/routes.json、data/processed/route_steps.csv
#       outputs/figures/p3_*.png

import json
import os
import re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "data", "processed", "documents.json")
OUT = os.path.join(ROOT, "data", "processed")
FIG = os.path.join(ROOT, "outputs", "figures")

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}

# 同一地方的不同叫法。只合明确等价的，像"长海"和"长海观景台"算父子关系，不强行合并。
ALIAS = {
    "白虎山": "百虎山",
    "小火车游览区": "小火车游览",
    "诺日朗": "诺日朗中心站",
    "则渣洼沟": "则查洼沟",
    "天鹅美食": "天鹅湖",
    "4D影院": "四维影院",
    "诺日朗中心": "诺日朗中心站",
}
# 父区域，归一化时保留但标出来，避免把它当成具体景点
PARENT = {"九寨沟", "长隆野生动物世界", "长隆欢乐世界", "长隆海洋王国", "海洋王国",
          "长隆水上乐园", "长隆飞鸟乐园", "长隆飞船乐园", "度假区", "景区", "园区"}

# 中午按 11-13 算，不按 12-14：官方路线里 11:30 是诺日朗吃饭，13:30 已经出发去长海了
PERIODS = {"上午": (6, 11), "中午": (11, 13), "下午": (13, 18), "晚上": (18, 24)}


def period_of(hhmm):
    if not hhmm:
        return ""
    try:
        h = int(hhmm.split(":")[0])
    except ValueError:
        return ""
    for name, (a, b) in PERIODS.items():
        if a <= h < b:
            return name
    return "清晨" if h < 6 else ""


def norm(name):
    name = (name or "").strip(" 　·、，。！？")
    name = re.sub(r"^(乘车|步行|换乘|乘坐|前往|到达|返回|到|去|从|经)", "", name)
    name = re.sub(r"(乘车|步行|换乘|乘坐)$", "", name)
    return ALIAS.get(name, name)


def split_chain(line):
    """把 A→B→C 或 A——B——C 拆成点列表。破折号要连着出现才算分隔，单个的不算。"""
    if line.count("→") >= 1:
        parts = re.split(r"\s*→\s*", line)
    elif line.count("——") >= 2:
        parts = re.split(r"\s*——\s*", line)
    else:
        return []
    return [p.strip() for p in parts if p.strip()]


def parse_official(doc):
    """把一篇官方路线解析成步骤。

    九寨沟那篇把一日、二日、三日游写在同一页里，不切开的话 46 步会串成一条，
    后面算时间层级就没意义了，所以遇到"一日/二日/三日"标题就另起一条。
    """
    routes = []
    steps = []
    fmt_counter = Counter()
    period = ""
    sub = ""
    pending = None          # 上一个"第X站"，用来挂后续描述

    def flush():
        if steps:
            routes.append({"sub": sub, "steps": list(steps), "fmts": dict(fmt_counter)})

    for para in doc["paragraphs"]:
        line = para["text"].strip()
        if not line:
            continue

        if re.match(r"^(一日|二日|三日|四日)", line) and len(line) <= 12:
            flush()
            steps.clear()
            fmt_counter.clear()
            sub = line
            period = ""
            pending = None
            continue

        # 时段标记
        m = re.match(r"^【(上午|中午|下午|晚上)】$", line)
        if m:
            period = m.group(1)
            fmt_counter["period_block"] += 1
            continue
        m = re.match(r"^(上午|中午|下午|傍晚|晚上)\s*[——:：]\s*(.+)$", line)
        if m:
            period = "下午" if m.group(1) == "傍晚" else m.group(1)
            steps.append({
                "step": len(steps) + 1, "period": period,
                "time_start": "", "time_end": "",
                "from": "", "to": norm(m.group(2)), "activity": m.group(2),
                "transport": "", "raw": line, "time_source": "period",
            })
            fmt_counter["period_atom"] += 1
            pending = steps[-1]
            continue

        # 第X站（10:00）：标题
        m = re.match(r"^第([一二三四五六七八九十]{1,3})站\s*[（(]?\s*(\d{1,2}:\d{2})?\s*[）)]?\s*[：:]?\s*(.*)$", line)
        if m:
            no = CN_NUM.get(m.group(1), len(steps) + 1)
            t = m.group(2) or ""
            title = (m.group(3) or "").strip()
            if len(title) > 30:            # 编号后面直接跟正文的，截一下
                title = title[:30]
            p = period_of(t) or period
            steps.append({
                "step": no, "period": p, "time_start": t, "time_end": "",
                "from": "", "to": norm(title), "activity": title,
                "transport": "", "raw": line, "time_source": "clock" if t else "none",
            })
            fmt_counter["station"] += 1
            pending = steps[-1]
            continue

        # 带时刻区间的箭头：A→B（07:00-08:10 一个小时车程）
        if "→" in line and re.search(r"\d{1,2}:\d{2}", line):
            body, _, tail = line.partition("（")
            pts = split_chain(body)
            rng = re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", tail)
            trans = ""
            for w in ["观光车", "小火车", "空中缆车", "缆车", "步行", "换乘"]:
                if w in tail:
                    trans = w
                    break
            for i in range(len(pts) - 1):
                steps.append({
                    "step": len(steps) + 1, "period": period_of(rng.group(1)) if rng else period,
                    "time_start": rng.group(1) if (rng and i == 0) else "",
                    "time_end": rng.group(2) if (rng and i == len(pts) - 2) else "",
                    "from": norm(pts[i]), "to": norm(pts[i + 1]), "activity": "",
                    "transport": trans, "raw": line, "time_source": "range" if rng else "none",
                })
            fmt_counter["arrow_time"] += 1
            pending = steps[-1]
            continue

        # 纯箭头链（没有时刻）
        pts = split_chain(line)
        if len(pts) >= 3:
            for i in range(len(pts) - 1):
                steps.append({
                    "step": len(steps) + 1, "period": period, "time_start": "", "time_end": "",
                    "from": norm(pts[i]), "to": norm(pts[i + 1]), "activity": "",
                    "transport": "", "raw": line, "time_source": "none",
                })
            fmt_counter["arrow_chain"] += 1
            pending = steps[-1]
            continue

        # 其余是描述段：挂到当前步骤的 activity 上，别丢
        if pending and len(line) <= 40 and not steps[-1]["activity"]:
            steps[-1]["activity"] = line
        elif pending and line.startswith(("上午", "中午", "下午")):
            pass

    flush()
    return routes


def tidy(steps):
    """同一篇里常把一条路线写两遍：先用箭头链概述一遍，再用"第X站"逐站展开。

    GZCL-OFF-01 就是这样。两遍都留着会变成 26 步，同一趟路走两次，步骤号还会从 12 跳回 1；
    只留箭头链也不对，链里没时刻，留下的又是链尾那几步，顺序直接乱掉。
    所以这里只保留"第X站"那一遍，它既有顺序也有时间。
    """
    st = [s for s in steps if re.match(r"^第[一二三四五六七八九十]{1,3}站", s["raw"])]
    if len(st) >= 3:
        steps = st
    # 把整句话当景点的、以及链里夹出来的长名字一起清掉
    steps = [s for s in steps if s["to"] and len(s["to"]) <= 12
             and not any(c in s["to"] for c in "！。，")]
    for i, s in enumerate(steps, 1):
        s["step"] = i
    return steps


def build(routes_mode="official"):
    docs = json.load(open(DOCS, encoding="utf-8"))
    out = []
    for d in docs:
        if d["text_type"] != "official_route":
            continue
        for k, r in enumerate(parse_official(d), start=1):
            fmts = r["fmts"]
            # 只有"箭头链 + 逐站"两种写法同时出现时才算重复描述，正常路线不做去重，
            # 否则二日游里第二天重走同一段会被误删
            steps = tidy(r["steps"]) if ("arrow_chain" in fmts and "station" in fmts) else r["steps"]
            if not steps:
                continue
            levels = defaultdict(list)
            for s in steps:
                key = s["period"] or "未标时段"
                levels[key].append(s["step"])
            out.append({
                "route_id": "%s-R%d" % (d["doc_id"], k),
                "doc_id": d["doc_id"],
                "sub_route": r["sub"],
                "scenic_area": d["scenic_area"],
                "scenic_code": d["scenic_code"],
                "title": d["title"],
                "format": "+".join(sorted(fmts.keys())) if fmts else "unknown",
                "step_count": len(steps),
                "time_start": min([s["time_start"] for s in steps if s["time_start"]], default=""),
                "time_end": max([s["time_end"] for s in steps if s["time_end"]], default=""),
                "levels": {k: v for k, v in levels.items()},
                "steps": steps,
            })
    return out


def main():
    routes = build()
    json.dump(routes, open(os.path.join(OUT, "routes.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 步骤扁平表，项目6 建图要用
    import csv
    with open(os.path.join(OUT, "route_steps.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["route_id", "doc_id", "scenic_area", "step", "period",
                    "time_start", "time_end", "from", "to", "activity",
                    "transport", "time_source", "sub_route"])
        for r in routes:
            for s in r["steps"]:
                w.writerow([r["route_id"], r["doc_id"], r["scenic_area"], s["step"],
                            s["period"], s["time_start"], s["time_end"], s["from"],
                            s["to"], s["activity"], s["transport"], s["time_source"],
                            r.get("sub_route", "")])

    print("解析出 %d 条官方路线，共 %d 步" % (
        len(routes), sum(r["step_count"] for r in routes)))
    for r in routes:
        print("  %-20s %-10s %2d 步  时段 %-22s 来源 %s" % (
            r["route_id"].replace("-OFF-", ""), (r["sub_route"] or r["scenic_area"])[:10],
            r["step_count"], ",".join(r["levels"].keys()), r["format"]))

    draw(routes)
    cmp = compare(routes)
    return routes


def seq_of_route(r):
    """按出现顺序抽景点序列，去掉沟口/出园这种起终点。"""
    seq = []
    for st in r["steps"]:
        for x in (st["from"], st["to"]):
            if not x or x in ("沟口", "出沟", "出园", "入园"):
                continue
            if x not in seq:
                seq.append(x)
    return seq


def parse_note_summary(text):
    """游记里的路线总结：上午长海→五彩池→…；下午… 按分号切开再取箭头链。"""
    out = []
    for m in re.finditer(r"(上午|中午|下午|傍晚|晚上)([^；;。]*)", text):
        period, body = m.group(1), m.group(2)
        pts = [x.strip() for x in re.split(r"→", body) if x.strip()]
        for x in pts:
            x = norm(re.sub(r"(吃饭休整|吃饭|休整|换乘|游览|打卡|出发).*$", "", x))
            if x and x not in ("沟口", "出沟", "出园"):
                out.append((period, x))
    return out


def lcs(a, b):
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n):
        for j in range(m):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[n][m]


def compare(routes):
    """官方 vs 游客：九寨沟一日游那一对。指标是覆盖度、顺序一致性和时段分配。"""
    docs = json.load(open(DOCS, encoding="utf-8"))
    off = next((r for r in routes if r["doc_id"] == "JZG-OFF-01" and "一日" in r.get("sub_route", "")), None)
    note = next((d for d in docs if d["doc_id"] == "JZG-NOTE-03"), None)
    if not off or not note:
        return None

    off_seq = seq_of_route(off)
    note_pairs = []
    for para in note["paragraphs"]:
        if "→" in para["text"]:
            note_pairs = parse_note_summary(para["text"])
            if len(note_pairs) >= 4:
                break
    note_seq = []
    for _, x in note_pairs:
        if x not in note_seq:
            note_seq.append(x)

    common = [x for x in off_seq if x in note_seq]
    cover = len(common) / max(1, len(off_seq))
    jac = len(set(off_seq) & set(note_seq)) / max(1, len(set(off_seq) | set(note_seq)))
    l = lcs(off_seq, note_seq)
    order = l / max(1, min(len(off_seq), len(note_seq)))

    def dist(pairs):
        c = Counter([p for p, _ in pairs])
        return dict(c)

    off_dist = Counter([st["period"] for st in off["steps"] if st["period"]])
    note_dist = Counter([p for p, _ in note_pairs])

    # 时段对比要能比：游客那边"傍晚"单列，官方没有这项，先并进下午
    def merge_dusk(d):
        d = dict(d)
        if "傍晚" in d:
            d["下午"] = d.get("下午", 0) + d.pop("傍晚")
        return d

    def share(d):
        tot = sum(d.values()) or 1
        return {k: round(v / tot, 3) for k, v in d.items()}

    # 官方路线两步之间的平均间隔，用来和游客的节奏对比
    times = [st["time_start"] for st in off["steps"] if st["time_start"]]
    span_min, gap_min = 0, 0
    if len(times) >= 2:
        t0 = int(times[0][:2]) * 60 + int(times[0][3:])
        t1 = int(times[-1][:2]) * 60 + int(times[-1][3:])
        span_min = t1 - t0
        gap_min = round(span_min / max(1, len(off["steps"]) - 1))

    note_dist_m = merge_dusk(note_dist)
    result = {
        "official": {"route_id": off["route_id"], "seq": off_seq,
                     "period_dist": dict(off_dist), "period_share": share(dict(off_dist))},
        "note": {"doc_id": note["doc_id"], "seq": note_seq,
                 "pairs": note_pairs, "period_dist": note_dist_m,
                 "period_share": share(note_dist_m)},
        "time_dim": {"span_minutes": span_min, "avg_gap_minutes": gap_min,
                     "note_has_clock": False},
        "metrics": {
            "official_spots": len(off_seq),
            "note_spots": len(note_seq),
            "common": len(common),
            "coverage": round(cover, 3),
            "jaccard": round(jac, 3),
            "lcs": l,
            "order_consistency": round(order, 3),
        },
        "only_official": [x for x in off_seq if x not in note_seq],
        "only_note": [x for x in note_seq if x not in off_seq],
    }
    json.dump(result, open(os.path.join(OUT, "route_compare.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    m = result["metrics"]
    print("\n官方 vs 游客（九寨沟一日游）")
    print("  官方 %d 个景点，游客 %d 个，共有 %d 个" % (
        m["official_spots"], m["note_spots"], m["common"]))
    print("  覆盖度 %.2f，Jaccard %.2f，LCS %d，顺序一致性 %.2f" % (
        m["coverage"], m["jaccard"], m["lcs"], m["order_consistency"]))
    td = result["time_dim"]
    print("  时间分配：官方 %s；游客 %s" % (
        result["official"]["period_share"], result["note"]["period_share"]))
    print("  官方全程 %d 分钟，平均每步间隔 %d 分钟" % (td["span_minutes"], td["avg_gap_minutes"]))
    print("  只有官方走：%s" % "、".join(result["only_official"][:8]))
    print("  只有游客走：%s" % "、".join(result["only_note"][:8]))

    draw_compare(result)
    return result


def draw_compare(res):
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

    fig, ax = plt.subplots(figsize=(12, 5.2), dpi=200)
    for y, (label, seq) in enumerate([("官方一日游", res["official"]["seq"]),
                                      ("游客实际", res["note"]["seq"])]):
        xs = list(range(len(seq)))
        ax.plot(xs, [y] * len(xs), "-", color="#B0B0B0", lw=1.2, zorder=1)
        ax.scatter(xs, [y] * len(xs), s=46, zorder=2,
                   color="#2F6DB5" if y == 0 else "#E8833A")
        for x, name in zip(xs, seq):
            ax.annotate(name, (x, y), textcoords="offset points",
                        xytext=(0, 12 if y == 0 else -16),
                        ha="center", fontsize=7, rotation=40)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["官方一日游", "游客实际"])
    ax.set_xlabel("顺序（第几个景点）", labelpad=8)
    ax.set_ylim(-0.8, 1.8)
    ax.spines[["right", "top", "left"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "p3_compare.png"))
    plt.close(fig)


def draw(routes):
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

    # 每篇的时段分布
    labels = [r["route_id"].replace("-OFF-", "") for r in routes]
    order = ["上午", "中午", "下午", "晚上", "未标时段"]
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=200)
    bottom = [0] * len(routes)
    colors = {"上午": "#4C72B0", "中午": "#DD8452", "下午": "#55A868",
              "晚上": "#8172B3", "未标时段": "#C4C4C4"}
    for k in order:
        vals = [len(r["levels"].get(k, [])) for r in routes]
        if not any(vals):
            continue
        ax.bar(labels, vals, bottom=bottom, label=k, color=colors[k])
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_ylabel("步骤数")
    ax.legend(ncol=5, fontsize=8)
    ax.set_title("")
    plt.xticks(rotation=20, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "p3_levels.png"))
    plt.close(fig)


if __name__ == "__main__":
    main()
