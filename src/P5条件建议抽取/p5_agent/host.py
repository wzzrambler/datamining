"""Transactional tool host. No model-generated code is ever executed."""
from __future__ import annotations

import json
from copy import deepcopy

from .common import digest

BUSINESS = {"submit_poi_facts", "submit_transport_leg", "submit_route", "submit_conditional_advice"}


class Reject(Exception):
    def __init__(self, code, message, path="/arguments", dependents=None):
        self.code, self.message, self.path = code, message, path
        self.dependents = dependents or []


class ToolHost:
    def __init__(self, package, contracts, max_same_error=2, entity_scenes=None):
        self.package = package
        self.context = package["context"]
        self.contracts = contracts
        self.max_same_error = max_same_error
        self.entity_scenes = entity_scenes or {}
        self.registry = {x["id"]: x for x in self.context["scene_poi_registry"] + self.context["scene_transport_registry"]}
        self.mentions = {m["mention_id"]: m for m in self.context["local_mentions"]}
        self.routes, self.steps = {}, {}
        self.records, self.issues, self.events = {}, {}, []
        self.finished, self.status = False, "RUNNING"

    @property
    def active(self):
        return {k: v for k, v in self.records.items() if v["active"]}

    @property
    def open_issues(self):
        return [k for k, v in self.issues.items() if v["status"] == "open"]

    def result(self, ok=True, code="OK", message="已接纳。", **data):
        return {"ok": ok, "code": code, "message": message,
                "data": {"record_ids": [], "leg_refs": [], "dependent_record_ids": [],
                         "open_issue_ids": self.open_issues,
                         "skipped_issue_ids": [k for k, v in self.issues.items() if v["status"] == "skipped"],
                         "route_ids": [], "step_ids": [],
                         "candidates": [], "issue_id": None, "finished": self.finished, **data},
                "errors": [], "next_actions": ["stop"] if self.finished else []}

    def fail(self, name, args, error):
        issue_id, retryable = None, True
        if name in BUSINESS and not self.finished:
            signature = digest({"name": name, "args": args, "code": error.code})
            issue_id = next((k for k, v in self.issues.items() if v["signature"] == signature and v["status"] == "open"), None)
            if issue_id is None:
                issue_id = f"ISSUE-{len(self.issues)+1:04d}"
                self.issues[issue_id] = {"signature": signature, "tool_name": name, "status": "open", "attempts": 0,
                                         "code": error.code, "arguments": deepcopy(args)}
            self.issues[issue_id]["attempts"] += 1
            retryable = self.issues[issue_id]["attempts"] <= self.max_same_error
        response = self.result(False, error.code, error.message, issue_id=issue_id,
                               dependent_record_ids=error.dependents)
        response["errors"] = [{"path": error.path, "code": error.code, "message": error.message, "retryable": retryable}]
        response["next_actions"] = (["review_context", "retry_arguments", "skip_relation"] if retryable
                                    else ["skip_relation", "finish_chunk"])
        if error.code == "DEPENDENTS_REQUIRE_RETRACTION":
            response["next_actions"] = ["retract_records"]
        return response

    def normalization_state(self):
        mappings, owners = {}, {}
        for record_id, record in self.active.items():
            for n in record["arguments"].get("normalizations", []):
                mappings[n["mention_id"]] = n["entity_id"]
                owners.setdefault((n["mention_id"], n["entity_id"]), set()).add(record_id)
        return mappings, owners

    def reference(self, identifier, kind, eligible, dependencies, owners):
        if identifier is None:
            return
        if kind == "ROUTE":
            if identifier not in self.routes:
                raise Reject("ROUTE_NOT_IN_PACKAGE", "请使用当前消息或工具返回的线路ID；独立信息填null，新线路由submit_route生成ID。")
            creator = self.routes[identifier].get("creator_record_id")
            if creator:
                dependencies.add(creator)
            return
        if kind == "LEG":
            match = next((rid for rid, r in self.active.items() if r.get("leg_ref") == identifier), None)
            if match is None:
                raise Reject("LEG_NOT_IN_SESSION", "使用本包submit_transport_leg成功返回的leg_ref，或忽略此关系。")
            dependencies.add(match)
            return
        if identifier not in self.registry:
            if identifier in self.entity_scenes:
                raise Reject("SCENIC_SCOPE_MISMATCH", "POI属于其他景区；请查阅当前景区表或忽略关系。")
            raise Reject("UNKNOWN_REFERENCE", "ID不在当前景区注册表；请复制表中对应ID。匹配不到地点时保留条件，跳过该地点关系。")
        if self.registry[identifier]["kind"] != kind:
            raise Reject("REFERENCE_TYPE_MISMATCH", f"这里需要{kind}类型的entity_id。")
        # The model matches the original text against the whole scenic registry.
        # Earlier local mention detection is no longer a prerequisite for use.

    def refresh_routes(self):
        self.routes = {r["route_id"]: deepcopy(r) for r in self.context["p3_routes"]}
        for rid, record in self.active.items():
            if record.get("created_route_id"):
                self.routes[record["created_route_id"]] = {
                    "route_id": record["created_route_id"], "steps": record["route_steps"],
                    "creator_record_id": rid}
        self.steps = {s["step_id"]: (r["route_id"], s) for r in self.routes.values() for s in r["steps"]}

    def validate_business(self, name, args):
        self.refresh_routes()
        mappings, owners = self.normalization_state()
        dependencies, own_ids = set(), set()
        normalizations = args["normalizations"]
        seen = {}
        for n in normalizations:
            mid, target = n["mention_id"], n["entity_id"]
            mention = self.mentions.get(mid)
            if not mention or mention["type"] != "POI":
                raise Reject("NORMALIZATION_MENTION_NOT_IN_PACKAGE", "该名称出现记录不在当前包中；可直接从注册表选择地点ID。")
            self.reference(target, "POI", set(self.registry), set(), {})
            if mention.get("entity_id") and mention["entity_id"] != target:
                raise Reject("P2_ID_CONFLICT", "该名称已有地点ID；请使用原有ID，或直接从注册表选择正文对应的地点。")
            if (mid in mappings and mappings[mid] != target) or (mid in seen and seen[mid] != target):
                raise Reject("CONTRADICTORY_REFINEMENT", "同一次名称出现已有不同匹配，请先撤回原记录及依赖记录。")
            seen[mid] = target
            own_ids.add(target)
        mappings.update(seen)
        eligible = set(self.context["eligible_references"]["poi_ids"] + self.context["eligible_references"]["transport_ids"]) | set(mappings.values())
        refs = set()

        def check(identifier, kind):
            if identifier is not None:
                refs.add(identifier)
                self.reference(identifier, kind, eligible, dependencies, owners)

        route_id = args.get("route_id")
        if route_id:
            check(route_id, "ROUTE")

        def check_step(step_id, endpoint):
            if not step_id:
                return
            if step_id not in self.steps or self.steps[step_id][0] != route_id:
                raise Reject("STEP_ROUTE_MISMATCH", "step_id必须属于当前包所填route_id；不确定步骤可填null。")

        for condition_id in args.get("condition_record_ids", []):
            record = self.active.get(condition_id)
            if not record or record["tool_name"] != "submit_conditional_advice":
                raise Reject("CONDITION_RECORD_NOT_AVAILABLE", "condition_record_ids只能填写本包条件工具成功返回且未撤回的data.record_ids。",
                             "/arguments/condition_record_ids")
            dependencies.add(condition_id)

        if name == "submit_poi_facts":
            check(args["entity_id"], "POI")
            check_step(args["step_id"], args["entity_id"])
            for fact in args["facts"]:
                if fact["related_poi_id"]:
                    check(fact["related_poi_id"], "POI")
                    if fact["related_poi_id"] == args["entity_id"]:
                        raise Reject("INVALID_PARENT_MEMBERSHIP", "所属园区不能是实体自身。")
        elif name == "submit_transport_leg":
            check(args["from_entity_id"], "POI")
            check(args["to_entity_id"], "POI")
            check(args["transport_entity_id"], "TRANSPORT")
            check_step(args["from_step_id"], args["from_entity_id"])
            check_step(args["to_step_id"], args["to_entity_id"])
        elif name == "submit_route":
            submitted = args["steps"]
            if route_id:
                expected = [s["step_id"] for s in self.routes[route_id]["steps"]]
                if [s["step_id"] for s in submitted] != expected:
                    raise Reject("ROUTE_STEPS_MISMATCH", "已有线路请按用户消息中的步骤顺序提交当前包的全部步骤，复用对应step_id。", "/arguments/steps")
            elif any(s["step_id"] is not None for s in submitted):
                raise Reject("ROUTE_STEPS_MISMATCH", "新线路的每个step_id请填null，由程序生成。", "/arguments/steps")
            for step in submitted:
                for identifier in step["poi_ids"]:
                    check(identifier, "POI")
                for identifier in step["transport_ids"]:
                    check(identifier, "TRANSPORT")
                if route_id:
                    inherited = self.steps[step["step_id"]][1]
                    if not set(inherited["poi_ids"]).issubset(step["poi_ids"]) or not set(inherited["transport_ids"]).issubset(step["transport_ids"]):
                        raise Reject("ROUTE_STEPS_MISMATCH", "请保留该步骤已有的地点和交通方式ID；可加入从原文补充识别的注册ID。", "/arguments/steps")
        else:
            for target in args["targets"]:
                check(target["id"], target["kind"])
        if not own_ids.issubset(refs):
            raise Reject("UNSUPPORTED_RELATION", "normalizations目标必须被本次POI、路段、路线或条件目标使用；纯归一请用submit_poi_facts。")
        for issue_id in args["resolves_issue_ids"]:
            issue = self.issues.get(issue_id)
            if not issue or issue["status"] != "open":
                raise Reject("UNKNOWN_ISSUE", "resolves_issue_ids必须引用当前open_issue_ids中的失败请求。")
            if issue["tool_name"] != name:
                raise Reject("ISSUE_NOT_RESOLVED", "修复必须使用与失败请求相同的业务工具；放弃时请在finish_chunk忽略。")
        return dependencies

    def submit(self, name, args):
        dependencies = self.validate_business(name, args)
        stable = {k: v for k, v in args.items() if k != "resolves_issue_ids"}
        fingerprint = digest({"package": self.context["package_key"], "tool": name, "args": stable})
        duplicate = next((rid for rid, r in self.active.items() if r["fingerprint"] == fingerprint), None)
        if duplicate:
            for iid in args["resolves_issue_ids"]:
                self.issues[iid]["status"] = "resolved"
            r = self.records[duplicate]
            return self.result(code="ALREADY_ACCEPTED", message="相同内容已接纳，未重复写入。", record_ids=[duplicate],
                               leg_refs=[r["leg_ref"]] if r.get("leg_ref") else [],
                               route_ids=[r["route_id"]] if r.get("route_id") else [],
                               step_ids=[s["step_id"] for s in r.get("route_steps", [])])
        rid = "P5OBS-" + digest([fingerprint, len(self.records)])[:16]
        leg = "LEGOBS-" + digest([rid, "leg"])[:16] if name == "submit_transport_leg" else None
        self.records[rid] = {"record_id": rid, "tool_name": name, "arguments": deepcopy(args),
                             "fingerprint": fingerprint, "leg_ref": leg, "active": True,
                             "depends_on": sorted(dependencies), "package_key": self.context["package_key"],
                             "scenic_code": self.context["active_scenic_code"],
                             "provenance": deepcopy(self.package["provenance"])}
        if name == "submit_route":
            route_id = args["route_id"] or "ROUTEOBS-" + digest([rid, "route"])[:16]
            steps = [dict(s, step_id=s["step_id"] or f"{route_id}-S{i:03d}")
                     for i, s in enumerate(args["steps"], 1)]
            self.records[rid].update(route_id=route_id, route_steps=steps,
                                     created_route_id=route_id if args["route_id"] is None else None)
        for iid in args["resolves_issue_ids"]:
            self.issues[iid]["status"] = "resolved"
        return self.result(record_ids=[rid], leg_refs=[leg] if leg else [],
                           route_ids=[self.records[rid]["route_id"]] if name == "submit_route" else [],
                           step_ids=[s["step_id"] for s in self.records[rid].get("route_steps", [])])

    def retract(self, args):
        requested = set(args["record_ids"])
        for rid in requested:
            if rid not in self.records:
                raise Reject("RECORD_NOT_IN_SESSION", "只能撤回本输入包返回的record_id。")
            if not self.records[rid]["active"]:
                raise Reject("RECORD_RETRACTED", "记录已经撤回，无需重复撤回。")
        closure = set(requested)
        while True:
            expanded = closure | {rid for rid, r in self.active.items() if closure.intersection(r["depends_on"])}
            if expanded == closure:
                break
            closure = expanded
        if closure != requested:
            raise Reject("DEPENDENTS_REQUIRE_RETRACTION", "请将下列依赖记录一并加入record_ids后重试。", dependents=sorted(closure-requested))
        for rid in requested:
            self.records[rid]["active"] = False
            self.records[rid]["retraction_reason"] = args["reason"]
        return self.result(message="记录及指定依赖已撤回，原始日志保留。", record_ids=sorted(requested))

    def finish(self, args):
        route_pois = {pid for r in self.active.values() if r["tool_name"] == "submit_route"
                      for step in r["arguments"].get("steps", []) for pid in step["poi_ids"]}
        submitted_pois = {r["arguments"]["entity_id"] for r in self.active.values()
                          if r["tool_name"] == "submit_poi_facts"}
        missing = sorted(route_pois - submitted_pois)
        if missing:
            labels = [f"{pid}（{self.registry[pid]['name']}）" for pid in missing]
            raise Reject("MISSING_POI_RECORDS", "线路中这些地点尚未提交地点工具：" + "、".join(labels)
                         + "。请逐项检查属性并调用submit_poi_facts；无属性可填facts=[]，完成后再结束。")
        ignored = set(args["ignored_issue_ids"])
        if not ignored.issubset(self.open_issues):
            raise Reject("UNKNOWN_ISSUE", "ignored_issue_ids只能选择当前未解决的issue。")
        if set(self.open_issues) - ignored:
            raise Reject("OPEN_ERRORS_REMAIN", "还有失败请求：修复后提交resolves_issue_ids，或在本工具显式忽略。")
        declaration = args["declaration"]
        if (declaration == "extracted") != bool(self.active):
            raise Reject("INVALID_FINISH_DECLARATION", "有有效记录时用extracted；无记录时用no_relevant_information或only_skipped。")
        if declaration == "only_skipped" and not (ignored or any(v["status"] == "skipped" for v in self.issues.values())):
            raise Reject("INVALID_FINISH_DECLARATION", "only_skipped需要至少一个明确忽略的失败请求。")
        if declaration == "no_relevant_information" and ignored:
            raise Reject("INVALID_FINISH_DECLARATION", "存在放弃的失败请求时应使用only_skipped。")
        for iid in ignored:
            self.issues[iid]["status"] = "skipped"
        self.finished = True
        self.status = "COMPLETED_WITH_SKIPS" if any(v["status"] == "skipped" for v in self.issues.values()) else "COMPLETED"
        return self.result(message="当前包已完成。")

    def execute(self, name, arguments):
        try:
            if self.finished:
                raise Reject("INVALID_ARGUMENTS", "当前包已经结束；finish_chunk应放在本轮最后。")
            if isinstance(arguments, str):
                try:
                    args = json.loads(arguments, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                except (ValueError, TypeError):
                    raise Reject("INVALID_ARGUMENTS", "参数必须是合法JSON对象，请重新提交完整参数。")
            else:
                args = deepcopy(arguments)
            if name in BUSINESS and isinstance(args, dict):
                # Legacy mention mappings remain readable in historical logs;
                # the current provider tool schema no longer asks for them.
                args.setdefault("normalizations", [])
            errors = self.contracts.errors(name, args)
            if errors:
                path, message = errors[0]
                raise Reject("INVALID_ARGUMENTS", message, path)
            if name in BUSINESS:
                result = self.submit(name, args)
            elif name == "retract_records":
                result = self.retract(args)
            else:
                result = self.finish(args)
        except Reject as error:
            result = self.fail(name, arguments, error)
        self.events.append({"tool_name": name, "arguments": deepcopy(arguments), "result": deepcopy(result)})
        return result

    def snapshot(self):
        return {"package_key": self.context["package_key"], "status": self.status, "finished": self.finished,
                "records": list(self.records.values()), "issues": self.issues, "events": self.events}

    def restore(self, state):
        if state["package_key"] != self.context["package_key"]:
            raise ValueError("checkpoint的package_key不匹配")
        self.records = {r["record_id"]: r for r in state["records"]}
        self.issues, self.events = state["issues"], state["events"]
        self.finished, self.status = state["finished"], state["status"]
