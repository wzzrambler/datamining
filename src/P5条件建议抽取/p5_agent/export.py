"""Two output levels; preserve route visits and conditional multivalue facts."""
from __future__ import annotations

import csv
from copy import deepcopy
from collections import Counter, defaultdict

from .common import compact, digest, read_json, write_json
from .evidence import annotate


def export_run(run_dir, corpus, packages):
    snapshots = [read_json(p) for p in sorted((run_dir / "checkpoints").glob("*.json"))]
    preserved_summary = None
    if not snapshots and (run_dir / "raw_observations.json").exists():
        # Release bundles retain observations and input manifests, while bulky
        # request/response checkpoints may be archived outside the repository.
        preserved_summary = read_json(run_dir / "summary.json")
        manifest = read_json(run_dir / "input_manifest.json")
        if manifest.get("input_hashes", corpus.hashes) != corpus.hashes:
            raise ValueError("正式交付输入哈希与当前P1/P2/P3不一致，拒绝混用版本重建")
        packages = [{"context": {"package_key": p["package_key"], "package_kind": p["package_kind"]}}
                    for p in manifest["effective_packages"]]
        grouped = defaultdict(list)
        for record in read_json(run_dir / "raw_observations.json"):
            grouped[record["package_key"]].append(record)
        snapshots = [{"host": {"status": p["status"], "records": grouped[p["package_key"]]}, "usage": []}
                     for p in sorted(manifest["effective_packages"], key=lambda p: p["package_key"])]
    # 分片补跑的包不在原 packages 列表里，只看传入的 packages 会把分片记录的 scope 判成 route。
    # 发布清单记录的是实际跑过的全部包，以它为准补充分片键。
    fragment_keys = {p["context"]["package_key"] for p in packages
                     if p["context"]["package_kind"] == "route_fragment"}
    manifest_path = run_dir / "input_manifest.json"
    if manifest_path.exists():
        fragment_keys |= {p["package_key"] for p in read_json(manifest_path)["effective_packages"]
                          if p["package_kind"] == "route_fragment"}
    all_records = [dict(r, package_status=s["host"]["status"]) for s in snapshots for r in s["host"]["records"]]
    accepted = [r for r in all_records if r["active"] and r["package_status"] in {"COMPLETED", "COMPLETED_WITH_SKIPS"}]
    # 导出时把引文定位回原文，证据从"包级"细化到"句级"；定位不到的仍标 package
    evidence_stats = annotate(accepted, corpus.documents)
    write_json(run_dir / "raw_observations.json", all_records)
    write_json(run_dir / "partial_observations.json", [r for r in all_records if r["active"] and r not in accepted])
    records = {r["record_id"]: r for r in accepted}
    entities = {eid: dict(item, scenic_code=scene if item["kind"] == "POI" else None,
                          facts=[], observation_ids=[], rule_ids=[], restriction_rule_ids=[], recommendation_rule_ids=[])
                for scene, registry in corpus.registries.items() for eid, item in registry.items()}
    routes = {r["route_id"]: {"route_id": r["route_id"], "scenic_code": r["scenic_code"],
              "nature": r["nature"], "scope": r["scope"], "granularity": r["granularity"],
              "steps": deepcopy(r["steps"]), "order_relations": r["order_relations"], "hierarchy": r.get("hierarchy", []),
              "summaries": [], "recommended_audiences": [], "observation_ids": [],
              "extracted_step_sequences": [], "rule_ids": [], "restriction_rule_ids": [],
              "recommendation_rule_ids": []} for r in corpus.routes}
    facts, legs, rules, normalizations = {}, {}, {}, []
    leg_refs, condition_links = {}, defaultdict(list)

    def observe(collection, key, item, record_id):
        if key not in collection:
            collection[key] = dict(item, observation_ids=[])
        if record_id not in collection[key]["observation_ids"]:
            collection[key]["observation_ids"].append(record_id)
        return collection[key]

    for r in accepted:
        a, rid, scene = r["arguments"], r["record_id"], r["scenic_code"]
        for n in a["normalizations"]:
            normalizations.append(dict(n, method="p5_llm_normalization", observation_id=rid))
        if r["tool_name"] == "submit_poi_facts":
            entities[a["entity_id"]]["observation_ids"].append(rid)
            for fact in a["facts"]:
                item = dict(fact, entity_id=a["entity_id"], route_id=a["route_id"], step_id=a["step_id"])
                key = digest(item)
                observe(facts, key, item, rid)
        elif r["tool_name"] == "submit_transport_leg":
            identity = {k: a[k] for k in ("from_entity_id", "to_entity_id", "transport_entity_id", "assertion_kind", "service_name_text")}
            key = "LEG-" + digest(identity)[:20]
            leg = observe(legs, key, dict(identity, leg_id=key, scenic_code=scene, facts=[], route_bindings=[],
                                        rule_ids=[], restriction_rule_ids=[], recommendation_rule_ids=[]), rid)
            binding = {k: a[k] for k in ("route_id", "from_step_id", "to_step_id")}
            if binding["route_id"] and binding not in leg["route_bindings"]:
                leg["route_bindings"].append(binding)
            for fact in a["facts"]:
                item = dict(fact, leg_id=key, route_id=a["route_id"], from_step_id=a["from_step_id"], to_step_id=a["to_step_id"])
                observe(facts, digest(item), item, rid)
            leg_refs[r["leg_ref"]] = key
        elif r["tool_name"] == "submit_route":
            route_id = r.get("route_id") or a["route_id"]
            if route_id not in routes:
                steps = [{"step_id": s["step_id"], "index": i, "time": {"day": None, "period": "unspecified"},
                          "route_poi_refs": [{"entity_id": pid} for pid in s["poi_ids"]],
                          "transport_refs": [], "evidence": {"text": s["text"]}}
                         for i, s in enumerate(r["route_steps"], 1)]
                routes[route_id] = {"route_id": route_id, "scenic_code": scene, "nature": "extracted",
                    "scope": None, "granularity": "unspecified", "steps": steps,
                    "order_relations": [{"before": x["step_id"], "after": y["step_id"]} for x, y in zip(steps, steps[1:])],
                    "hierarchy": [], "summaries": [], "recommended_audiences": [], "observation_ids": [],
                    "extracted_step_sequences": [], "rule_ids": [], "restriction_rule_ids": [], "recommendation_rule_ids": []}
            route = routes[route_id]
            if r.get("route_steps"):
                route["extracted_step_sequences"].append({"steps": r["route_steps"], "observation_id": rid})
            if a["summary"]:
                summary = {"text": a["summary"], "scope": "fragment" if r["package_key"] in fragment_keys else "route",
                           "observation_id": rid}
                if summary not in route["summaries"]:
                    route["summaries"].append(summary)
            for audience in a["recommended_audiences"]:
                if audience not in route["recommended_audiences"]:
                    route["recommended_audiences"].append(audience)
            route["observation_ids"].append(rid)
        for condition_id in a.get("condition_record_ids", []):
            if r["tool_name"] == "submit_poi_facts":
                target = {"kind": "POI", "id": a["entity_id"]}
            elif r["tool_name"] == "submit_transport_leg":
                target = {"kind": "LEG", "id": leg_refs[r["leg_ref"]]}
            else:
                target = {"kind": "ROUTE", "id": r.get("route_id") or a["route_id"]}
            if target not in condition_links[condition_id]:
                condition_links[condition_id].append(target)
    for r in accepted:
        if r["tool_name"] != "submit_conditional_advice":
            continue
        a = r["arguments"]
        targets = [{"kind": t["kind"], "id": leg_refs[t["id"]] if t["kind"] == "LEG" else t["id"]} for t in a["targets"]]
        for target in condition_links[r["record_id"]]:
            if target not in targets:
                targets.append(target)
        targets.sort(key=lambda t: (t["kind"], t["id"]))
        identity = {k: a[k] for k in ("statement_kind", "condition", "consequence_text", "force")}
        identity.update(targets=targets, scenic_code=r["scenic_code"])
        key = "RULE-" + digest(identity)[:20]
        observe(rules, key, dict(identity, rule_id=key,
                p5_complete=a["statement_kind"] in {"conditional_advice", "conditional_restriction"}), r["record_id"])
    for key, fact in facts.items():
        if "entity_id" in fact:
            entities[fact["entity_id"]]["facts"].append(fact)
        else:
            legs[fact["leg_id"]]["facts"].append(fact)
    for rule in rules.values():
        for target in rule["targets"]:
            obj = ({"POI": entities, "TRANSPORT": entities, "LEG": legs, "ROUTE": routes}[target["kind"]])[target["id"]]
            obj["rule_ids"].append(rule["rule_id"])
            field = ("restriction_rule_ids" if rule["force"] in {"prohibited", "required", "avoid"}
                     else "recommendation_rule_ids" if rule["force"] in {"recommended", "optional"} else None)
            if field:
                obj[field].append(rule["rule_id"])

    # One consumer-facing sequence, without replacing P3 order/day/group data.
    for route in routes.values():
        for step in route["steps"]:
            poi_ids = {p["entity_id"] for p in step["route_poi_refs"] if p.get("entity_id")}
            poi_ids.update(p["entity_id"] for p in step.get("p3_normalized_links", []))
            modes = {corpus.mentions.get(ref.get("mention_id"), {}).get("entity_id")
                     for ref in step["transport_refs"]}
            modes.discard(None)
            extracted = [s for seq in route["extracted_step_sequences"] for s in seq["steps"]
                         if s["step_id"] == step["step_id"]]
            for item in extracted:
                poi_ids.update(item["poi_ids"])
                modes.update(item["transport_ids"])
            step["poi_ids"], step["transport_ids"] = sorted(poi_ids), sorted(modes)
            step["extracted_texts"] = list(dict.fromkeys(s["text"] for s in extracted))
    nodes, edges = {}, {}

    def node(identifier, label, **properties):
        nodes[identifier] = {"id": identifier, "label": label, "properties": properties}

    def edge(source, relation, target, **properties):
        value = {"source": source, "type": relation, "target": target, "properties": properties}
        edges[digest(value)] = value

    for entity in entities.values():
        node(entity["id"], entity["kind"], **entity)
    for route in routes.values():
        route_id = route["route_id"]
        node(route_id, "ROUTE", **route)
        for step in route["steps"]:
            sid = step["step_id"]
            node(sid, "ROUTE_STEP", **step)
            edge(route_id, "HAS_STEP", sid)
            poi_ids = set(step["poi_ids"])
            poi_ids.update(x["entity_id"] for x in step.get("p3_normalized_links", []))
            for n in normalizations:
                if n["mention_id"] in step.get("p2_mention_ids", []):
                    poi_ids.add(n["entity_id"])
            for poi in poi_ids:
                edge(sid, "VISITS", poi)
            for mode in step["transport_ids"]:
                edge(sid, "MENTIONS_MODE", mode)
        for relation in route["order_relations"]:
            edge(relation["before"], "PRECEDES", relation["after"])
        for sequence in route["extracted_step_sequences"]:
            for step in sequence["steps"]:
                for pid in step["poi_ids"]:
                    edge(step["step_id"], "VISITS", pid)
                for tid in step["transport_ids"]:
                    edge(step["step_id"], "MENTIONS_MODE", tid)
    for leg in legs.values():
        lid = leg["leg_id"]
        node(lid, "TRANSPORT_LEG", **leg)
        edge(lid, "FROM", leg["from_entity_id"])
        edge(lid, "TO", leg["to_entity_id"])
        if leg["transport_entity_id"]:
            edge(lid, "USES_MODE", leg["transport_entity_id"])
        for binding in leg["route_bindings"]:
            edge(binding["route_id"], "USES_LEG", lid, from_step_id=binding["from_step_id"], to_step_id=binding["to_step_id"])
    for rule in rules.values():
        node(rule["rule_id"], "CONDITIONAL_RULE", **rule)
        for target in rule["targets"]:
            edge(rule["rule_id"], "APPLIES_TO", target["id"])
    for fact in facts.values():
        fid = "FACT-" + digest({k: v for k, v in fact.items() if k != "observation_ids"})[:20]
        node(fid, "FACT", **fact)
        edge(fact.get("entity_id") or fact.get("leg_id"), "HAS_FACT", fid)
        for rid in fact["observation_ids"]:
            edge(fid, "SUPPORTED_BY", rid)
        if fact["attribute"] == "parent_park":
            edge(fact["entity_id"], "PART_OF", fact["related_poi_id"], observation_ids=fact["observation_ids"])
    for record in accepted:
        node(record["record_id"], "OBSERVATION", tool_name=record["tool_name"],
             package_key=record["package_key"], scenic_code=record["scenic_code"], provenance=record["provenance"])
    for obj in list(entities.values()) + list(legs.values()) + list(routes.values()) + list(rules.values()):
        oid = obj.get("id") or obj.get("leg_id") or obj.get("route_id") or obj.get("rule_id")
        for rid in set(obj["observation_ids"]):
            edge(oid, "SUPPORTED_BY", rid)
    dangling = [e for e in edges.values() if e["source"] not in nodes or e["target"] not in nodes]
    if dangling:
        raise ValueError("汇总图出现悬空引用，已停止导出")
    write_json(run_dir / "integrated/entities.json", list(entities.values()))
    write_json(run_dir / "integrated/transport_legs.json", list(legs.values()))
    write_json(run_dir / "integrated/routes.json", list(routes.values()))
    write_json(run_dir / "integrated/conditions.json", list(rules.values()))
    write_json(run_dir / "integrated/normalizations.json", normalizations)
    write_json(run_dir / "integrated/observations.json", accepted)
    write_json(run_dir / "integrated/graph.json", {"nodes": sorted(nodes.values(), key=lambda n: n["id"]),
               "edges": sorted(edges.values(), key=lambda e: (e["source"], e["type"], e["target"], compact(e["properties"]))),
               "schema_version": "p5-extraction-graph-v3",
               "note": "P5保留逐条抽取及确定性实体关联；时间汇总、条件推荐汇总和线路筛选统一由P6入库前处理。P3顺序边不代表交通连通。"})
    with (run_dir / "integrated/conditions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rule_id", "scenic_code", "statement_kind", "condition", "consequence", "force", "targets", "categories", "p5_complete", "observation_ids"])
        writer.writeheader()
        for r in rules.values():
            writer.writerow({"rule_id": r["rule_id"], "scenic_code": r["scenic_code"], "statement_kind": r["statement_kind"],
                "condition": (r["condition"] or {}).get("text", ""), "consequence": r["consequence_text"], "force": r["force"],
                "targets": compact(r["targets"]), "categories": compact(sorted({x["category"] for x in (r["condition"] or {}).get("clauses", [])})),
                "p5_complete": r["p5_complete"], "observation_ids": compact(r["observation_ids"])})
    report = {"run_id": run_dir.name, "packages_prepared": len(packages), "packages_attempted": len(snapshots),
              "status_counts": dict(Counter(s["host"]["status"] for s in snapshots)),
              "active_completed_observations": len(accepted), "conditions": len(rules),
              "p5_complete_conditions": sum(r["p5_complete"] for r in rules.values()),
              "transport_legs": len(legs), "p3_routes_inherited": len(corpus.routes),
              "new_routes_extracted": len(routes) - len(corpus.routes),
              "rule_categories": dict(Counter(x["category"] for r in rules.values() for x in (r["condition"] or {}).get("clauses", []))),
              # 输入 token 里九成以上命中前缀缓存，按原价计费的只有 miss 部分。
              # 只报 prompt_tokens 会让人以为一次全量烧了 3500 万，实际不是这么算的。
              "usage": {k: sum(u.get(k, 0) for s in snapshots for u in s["usage"])
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                                  "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")},
              "requests": sum(len(s["usage"]) for s in snapshots),
              "max_actual_prompt_tokens": max((u.get("prompt_tokens", 0) for s in snapshots for u in s["usage"]), default=0),
              "evidence": evidence_stats,
              "limitations": ["未完成包的已接纳记录只进入partial_observations，不进入汇总图",
                              "部分记录的引文未能唯一定位，证据精度仍为包级",
                              "时间仅保留逐条属性；跨来源时间汇总和条件推荐汇总由P6负责",
                              "图为P6输入材料；未连接Neo4j，未完成课程人工质量评估"]}
    if preserved_summary is not None:
        for key in ("usage", "usage_selected_completed", "original_packages_covered", "original_failed_packages_replaced",
                    "recovery_fragments", "max_actual_prompt_tokens", "neo4j_validation"):
            if key in preserved_summary:
                report[key] = preserved_summary[key]
        report["usage_source"] = "preserved_real_execution_summary; offline_export_made_no_model_requests"
        if report.get("neo4j_validation", {}).get("verified"):
            report["limitations"] = preserved_summary["limitations"]
    report.pop('neo4j_validation', None)
    report['artifact_stage'] = 'P5_extraction_only'
    report['quality_processing_owner'] = 'P6'
    report['limitations'] = ['来源精度为输入包，不声称每条事实均已定位精确引句',
                            'P5仅保存逐条抽取与关联；时间汇总、条件推荐汇总、线路筛选和入库统一由P6负责',
                            '未完成独立人工金标准评估；历史模型用量从真实日志保留，本次导出不请求模型']
    # 引用来源分三类：本包正文提到过、只在景区注册表里、本包内新建。这里不做过滤，
    # 只把数量摊开，事后能查出有多少关系没有本地提及支撑。
    report['reference_scopes'] = dict(Counter(
        scope for r in accepted for scope in (r.get('reference_scopes') or {}).values()))
    write_json(run_dir / "summary.json", report)
    return report
