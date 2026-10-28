# -*- coding: utf-8 -*-
# 项目6：多源程序性知识的融合与图谱可视化。
#
# 输入三份：项目3 的路线步骤、项目2 的实体、项目5 的条件建议。
# 节点分四类：景区、景点、时间节点、操作（吃饭/换乘这类）。
# 边分三种：NEXT（下一步去哪）、BELONG（景点属于哪个景区）、AT（这一步发生在哪个时段）。
#
#   python src/p6_graph.py
#
# 输出：data/processed/graph_nodes.csv、graph_edges.csv、graph.json
#       outputs/figures/p6_graph.png

import csv
import json
import os
from collections import Counter, defaultdict

import networkx as nx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "processed")
FIG = os.path.join(ROOT, "outputs", "figures")

ACTION_WORDS = ["吃饭", "用餐", "午餐", "休息", "换乘", "购物", "表演", "巡游", "烟花"]


def load(name):
    p = os.path.join(OUT, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def build(route_ids, area_name):
    routes = load("routes.json")
    ents = load("entities_v2.json")
    conds = load("conditions.json")

    G = nx.MultiDiGraph()
    G.add_node(area_name, ntype="景区", label=area_name)

    # 只取指定路线，避免把一日游和二日游混成一张网
    picked = [r for r in routes if r["route_id"] in route_ids]
    if not picked:
        return G

    for r in picked:
        prev = None
        for st in r["steps"]:
            poi = st["to"]
            if not poi:
                continue
            kind = "操作" if any(w in poi for w in ACTION_WORDS) else "景点"
            if poi not in G:
                G.add_node(poi, ntype=kind, label=poi, area=area_name)
            G.add_edge(area_name, poi, key="belong", rel="BELONG")

            if st["period"]:
                tnode = "%s时段" % st["period"]
                if tnode not in G:
                    G.add_node(tnode, ntype="时间", label=st["period"])
                G.add_edge(poi, tnode, key="at", rel="AT", time_start=st["time_start"])

            if prev and prev != poi:
                G.add_edge(prev, poi, key="next", rel="NEXT",
                           transport=st["transport"], time=st["time_start"],
                           route=r["route_id"])
            prev = poi

    # 条件建议挂到景点上，作为属性而不是新节点，免得图被撑散
    for c in conds:
        for n in list(G.nodes):
            if n in c["advice"] and G.nodes[n].get("ntype") == "景点":
                G.nodes[n].setdefault("tips", []).append(c["advice"][:40])
    return G


def export(G, tag):
    nodes = [{"id": n, "type": d.get("ntype", ""), "label": d.get("label", n),
              "area": d.get("area", ""), "tips": len(d.get("tips", []))}
             for n, d in G.nodes(data=True)]
    edges = []
    for u, v, k, d in G.edges(keys=True, data=True):
        edges.append({"source": u, "target": v, "rel": d.get("rel", ""),
                      "transport": d.get("transport", ""), "time": d.get("time", "")})

    with open(os.path.join(OUT, "graph_nodes.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "type", "label", "area", "tips"])
        w.writeheader()
        w.writerows(nodes)
    with open(os.path.join(OUT, "graph_edges.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["source", "target", "rel", "transport", "time"])
        w.writeheader()
        w.writerows(edges)

    data = nx.readwrite.json_graph.node_link_data(G, edges="links")
    json.dump(data, open(os.path.join(OUT, "graph.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[%s] 节点 %d，边 %d" % (tag, len(nodes), len(edges)))
    print("  节点类型：", dict(Counter(n["type"] for n in nodes)))
    print("  边类型：", dict(Counter(e["rel"] for e in edges)))


def draw(G, title, fname, route_id):
    """按"时段泳道 + 步骤顺序"画，别用弹簧布局。

    spring 布局对一条链来说纯属自找麻烦，几条线交叉在一起谁也看不清。
    官方路线本来就有时间顺序，横轴放步骤、纵向分上午/中午/下午，路径一眼能读出来。
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

    routes = load("routes.json")
    r = next((x for x in routes if x["route_id"] == route_id), None)
    if not r:
        return
    steps = [s for s in r["steps"] if s["to"]]

    used = [s["period"] or "未标时段" for s in steps]
    lanes = [x for x in ["上午", "中午", "下午", "未标时段"] if x in used]
    ymap = {k: i for i, k in enumerate(lanes)}

    fig, ax = plt.subplots(figsize=(13, 1.9 * max(2, len(lanes)) + 1.2), dpi=200)
    for k, lane in enumerate(lanes):
        ax.axhspan(k - 0.42, k + 0.42, color="#F4F6F9" if k % 2 == 0 else "#FFFFFF", zorder=0)
        ax.text(-0.9, k, lane, ha="right", va="center", fontsize=9, color="#555555")

    xs, ys = [], []
    for i, st in enumerate(steps):
        y = ymap.get(st["period"] or "未标时段", len(lanes) - 1)
        xs.append(i)
        ys.append(y)

    ax.plot(xs, ys, "-", color="#C9D3E0", lw=1.4, zorder=1)
    for i, st in enumerate(steps):
        y = ys[i]
        color = "#E8833A" if any(w in st["to"] for w in ACTION_WORDS) else "#2F6DB5"
        ax.scatter(i, y, s=120, color=color, zorder=3, edgecolor="white", linewidth=0.8)
        ax.annotate(st["to"], (i, y), textcoords="offset points",
                    xytext=(0, 13 if i % 2 == 0 else -18), ha="center",
                    fontsize=7.2, rotation=38, color="#222222")
        if st["time_start"]:
            ax.annotate(st["time_start"], (i, y), textcoords="offset points",
                        xytext=(0, -6), ha="center", fontsize=5.6, color="#777777")

    ax.set_xlim(-1.6, len(steps) - 0.2)
    ax.set_ylim(-0.7, len(lanes) - 0.3)
    ax.set_yticks([])
    ax.set_xticks([])
    ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
    ax.set_title(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, fname))
    plt.close(fig)


def check(G):
    """图谱质量：连通性、孤立点、最长路径。只统计 NEXT 边组成的子图。"""
    H = nx.DiGraph()
    for u, v, d in G.edges(data=True):
        if d.get("rel") == "NEXT":
            H.add_edge(u, v)
    weak = list(nx.weakly_connected_components(H))
    iso = [n for n in nx.nodes_with_selfloops(H)]
    deg = sorted(H.degree, key=lambda x: -x[1])[:5]
    print("  连通分量 %d 个，最大分量 %d 个点" % (len(weak), max((len(c) for c in weak), default=0)))
    print("  度数最高的点：", "，".join("%s(%d)" % (n, d) for n, d in deg))
    try:
        print("  最长路径长度：%d 步" % nx.dag_longest_path_length(H))
    except Exception:
        print("  最长路径：图里有环，跳过")
    return {"components": len(weak),
            "max_component": max((len(c) for c in weak), default=0),
            "top_degree": [(n, d) for n, d in deg]}


if __name__ == "__main__":
    # 选九寨沟一日游：步骤最完整、时间信息最密
    G = build({"JZG-OFF-01-R1"}, "九寨沟风景名胜区")
    export(G, "九寨沟一日游")
    draw(G, "九寨沟一日游知识图谱：横轴为游览步骤，纵向按上午／中午／下午分层（蓝＝景点，橙＝操作）",
         "p6_graph.png", "JZG-OFF-01-R1")
    print("\n图谱检查：")
    check(G)
