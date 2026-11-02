"""P6 secondary route curation; never modifies upstream P5 artifacts.

Long source-backed sequences are retrieval candidates, not a claim of semantic
correctness. Rejected routes and uncertain affiliations are retained in audit
files, outside the production graph. No cross-document stitching or LLM calls.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
P5 = ROOT / "processed/P5条件建议抽取/runs/full-final-20261101"
OUT = ROOT / "processed/P6知识图谱/quality-stages/route-filtered"
VERSION = "p6-route-curation-v1"


def load(path):
    return json.loads(path.read_text("utf-8-sig"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text):
    return re.sub(r"[\W_]+", "", text).lower()


def source_spans(route, observations):
    spans = set()
    for step in route["steps"]:
        evidence = step.get("evidence", {})
        if evidence.get("doc_id") and isinstance(evidence.get("start"), int) and isinstance(evidence.get("end"), int):
            spans.add((evidence["doc_id"], evidence["start"], evidence["end"]))
    # P5-created sequences have package-level, not claim-level, provenance.
    for oid in route.get("observation_ids", []):
        obs = observations.get(oid, {})
        doc = obs.get("provenance", {}).get("doc_id")
        for block in obs.get("provenance", {}).get("blocks", []):
            if doc and isinstance(block.get("start"), int) and isinstance(block.get("end"), int):
                spans.add((doc, block["start"], block["end"]))
    return sorted(spans)


def mapped_stages(route):
    # Groups are unordered within a step. A->B->A preserves the return visit;
    # collapsing adjacent equal groups is only a length diagnostic, not editing.
    stages = []
    for step in route["steps"]:
        group = tuple(sorted(step.get("poi_ids", [])))
        if group and (not stages or stages[-1] != group):
            stages.append(group)
    return stages


def classify(route, source_text, nodes):
    steps = route["steps"]
    stages = mapped_stages(route)
    descriptions = " ".join(x.get("text", "") for x in route.get("summaries", []))
    step_text = " ".join(s.get("evidence", {}).get("text", "") for s in steps)
    text = descriptions + " " + step_text
    poi_ids = {pid for s in steps for pid in s.get("poi_ids", [])}
    decision = {"route_id": route["route_id"], "scenic_code": route["scenic_code"],
                "upstream": "P5" if route["route_id"].startswith("ROUTEOBS-") else "P3",
                "step_count": len(steps), "ordered_mapped_stage_count": len(stages),
                "mapped_step_count": sum(bool(s.get("poi_ids")) for s in steps),
                "different_poi_count_diagnostic_only": len(poi_ids),
                "status": "excluded", "route_class": "local_fragment", "reason": "局部或较短游览序列，不作为长线路推荐主干"}
    if re.search(r"赶不及|另一位家长|或右线|原文不完整|作者认为该路线不科学", text):
        decision.update(route_class="ambiguous_alternatives", reason="含替代方案、并行动作、批评方案或不完整文本，不自动串成一条推荐线路")
    elif not poi_ids:
        decision.update(route_class="unresolved_fragment", reason="没有已匹配的地点，保留原文供后续复核")
    elif len(stages) <= 1:
        decision.update(route_class="single_stage", reason="单个地点组或重复同一地点组；组内多个地点不推断成有序路线")
    elif all(nodes[p]["properties"].get("role") == "support" for p in poi_ids) or re.search(r"环园线空中缆车|乘坐环园线|天鹅湖线路是一条|天鹅湖线：", text):
        decision.update(route_class="transport_fragment", reason="交通站点接驳或交通服务线路，保留交通事实但不作为游览主干")
    elif route["route_id"] in {"GZCL-OFF-03-R-6b4fdec24e", "ZHCL-OFF-01-R-ed40badaa7"}:
        decision.update(status="retained", route_class="multi_park_overview", reason="原文明确列出三个园区/剧院站次；保留跨园区行程概要，不能替代园内详细路线")
    elif len(steps) >= 6 and len(stages) >= 6:
        decision.update(status="retained", route_class="long_touring_sequence", reason="至少六个有序地点阶段，作为长游览序列；重复访问保留，不按不同实体数筛除，也不声称必为全天完整行程")
    elif len(steps) >= 4 and len(stages) >= 4 and re.search(r"一日|一天|全天|两天|二日|[23]天|Day\s*\d|第[一二三123]天", source_text, re.I):
        decision.update(status="retained", route_class="explicit_day_itinerary", reason="原文明确日程范围且至少四个地点阶段；保留原有步骤，仍需提示未注册地点及事实核验边界")
    elif len(steps) >= 5 and len(stages) >= 3 and len(re.findall(r"\d{1,2}[:：]\d{2}", source_text)) >= 2:
        decision.update(status="retained", route_class="timed_thematic_itinerary", reason="至少五步、三个地点阶段，原文含多处钟点；允许未注册演出步骤，标注地点覆盖缺口")
    elif re.search(r"高铁|地铁|接驳|机场|返程|乘车|缆车", text) and not re.search(r"游览|游玩|徒步|观赏", text):
        decision.update(route_class="transport_fragment", reason="短交通连接，供路段层使用，不作为游览主干")
    return decision


def subsequence(child, parent):
    """Match ordered step groups without inventing order inside a group."""
    j = 0
    for group in parent:
        if j < len(child) and set(child[j]).issubset(group):
            j += 1
    return j == len(child)


def affiliation(child, parent, spans):
    # Never associate across articles or solely because two routes share POIs.
    cspan, pspan = spans[child["route_id"]], spans[parent["route_id"]]
    if not cspan or not pspan:
        return None
    docs = {s[0] for s in cspan}
    if len(docs) != 1 or docs != {s[0] for s in pspan}:
        return None
    lo, hi = min(s[1] for s in pspan), max(s[2] for s in pspan)
    if not all(lo <= a and b <= hi for _, a, b in cspan):
        return None
    cs, ps = mapped_stages(child), mapped_stages(parent)
    if len(cs) < 2 or len(cs) >= len(ps) or not subsequence(cs, ps):
        return None
    return {"route_id": parent["route_id"], "basis": "same_document_source_containment_and_ordered_step_group_subsequence",
            "source_envelope": [next(iter(docs)), lo, hi], "evidence_precision": "package_or_step", "span_width": hi-lo}


def clean_reference_payload(payload, removed_routes, removed_steps, audit, owner):
    """Clear active bindings to excluded scopes; retain exact upstream scope.

    Do not promote a fragment-scoped fact into a global fact: marked scoped
    facts retain scope_status and are excluded from unrestricted recommendations.
    """
    if isinstance(payload, list):
        return [clean_reference_payload(x, removed_routes, removed_steps, audit, owner) for x in payload]
    if not isinstance(payload, dict):
        return payload
    out = {}
    for key, value in payload.items():
        if key in {"route_id", "step_id", "from_step_id", "to_step_id"} and isinstance(value, str) and value in removed_routes | removed_steps:
            out[key] = None
            out["upstream_" + key] = value
            out["scope_status"] = "excluded_route_fragment_scope"
            audit.append({"owner_id": owner, "field": key, "upstream_id": value})
        elif key == "route_bindings":
            kept, excluded = [], []
            for binding in value:
                (excluded if binding.get("route_id") in removed_routes else kept).append(binding)
            out[key] = clean_reference_payload(kept, removed_routes, removed_steps, audit, owner)
            if excluded:
                out["upstream_fragment_bindings"] = excluded
                audit.extend({"owner_id": owner, "field": "route_bindings", "upstream_id": b.get("route_id")} for b in excluded)
        elif key == "targets":
            out[key] = [deepcopy(t) for t in value if t.get("id") not in removed_routes]
            excluded = [deepcopy(t) for t in value if t.get("id") in removed_routes]
            if excluded:
                out["upstream_fragment_targets"] = excluded
                audit.extend({"owner_id": owner, "field": "targets", "upstream_id": t.get("id")} for t in excluded)
                if not out[key]:
                    out["scope_status"] = "excluded_route_fragment_scope"
        else:
            out[key] = clean_reference_payload(value, removed_routes, removed_steps, audit, owner)
    return out


def curate(graph, routes, observations, documents):
    graph = deepcopy(graph)
    original_edge_count = len(graph["edges"])
    nodes = {n["id"]: n for n in graph["nodes"]}
    route_by_id = {r["route_id"]: deepcopy(r) for r in routes}
    spans = {rid: source_spans(r, observations) for rid, r in route_by_id.items()}
    source_evidence = {}
    decisions = {}
    for rid, r in route_by_id.items():
        evidence = []
        for doc, start, end in spans[rid]:
            content = documents[doc]["content"]
            assert 0 <= start < end <= len(content), (rid, doc, start, end)
            evidence.append({"doc_id": doc, "start": start, "end": end, "text": content[start:end]})
        source_evidence[rid] = evidence
        decisions[rid] = classify(r, "\n".join(e["text"] for e in evidence), nodes)
        decisions[rid]["source_spans"] = [list(x) for x in spans[rid]]
    # Exact duplicates require same article, same complete step content, time,
    # transport and rules. Different articles / repeated visits remain separate.
    canonical, exact_duplicates = {}, {}
    for rid in sorted(route_by_id, key=lambda x: (x.startswith("ROUTEOBS-"), x)):
        if decisions[rid]["status"] != "retained":
            continue
        r = route_by_id[rid]
        key = json.dumps({"docs": sorted({x[0] for x in spans[rid]}), "scene": r["scenic_code"],
                          "steps": [{"text": normalized(s.get("evidence", {}).get("text", "")),
                                     "pois": s.get("poi_ids", []), "modes": s.get("transport_ids", []),
                                     "time": s.get("time"), "order_group": s.get("order_group")} for s in r["steps"]],
                          "rules": sorted(r.get("rule_ids", [])), "audiences": r.get("recommended_audiences", [])}, sort_keys=True, ensure_ascii=False)
        if key in canonical:
            exact_duplicates[rid] = canonical[key]
            decisions[rid].update(status="excluded", route_class="exact_duplicate", reason="同文档、完整步骤内容/时间/方式/条件/人群一致；保存别名与全部来源，不跨文档合并")
        else:
            canonical[key] = rid
    # Remove source-contained copies even when they are long. Sequence overlap
    # without source containment remains only a candidate, never an attachment.
    candidates = [r for rid, r in route_by_id.items() if decisions[rid]["status"] == "retained"]
    for r in candidates:
        rid = r["route_id"]
        matches = [a for parent in candidates if parent["route_id"] != rid
                   if (a := affiliation(r, parent, spans))]
        if matches:
            decisions[rid].update(status="excluded", route_class="contained_fragment", reason="同文档原文范围被更长序列包含，且地点阶段按序匹配；不重复作为主干")
    kept_ids = {rid for rid, d in decisions.items() if d["status"] == "retained"}
    fragments = []
    for rid, r in route_by_id.items():
        if rid in kept_ids:
            continue
        matches = [a for pid in sorted(kept_ids) if (a := affiliation(r, route_by_id[pid], spans))]
        matches.sort(key=lambda a: (a["span_width"], a["route_id"]))
        chosen = matches[:1] if matches and (len(matches) == 1 or matches[0]["span_width"] < matches[1]["span_width"]) else []
        if rid in exact_duplicates and exact_duplicates[rid] in kept_ids:
            chosen = [{"route_id": exact_duplicates[rid], "basis": "same_document_exact_full_sequence", "evidence_precision": "step"}]
        decisions[rid]["affiliation_status"] = "source_backed" if chosen else "unresolved_or_ambiguous"
        decisions[rid]["parent_route_ids"] = [x["route_id"] for x in chosen]
        fragments.append({"upstream_route": r, "decision": decisions[rid], "source_evidence": source_evidence[rid],
                          "accepted_affiliations": chosen, "affiliation_candidates": matches})
    cleaned_routes = []
    for rid in sorted(kept_ids):
        r = route_by_id[rid]
        d = decisions[rid]
        raw_text = "\n".join(e["text"] for e in source_evidence[rid])
        days = {s.get("time", {}).get("day") for s in r["steps"]} - {None}
        if d["route_class"] == "multi_park_overview":
            scope = "multi_park_overview_not_detailed_park_route"
        elif len(days) >= 2 or re.search(r"[两二23]天|[二两]日|两日", raw_text):
            scope = "explicit_multi_day"
        elif re.search(r"一日|一天|全天|Day\s*\d|第[一二三123]天", raw_text, re.I):
            scope = "explicit_day"
        else:
            scope = "long_section_or_unstated_duration"
        r["p6_curation"] = {"version": VERSION, "route_class": d["route_class"], "reason": d["reason"],
                             "itinerary_scope": scope,
                             "retrieval_priority": 1 if scope in {"explicit_day", "explicit_multi_day"} else 2,
                             "recommendation_status": "source_backed_candidate_not_independent_gold",
                             "ordered_mapped_stage_count": d["ordered_mapped_stage_count"],
                             "full_day_completeness_verified": False,
                             "source_document_ids": sorted({x[0] for x in spans[rid]}),
                             "unmapped_step_ids": [s["step_id"] for s in r["steps"] if not s.get("poi_ids")],
                             "repeated_visits_preserved": True}
        r["source_evidence"] = source_evidence[rid]
        r["fragment_annotations"] = [{"upstream_route_id": f["upstream_route"]["route_id"],
                                      "summaries": f["upstream_route"].get("summaries", []),
                                      "observation_ids": f["upstream_route"].get("observation_ids", []),
                                      "basis": f["accepted_affiliations"][0]["basis"],
                                      "scope": "fragment_only_not_whole_route"} for f in fragments
                                     if rid in f["decision"].get("parent_route_ids", [])]
        for duplicate, parent in exact_duplicates.items():
            if parent == rid:
                r["observation_ids"] = sorted(set(r.get("observation_ids", [])) | set(route_by_id[duplicate].get("observation_ids", [])))
        nodes[rid]["properties"] = deepcopy(r)
        cleaned_routes.append(r)
    removed_routes = set(route_by_id) - kept_ids
    removed_steps = {s["step_id"] for rid in removed_routes for s in route_by_id[rid]["steps"]}
    dropped = removed_routes | removed_steps
    audit = []
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in dropped]
    for n in graph["nodes"]:
        # OBSERVATION is an immutable evidence record, not an active binding.
        if n["label"] != "OBSERVATION":
            n["properties"] = clean_reference_payload(n["properties"], removed_routes, removed_steps, audit, n["id"])
    graph["edges"] = [e for e in graph["edges"] if e["source"] not in dropped and e["target"] not in dropped]
    for e in graph["edges"]:
        e["properties"] = clean_reference_payload(e["properties"], removed_routes, removed_steps, audit, e["source"])
    # Strengthen merged-source provenance only for verified exact duplicates.
    edge_keys = {(e["source"], e["type"], e["target"]) for e in graph["edges"]}
    for r in cleaned_routes:
        for oid in r.get("observation_ids", []):
            key = (r["route_id"], "SUPPORTED_BY", oid)
            if key not in edge_keys and oid in nodes:
                graph["edges"].append({"source": key[0], "type": key[1], "target": key[2], "properties": {}})
                edge_keys.add(key)
    graph["nodes"].sort(key=lambda n: n["id"])
    graph["edges"].sort(key=lambda e: (e["source"], e["type"], e["target"], json.dumps(e["properties"], sort_keys=True)))
    graph["p6_cleaning"] = {"version": VERSION, "source_run": "full-final-20261101", "route_fragments_in_production_graph": False,
                            "scope": "route_layer_only; existing POI/fact/rule/time semantic issues are not all repaired", "llm_calls": 0}
    graph["note"] = "P6二次清理正式图；仅主干候选为ROUTE；片段在审计文件；交通路段独立保留；观测节点保留上游原始提交，不作活跃线路。"
    cleaned_routes = [deepcopy(n["properties"]) for n in graph["nodes"] if n["label"] == "ROUTE"]
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    assert {n["id"] for n in graph["nodes"] if n["label"] == "ROUTE"} == kept_ids
    assert all(n["id"] not in removed_steps for n in graph["nodes"])
    for label in {"POI", "TRANSPORT", "TRANSPORT_LEG", "CONDITIONAL_RULE", "FACT", "TIME_SUMMARY", "OBSERVATION"}:
        assert {n["id"] for n in graph["nodes"] if n["label"] == label} == {nid for nid, n in nodes.items() if n["label"] == label}
    summary = {"version": VERSION, "before": {"nodes": len(nodes), "edges": original_edge_count, "routes": len(routes)},
               "after": {"nodes": len(graph["nodes"]), "edges": len(graph["edges"]), "routes": len(kept_ids),
                         "node_types": dict(Counter(n["label"] for n in graph["nodes"])), "edge_types": dict(Counter(e["type"] for e in graph["edges"]))},
               "decisions": dict(Counter(d["route_class"] for d in decisions.values())),
               "scene_counts": {scene: {"before": sum(r["scenic_code"] == scene for r in routes),
                                          "after": sum(r["scenic_code"] == scene for r in cleaned_routes)} for scene in ["GZCL", "JZG", "ZHCL"]},
               "fragments": len(fragments), "source_backed_affiliations": sum(bool(f["accepted_affiliations"]) for f in fragments),
               "exact_duplicates": exact_duplicates, "cleared_active_scopes": len(audit), "llm_calls": 0,
               "limitations": ["长序列是可检索候选，不等于每条均为全天完整行程或事实已核真", "未注册地点不新增ID；原步骤及重复访问不改写", "不同文章不自动拼接；相同景点不作为合并依据", "只清理线路层；此前时间/条件语义存疑项仍待后续修订"]}
    return graph, cleaned_routes, fragments, list(decisions.values()), audit, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    protected = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / "processed/P5条件建议抽取").rglob("*") if p.is_file()}
    graph = load(P5 / "integrated/graph.json")
    observations = {r["record_id"]: r for r in load(P5 / "raw_observations.json")}
    docs = {d["doc_id"]: d for d in load(ROOT / "processed/P1爬虫数据清洗/documents.json")}
    result = curate(graph, load(P5 / "integrated/routes.json"), observations, docs)
    cleaned, routes, fragments, decisions, bindings, summary = result
    summary["before"]["edges"] = len(graph["edges"])
    summary["source_graph_sha256"] = sha(P5 / "integrated/graph.json")
    summary["protected_p5_hashes"] = protected
    # Similar sequences outside the strict source envelope are review candidates
    # only. They do not alter production route identities or create graph edges.
    by_id = {r["route_id"]: r for r in load(P5 / "integrated/routes.json")}
    by_decision = {d["route_id"]: d for d in decisions}
    relationship_candidates = []
    for child_id, child in by_id.items():
        child_stages = mapped_stages(child)
        if len(child_stages) < 2:
            continue
        child_docs = {s[0] for s in by_decision[child_id]["source_spans"]}
        if len(child_docs) != 1:
            continue
        for parent in routes:
            parent_id = parent["route_id"]
            if parent_id == child_id or child_docs != set(parent["p6_curation"]["source_document_ids"]):
                continue
            parent_stages = mapped_stages(parent)
            if len(child_stages) < len(parent_stages) and subsequence(child_stages, parent_stages):
                relationship_candidates.append({"child_route_id": child_id, "parent_route_id": parent_id,
                                                "basis": "same_document_ordered_step_group_subsequence",
                                                "status": "candidate_only_not_in_graph", "needs": "原文主题/日序/替代路线范围复核，不能仅凭共同地点确定归属"})
    summary["unconfirmed_relationship_candidates"] = len(relationship_candidates)
    for name, value in {"graph.json": cleaned, "routes.json": routes, "route_fragments_audit.json": fragments,
                        "route_decisions.json": decisions, "scope_cleanup_audit.json": bindings, "cleaning_summary.json": summary}.items():
        save(args.output / name, value)
    for name, labels in {"entities.json": {"POI", "TRANSPORT"}, "transport_legs.json": {"TRANSPORT_LEG"},
                         "conditions.json": {"CONDITIONAL_RULE"}, "entity_time_summaries.json": {"TIME_SUMMARY"}}.items():
        save(args.output / name, [n["properties"] for n in cleaned["nodes"] if n["label"] in labels])
    save(args.output / "route_relationship_candidates.json", relationship_candidates)
    with (args.output / "route_decisions.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["route_id", "scenic_code", "upstream", "step_count", "ordered_mapped_stage_count", "mapped_step_count", "different_poi_count_diagnostic_only", "status", "route_class", "reason", "affiliation_status", "parent_route_ids"]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); writer.writeheader(); writer.writerows(decisions)
    assert all(sha(ROOT / p) == h for p, h in protected.items()), "Upstream P5 changed"
    print(json.dumps({k: v for k, v in summary.items() if k != "protected_p5_hashes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
