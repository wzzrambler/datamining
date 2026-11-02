from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .budget import initial_messages
from .common import ROOT, digest, file_hash, read_json, matches_sha256


class Corpus:
    def __init__(self, root=ROOT):
        paths = {
            "p1": root / "processed/P1爬虫数据清洗/documents.json",
            "p2": root / "processed/P2实体识别/entities.json",
            "p3": root / "processed/P3路线层级挖掘/routes.json",
            "sources": root / "processed/P1爬虫数据清洗/辅助资料/paragraph_sources.json",
        }
        self.hashes = {k: file_hash(p) for k, p in paths.items()}
        # Keep existing release/provenance IDs when bytes differ only by EOL.
        release = root / 'processed/P5条件建议抽取/runs/full-final-20261101/input_manifest.json'
        if release.is_file():
            for key, expected in read_json(release).get('input_hashes', {}).items():
                if key in paths and matches_sha256(paths[key], expected):
                    self.hashes[key] = expected
        docs, p2, p3, sources = (read_json(paths[k]) for k in paths)
        if not matches_sha256(paths['p1'], p2.get('input_sha256')):
            raise ValueError("P2与P1输入哈希不一致，请先更新前置阶段")
        if not matches_sha256(paths['sources'], p2.get('origin_sha256')):
            raise ValueError("P2来源映射与P1不一致")
        self.documents = {d["doc_id"]: d for d in docs}
        self.entity_documents = {d["doc_id"]: d for d in p2["documents"]}
        self.routes = p3["routes"]
        if p3.get("p2_config_version") != p2.get("config_version"):
            raise ValueError("P3使用的P2配置版本不一致")
        for route in self.routes:
            doc = self.documents[route["doc_id"]]
            if route["scenic_code"] != doc["scenic_code"]:
                raise ValueError("P3线路景区与P1文档不一致")
            for step in route["steps"]:
                evidence = step["evidence"]
                if doc["content"][evidence["start"]:evidence["end"]] != evidence["text"]:
                    raise ValueError(f"P3步骤原文证据与P1不一致：{step['step_id']}")
        self.sources = {(x["doc_id"], x["para_id"]): x for x in sources}
        self.mentions = {m["mention_id"]: m for d in p2["documents"] for m in d["mentions"]}
        self.registries = defaultdict(dict)
        self.entity_scenes = {}
        for d in p2["documents"]:
            for m in d["mentions"]:
                if not m.get("entity_id"):
                    continue
                item = {"id": m["entity_id"], "name": m.get("canonical_name") or m["text"], "kind": m["type"]}
                if m["type"] == "POI":
                    item.update(role=m["poi_role"], subtype=m["subtype"])
                    self.entity_scenes[item["id"]] = d["scenic_code"]
                self.registries[d["scenic_code"]][item["id"]] = item
        self.conditions = read_json(Path(__file__).resolve().parents[1] / "condition_terms.json")
        self.coverage = []

    def source(self, doc_id, para_id):
        return self.sources.get((doc_id, para_id), {"kind": "legacy", "para_id": para_id})

    @staticmethod
    def overlap(a, b):
        return a["start"] < b["end"] and b["start"] < a["end"]

    def make_package(self, doc, spans, route=None, fragment=False):
        local = [m for m in self.entity_documents[doc["doc_id"]]["mentions"]
                 if any(self.overlap(m, s) for s in spans)]
        scene = doc["scenic_code"]
        registry = sorted(self.registries[scene].values(), key=lambda x: x["id"])
        linked = set()
        steps = []
        if route:
            for step in route["steps"]:
                if not any(self.overlap(step["evidence"], s) for s in spans):
                    continue
                links = step.get("p3_normalized_links", [])
                linked.update(x["entity_id"] for x in links)
                def transport_ids(refs):
                    return sorted({self.mentions[x["mention_id"]]["entity_id"] for x in refs
                                   if self.mentions.get(x.get("mention_id"), {}).get("entity_id")})
                steps.append({
                    "step_id": step["step_id"], "index": step["index"],
                    "poi_ids": sorted({x["entity_id"] for x in step["route_poi_refs"] if x.get("entity_id")} |
                                      {x["entity_id"] for x in links}),
                    "transport_ids": transport_ids(step["transport_refs"]),
                    "context_transport_ids": transport_ids(step.get("context_transport_mentions", [])),
                    "unmapped_mentions": [x["text"] for x in step["route_poi_refs"] if not x.get("entity_id")
                                          and not any(x["text"] == a["text"] for a in links)],
                    "normalized_links": [{k: x[k] for k in ("text", "entity_id", "target_p2_mention_id", "method")}
                                         for x in links],
                    "day": step["time"].get("day"), "period": step["time"]["period"],
                    "order_group": step.get("order_group"),
                    "evidence_text": step["evidence"]["text"],
                    "clock_texts": step["time"].get("clock_texts", []),
                    "duration_texts": [x["text"] for x in step.get("durations", [])],
                    "activity_texts": [x["text"] for x in step.get("activity", [])],
                    "mention_ids": step.get("p2_mention_ids", []),
                })
        step_ids = {x["step_id"] for x in steps}
        signature = {"doc": doc["doc_id"], "route": route["route_id"] if route else None,
                     "spans": [(s["start"], s["end"]) for s in spans]}
        context = {
            "package_key": "PKG-" + digest(signature)[:20], "active_scenic_code": scene,
            "package_kind": "route_fragment" if fragment else "route" if route else "paragraph",
            "heading": doc.get("title") or None,
            "scene_poi_registry": [x for x in registry if x["kind"] == "POI"],
            "scene_transport_registry": [x for x in registry if x["kind"] == "TRANSPORT"],
            "blocks": [{"block_id": f"B{i+1:03d}", "text": doc["content"][s["start"]:s["end"]],
                        "source_kind": self.source(doc["doc_id"], s["i"]).get("kind", "legacy"),
                        "source_group": "ORIGIN-" + digest({k: v for k, v in self.source(doc["doc_id"], s["i"]).items()
                                                               if k not in {"doc_id", "para_id"}})[:12]}
                       for i, s in enumerate(spans)],
            "local_mentions": [{"mention_id": m["mention_id"], "text": m["text"], "type": m["type"],
                                "entity_id": m.get("entity_id") or None,
                                "canonical_name": m.get("canonical_name") or None,
                                "poi_role": m.get("poi_role") or None, "subtype": m["subtype"],
                                "time_context_text": m.get("time_context", {}).get("expression")}
                               for m in local],
            "p3_routes": [] if not route else [{"route_id": route["route_id"], "nature": route["nature"],
                         "scope": route["scope"], "granularity": route["granularity"], "steps": steps,
                         "order_relations": [{"before": x["before"], "after": x["after"]}
                                             for x in route["order_relations"]
                                             if x["before"] in step_ids and x["after"] in step_ids]}],
            "eligible_references": {
                "poi_ids": sorted({m["entity_id"] for m in local if m["type"] == "POI" and m.get("entity_id")} | linked |
                                  {x["id"] for x in registry if x["kind"] == "POI" and any(x["name"] in doc["content"][s["start"]:s["end"]] for s in spans)}),
                "transport_ids": sorted({m["entity_id"] for m in local if m["type"] == "TRANSPORT" and m.get("entity_id")} |
                                        {x["id"] for x in registry if x["kind"] == "TRANSPORT" and any(x["name"] in doc["content"][s["start"]:s["end"]] for s in spans)}),
                "route_ids": [route["route_id"]] if route else [],
            },
        }
        provenance = {"doc_id": doc["doc_id"], "source_url": doc.get("source_url"), "input_hashes": self.hashes,
                      "evidence_precision": "package",  # Not a claim-level supporting quote.
                      "blocks": [{"block_id": f"B{i+1:03d}", "start": s["start"], "end": s["end"],
                                  "para_id": s["i"], "origin": self.source(doc["doc_id"], s["i"])}
                                 for i, s in enumerate(spans)]}
        return {"context": context, "provenance": provenance}

    def split_to_budget(self, doc, spans, route, budget, tools, fragment=False):
        package = self.make_package(doc, spans, route, fragment)
        if budget.fits(initial_messages(package["context"]), tools, initial=True):
            return [package]
        # Preserve complete routes whenever possible; split only on actual budget
        # overflow. Do not silently truncate text or remove its identifiers.
        if len(spans) > 1:
            middle = len(spans) // 2
            halves = (spans[:middle], spans[middle:])
        else:
            s = spans[0]
            if s["end"] - s["start"] < 100:
                raise ValueError(f"景区前缀/工具/最小文本超出预算：{doc['doc_id']}，初始估算{budget.estimate(initial_messages(package['context']), tools)}，请调整配置")
            middle = (s["start"] + s["end"]) // 2
            text = doc["content"]
            # Prefer a sentence boundary, then never cut through a P2 mention or
            # a P3 step. A single oversized step requires manual handling.
            candidates = [i+1 for i in range(s["start"], s["end"]-1) if text[i] in "。！？；\n"]
            protected = self.entity_documents[doc["doc_id"]]["mentions"]
            if route:
                protected = protected + [x["evidence"] for x in route["steps"]]
            candidates = [i for i in candidates if not any(x["start"] < i < x["end"] for x in protected)]
            if not candidates:
                raise ValueError("单个不可拆分句/步骤超出30K预算，已停止而非截断")
            cut = min(candidates, key=lambda i: abs(i-middle))
            halves = ([dict(s, end=cut)], [dict(s, start=cut)])
        result = []
        for half in halves:
            result.extend(self.split_to_budget(doc, half, route, budget, tools, fragment=bool(route)))
        return result

    def build(self, budget, tools, scenic=None):
        packages, covered = [], defaultdict(set)
        routes = [r for r in self.routes if scenic is None or r["scenic_code"] == scenic]
        for route in routes:
            doc = self.documents[route["doc_id"]]
            evidence = [s["evidence"] for s in route["steps"]]
            evidence += [e for s in route["steps"] for e in s.get("context_evidence", [])]
            spans = [p for p in doc["paragraphs"] if any(self.overlap(p, e) for e in evidence)]
            if not spans:
                raise ValueError(f"P3线路没有可回溯正文：{route['route_id']}")
            packages.extend(self.split_to_budget(doc, spans, route, budget, tools))
            # Full selected paragraphs really are included, not only the steps.
            covered[doc["doc_id"]].update(p["i"] for p in spans)
        coverage = []
        for doc in self.documents.values():
            if scenic is not None and doc["scenic_code"] != scenic:
                continue
            mentions = self.entity_documents[doc["doc_id"]]["mentions"]
            names = [x["name"] for x in self.registries[doc["scenic_code"]].values()]
            for p in doc["paragraphs"]:
                reason = "route_covered" if p["i"] in covered[doc["doc_id"]] else None
                if not reason:
                    hits = []
                    if any(self.overlap(p, m) for m in mentions) or any(n in p["text"] for n in names):
                        hits.append("entity")
                    if any(term in p["text"] for term in self.conditions):
                        hits.append("condition")
                    reason = "+".join(hits) or "not_selected"
                    if hits:
                        packages.extend(self.split_to_budget(doc, [p], None, budget, tools))
                coverage.append({"doc_id": doc["doc_id"], "para_id": p["i"], "start": p["start"],
                                 "end": p["end"], "reason": reason})
        self.coverage = coverage
        # Scene batches, routes first inside each batch.
        packages.sort(key=lambda x: (x["context"]["active_scenic_code"], x["context"]["package_kind"] == "paragraph"))
        return packages


def package_statistics(packages, budget, tools):
    from statistics import mean
    counts = [budget.estimate(initial_messages(p["context"]), tools) for p in packages]
    ordered = sorted(counts)
    return {"packages": len(packages), "by_kind": dict(Counter(p["context"]["package_kind"] for p in packages)),
            "by_scene": dict(Counter(p["context"]["active_scenic_code"] for p in packages)),
            "token_method": budget.method, "initial_input_estimate": {
                "mean": round(mean(counts)) if counts else 0, "min": min(counts, default=0),
                "p95": ordered[min(len(ordered)-1, int(len(ordered)*.95))] if ordered else 0,
                "max": max(counts, default=0)},
            "per_request_policy": {"context_limit": budget.settings.context_limit,
                "output_reserve": budget.settings.output_tokens, "safety_reserve": budget.settings.safety_tokens,
                "initial_history_reserve": budget.settings.history_reserve},
            "note": "离线估算不是DeepSeek精确分词；真实usage单独记录并向上校准。累计计费token随轮次重复输入增长。"}
