"""Generate supplementary scenic HTML views without changing reports or data."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT6 = ROOT / "outputs/P6"
SCENES = {"GZCL": "广州长隆", "JZG": "九寨沟", "ZHCL": "珠海长隆"}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_html_view(view, path, colors, edge_colors, scenic_name):
    payload = json.dumps({"view": view, "colors": colors, "edgeColors": edge_colors}, ensure_ascii=False).replace("<", "\\u003c")
    template = (ROOT / "tools/P6/network_view.html").read_text("utf-8")
    poi_count = sum(n["label"] == "POI" for n in view["nodes"])
    document = template.replace("__SCENE__", scenic_name).replace("__POI_COUNT__", str(poi_count))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.replace("__DATA__", payload), encoding="utf-8")


def build_view(graph, scene):
    nodes = {n["id"]: n for n in graph["nodes"]}
    selected = {n["id"] for n in graph["nodes"]
                if n["label"] in {"POI", "ROUTE", "TRANSPORT_LEG"}
                and n["properties"].get("scenic_code") == scene}
    links = []
    steps, visits = defaultdict(set), defaultdict(set)
    for edge in graph["edges"]:
        kind, source, target = edge["type"], edge["source"], edge["target"]
        if kind == "HAS_STEP":
            steps[source].add(target)
        elif kind == "VISITS":
            visits[source].add(target)
        elif kind in {"FROM", "TO", "USES_MODE", "USES_LEG", "PART_OF"} and source in selected:
            if target in selected or nodes[target]["label"] == "TRANSPORT":
                selected.add(target)
                links.append({"source": source, "target": target, "type": kind})
    for rid, step_ids in steps.items():
        if rid in selected:
            for poi in sorted({p for sid in step_ids for p in visits[sid]} & selected):
                links.append({"source": rid, "target": poi, "type": "ROUTE_VISITS_PROJECTED"})
    positions = {}
    for kind, radius in {"POI": 1.05, "TRANSPORT_LEG": .78, "ROUTE": .51, "TRANSPORT": .22}.items():
        group = sorted(n for n in selected if nodes[n]["label"] == kind)
        for i, nid in enumerate(group):
            angle = 2 * math.pi * i / len(group) + math.pi / 2
            positions[nid] = (radius * math.cos(angle), radius * math.sin(angle))
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in graph["edges"]:
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)
    # Embed the related records for offline inspection, without adding them to
    # the visible graph or changing the production graph.
    details = {}
    for nid in sorted(selected):
        attached = {nid}
        for edge in outgoing[nid] + incoming[nid]:
            attached.update((edge["source"], edge["target"]))
        for sid in steps.get(nid, []):
            attached.add(sid)
            attached.update(e["target"] for e in outgoing[sid])
        for aid in list(attached):
            attached.update(e["target"] for e in outgoing[aid] if e["type"] == "SUPPORTED_BY")
        # Generic modes are shared across scenes; related records must remain
        # scoped to the scenic view currently being inspected.
        attached = {aid for aid in attached if nodes[aid]["properties"].get("scenic_code") in (None, scene)}
        related_edges = [e for aid in attached for e in outgoing[aid]
                         if e["target"] in attached]
        details[nid] = {"records": [nodes[aid] for aid in sorted(attached)], "edges": related_edges}
    view = {"nodes": [{"id": n, "label": nodes[n]["label"],
                       "name": nodes[n]["properties"].get("name", n),
                       "x": positions[n][0], "y": positions[n][1]} for n in sorted(selected)],
            "edges": links, "details": details,
            "view_definition": f"P6清理后{scene}主体网络；线路→地点为步骤投影；线路是检索候选，不保证完整全天行程"}
    assert all(e["source"] in selected and e["target"] in selected for e in links)
    expected = {n["id"] for n in graph["nodes"] if n["label"] == "POI" and n["properties"].get("scenic_code") == scene}
    assert {n["id"] for n in view["nodes"] if n["label"] == "POI"} == expected
    return view


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenes", nargs="+", choices=SCENES, default=["GZCL", "JZG", "ZHCL"])
    parser.add_argument("--graph", type=Path, default=ROOT / "processed/P6知识图谱/cleaned-v1/graph.json")
    parser.add_argument("--view-json-dir", type=Path, help="可选：将HTML内嵌的调试视图另存到指定目录；默认不生成重复JSON")
    args = parser.parse_args()
    graph_path = args.graph
    graph = json.loads(graph_path.read_text("utf-8"))
    if not graph.get("p6_cleaning"):
        parser.error("展示入口只接收P6清理后图谱，不能回退到P5原始线路。")
    docs = {d["doc_id"]: d for d in json.loads((ROOT / "processed/P1爬虫数据清洗/documents.json").read_text("utf-8"))}
    for node in graph["nodes"]:
        if node["label"] == "OBSERVATION":
            props = node["properties"]
            provenance = props.get("provenance", {})
            doc = docs.get(provenance.get("doc_id"), {})
            props["display_source"] = {"title": doc.get("title"), "excerpts": [
                doc.get("content", "")[b["start"]:b["end"]]
                for b in provenance.get("blocks", []) if "start" in b and "end" in b]}
    colors = {"POI": "#2879ae", "TRANSPORT": "#299275", "TRANSPORT_LEG": "#de8c38", "ROUTE": "#9667b5"}
    edge_colors = {"FROM": "#4989bb", "TO": "#ce8249", "USES_MODE": "#4e9d80", "USES_LEG": "#a288b8", "PART_OF": "#505050", "ROUTE_VISITS_PROJECTED": "#c4b2d4"}
    records = []
    for scene in args.scenes:
        name = SCENES[scene]
        view = build_view(graph, scene)
        counts = Counter(n["label"] for n in view["nodes"])
        view["p6_cleaning"] = graph["p6_cleaning"]
        if args.view_json_dir:
            write(args.view_json_dir / f"{name}主体网络视图.json", view)
        path = OUT6 / f"可视化可交互知识图谱/{name}知识网络_交互版.html"
        write_html_view(view, path, colors, edge_colors, name)
        html = path.read_text('utf-8')
        html = html.replace('包含全部', f'P6清理后：{counts["ROUTE"]}个线路候选、{counts["TRANSPORT_LEG"]}个交通路段；包含全部', 1)
        html = html.replace('线路对象包含完整路线、行程片段及交通片段，不代表均为独立完整推荐路线。', '')
        html = html.replace('线路→地点为通过访问步骤投影；', '线路→地点为通过访问步骤投影；短片段已移出正式图，线路候选仍需结合原文判断完整性与适用条件；')
        path.write_text(html, encoding='utf-8')
        records.append({"scenic_code": scene, "name": name, "nodes": dict(Counter(n["label"] for n in view["nodes"])), "edges": len(view["edges"])})
    print(json.dumps({"source_graph_sha256": hashlib.sha256(graph_path.read_bytes()).hexdigest(), "views": records}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
