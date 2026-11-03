"""Run: python -m unittest discover -s tests/P5 -v (no network or model fees)."""
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/P5条件建议抽取"))

from p5_agent.budget import TokenBudget, initial_messages
from p5_agent.common import ASSETS, Settings, compact, read_json
from p5_agent.contracts import Contracts
from p5_agent.host import Reject, ToolHost
from p5_agent.packages import Corpus
from p5_agent.runner import DeepSeekClient, run_package


def resolution():
    return {"normalizations": [], "resolves_issue_ids": []}


def conditional():
    return {"statement_kind": "conditional_advice", "condition": {
        "text": "带老人", "logic": "single", "clauses": [{"category": "audience", "text": "带老人"}]},
        "consequence_text": "建议乘坐观光车", "force": "recommended", "targets": [], **resolution()}


def finish(declaration="extracted", ignored=None):
    return {"declaration": declaration, "ignored_issue_ids": ignored or [], "note": None}


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus(ROOT)
        cls.contracts = Contracts()
        r = cls.corpus.routes[0]
        d = cls.corpus.documents[r["doc_id"]]
        spans = [p for p in d["paragraphs"] if any(cls.corpus.overlap(p, s["evidence"]) for s in r["steps"])]
        cls.package = cls.corpus.make_package(d, spans, r)

    def setUp(self):
        self.package = deepcopy(type(self).package)
        self.host = ToolHost(self.package, self.contracts, entity_scenes=self.corpus.entity_scenes)
        self.poi = self.package["context"]["eligible_references"]["poi_ids"][0]

    def poi_args(self, poi=None):
        return {"entity_id": poi or self.poi, "route_id": None, "step_id": None,
                "condition_record_ids": [],
                "facts": [{"attribute": "opening_time", "value_text": "09:00—17:00", "related_poi_id": None,
                           "condition": None, "modality": "asserted"}], **resolution()}

    def test_standalone_condition(self):
        self.assertTrue(self.host.execute("submit_conditional_advice", conditional())["ok"])
        self.assertTrue(self.host.execute("finish_chunk", finish())["ok"])

    def test_unknown_id_blocks_finish_but_can_skip(self):
        error = self.host.execute("submit_poi_facts", self.poi_args("POI-0000000000000000"))
        self.assertEqual(error["code"], "UNKNOWN_REFERENCE")
        self.assertEqual(self.host.execute("finish_chunk", finish("no_relevant_information"))["code"], "OPEN_ERRORS_REMAIN")
        self.assertTrue(self.host.execute("finish_chunk", finish("only_skipped", [error["data"]["issue_id"]]))["ok"])
        self.assertEqual(self.host.status, "COMPLETED_WITH_SKIPS")

    def test_error_repair(self):
        error = self.host.execute("submit_poi_facts", self.poi_args("POI-0000000000000000"))
        args = self.poi_args(); args["resolves_issue_ids"] = [error["data"]["issue_id"]]
        self.assertTrue(self.host.execute("submit_poi_facts", args)["ok"])
        self.assertFalse(self.host.open_issues)

    def test_idempotency_ignores_issue_metadata(self):
        first = self.host.execute("submit_poi_facts", self.poi_args())
        error = self.host.execute("submit_poi_facts", self.poi_args("POI-0000000000000000"))
        args = self.poi_args(); args["resolves_issue_ids"] = [error["data"]["issue_id"]]
        repeat = self.host.execute("submit_poi_facts", args)
        self.assertEqual(repeat["code"], "ALREADY_ACCEPTED")
        self.assertEqual(first["data"]["record_ids"], repeat["data"]["record_ids"])
        self.assertEqual(len(self.host.active), 1)

    def test_scene_mismatch(self):
        other = next(e for e, scene in self.corpus.entity_scenes.items() if scene != self.package["context"]["active_scenic_code"])
        self.assertEqual(self.host.execute("submit_poi_facts", self.poi_args(other))["code"], "SCENIC_SCOPE_MISMATCH")

    def test_whole_scenic_registry_can_be_used_without_local_mentions(self):
        absent = next(x["id"] for x in self.package["context"]["scene_poi_registry"]
                      if x["id"] not in self.package["context"]["eligible_references"]["poi_ids"])
        result = self.host.execute("submit_poi_facts", self.poi_args(absent))
        self.assertTrue(result["ok"])
        record = self.host.records[result["data"]["record_ids"][0]]
        self.assertEqual(record["reference_scopes"][absent], "scene_registry")

    def test_reference_scope_separates_mentioned_ids_from_registry_only(self):
        mentioned = next(x["id"] for x in self.package["context"]["scene_poi_registry"]
                         if x["id"] in self.host.local_entity_ids)
        only_registry = next(x["id"] for x in self.package["context"]["scene_poi_registry"]
                             if x["id"] not in self.host.local_entity_ids)
        for poi, expected in ((mentioned, "local_mention"), (only_registry, "scene_registry")):
            result = self.host.execute("submit_poi_facts", self.poi_args(poi))
            record = self.host.records[result["data"]["record_ids"][0]]
            self.assertEqual(record["reference_scopes"][poi], expected)

    def test_reference_type_is_checked_even_for_a_registered_scenic_id(self):
        transport_id = self.package["context"]["scene_transport_registry"][0]["id"]
        with self.assertRaises(Reject) as error:
            self.host.reference(transport_id, "POI", set())
        self.assertEqual(error.exception.code, "REFERENCE_TYPE_MISMATCH")

    def test_p2_id_cannot_be_overwritten(self):
        m = next(m for m in self.package["context"]["local_mentions"] if m["type"] == "POI" and m["entity_id"] != self.poi and m["entity_id"])
        args = self.poi_args(); args["normalizations"] = [{"mention_id": m["mention_id"], "entity_id": self.poi}]
        self.assertEqual(self.host.execute("submit_poi_facts", args)["code"], "P2_ID_CONFLICT")

    def test_unmapped_alias_expands_context_atomically(self):
        target = next(x["id"] for x in self.package["context"]["scene_poi_registry"] if x["id"] not in self.package["context"]["eligible_references"]["poi_ids"])
        self.host.mentions["M-test-alias"] = {"mention_id": "M-test-alias", "type": "POI", "entity_id": None}
        args = self.poi_args(target); args["facts"] = []
        args["normalizations"] = [{"mention_id": "M-test-alias", "entity_id": target}]
        self.assertTrue(self.host.execute("submit_poi_facts", args)["ok"])
        self.assertTrue(self.host.execute("submit_poi_facts", self.poi_args(target))["ok"])

    def test_mapping_rolls_back_with_invalid_relation(self):
        self.host.mentions["M-test-alias"] = {"mention_id": "M-test-alias", "type": "POI", "entity_id": None}
        args = self.poi_args(); args["normalizations"] = [{"mention_id": "M-test-alias", "entity_id": self.poi}]
        args["facts"][0].update(attribute="parent_park", related_poi_id=self.poi)
        self.assertEqual(self.host.execute("submit_poi_facts", args)["code"], "INVALID_PARENT_MEMBERSHIP")
        self.assertEqual(self.host.normalization_state(), ({}, {}))

    def test_dependent_retraction_closure(self):
        first = self.host.execute("submit_poi_facts", self.poi_args())["data"]["record_ids"][0]
        self.host.records[first]["leg_ref"] = "LEGOBS-0000000000000001"
        a = conditional(); a["targets"] = [{"kind": "LEG", "id": "LEGOBS-0000000000000001"}]
        second = self.host.execute("submit_conditional_advice", a)["data"]["record_ids"][0]
        rejected = self.host.execute("retract_records", {"record_ids": [first], "reason": "修正"})
        self.assertEqual(rejected["code"], "DEPENDENTS_REQUIRE_RETRACTION")
        self.assertIn(second, rejected["data"]["dependent_record_ids"])
        self.assertTrue(self.host.execute("retract_records", {"record_ids": [first, second], "reason": "修正"})["ok"])
        self.assertFalse(self.host.active)

    def test_schema_rejects_missing_condition_and_source_fields(self):
        a = conditional(); a["condition"] = None
        self.assertEqual(self.host.execute("submit_conditional_advice", a)["code"], "INVALID_ARGUMENTS")
        a = self.poi_args(); a["source_url"] = "https://invalid/"
        self.assertEqual(self.host.execute("submit_poi_facts", a)["code"], "INVALID_ARGUMENTS")

    def test_malformed_json(self):
        self.assertEqual(self.host.execute("submit_poi_facts", "{broken")["code"], "INVALID_ARGUMENTS")
        self.assertEqual(self.host.execute("submit_poi_facts", "NaN")["code"], "INVALID_ARGUMENTS")

    def test_route_and_step_validation(self):
        args = self.poi_args(); args["route_id"] = "invented-route"
        self.assertEqual(self.host.execute("submit_poi_facts", args)["code"], "ROUTE_NOT_IN_PACKAGE")
        route = self.package["context"]["p3_routes"][0]
        args["route_id"] = route["route_id"]; args["step_id"] = "invented-step"
        self.assertEqual(self.host.execute("submit_poi_facts", args)["code"], "STEP_ROUTE_MISMATCH")

    def test_control_errors_do_not_create_blocking_issues(self):
        self.host.execute("finish_chunk", finish())
        self.assertFalse(self.host.open_issues)
        self.assertTrue(self.host.execute("finish_chunk", finish("no_relevant_information"))["ok"])

    def test_transport_leg_direction_and_rule_dependency(self):
        pois = self.package["context"]["eligible_references"]["poi_ids"]
        args = {"from_entity_id": pois[0], "to_entity_id": pois[1], "transport_entity_id": None,
                "condition_record_ids": [],
                "route_id": None, "from_step_id": None, "to_step_id": None,
                "assertion_kind": "visitor_movement", "service_name_text": None,
                "facts": [{"attribute": "ride_duration", "value_text": "20分钟", "condition": None, "modality": "asserted"}],
                **resolution()}
        result = self.host.execute("submit_transport_leg", args)
        self.assertTrue(result["ok"])
        a = conditional(); a["targets"] = [{"kind": "LEG", "id": result["data"]["leg_refs"][0]}]
        result2 = self.host.execute("submit_conditional_advice", a)
        self.assertTrue(result2["ok"])
        self.assertIn(result["data"]["record_ids"][0], self.host.records[result2["data"]["record_ids"][0]]["depends_on"])
        args["directionality"] = "one_way"
        self.assertEqual(self.host.execute("submit_transport_leg", args)["code"], "INVALID_ARGUMENTS")

    def test_retry_bound_guides_to_skip(self):
        for _ in range(3):
            result = self.host.execute("submit_poi_facts", self.poi_args("POI-0000000000000000"))
        self.assertFalse(result["errors"][0]["retryable"])
        self.assertNotIn("retry_arguments", result["next_actions"])
        self.assertEqual(len(self.host.open_issues), 1)

    def route_args(self):
        route = self.package["context"]["p3_routes"][0]
        return {"route_id": route["route_id"], "steps": [
            {"step_id": s["step_id"], "text": s["evidence_text"],
             "poi_ids": s["poi_ids"], "transport_ids": s["transport_ids"]} for s in route["steps"]],
                "summary": "依次游览正文中的地点", "recommended_audiences": [],
                "audience_basis": "not_stated", "condition_record_ids": [], "resolves_issue_ids": []}

    def test_condition_first_then_entity_link_and_retraction_dependency(self):
        condition_id = self.host.execute("submit_conditional_advice", conditional())["data"]["record_ids"][0]
        args = self.poi_args(); args["condition_record_ids"] = [condition_id]
        result = self.host.execute("submit_poi_facts", args)
        self.assertTrue(result["ok"])
        rid = result["data"]["record_ids"][0]
        self.assertIn(condition_id, self.host.records[rid]["depends_on"])
        error = self.host.execute("retract_records", {"record_ids": [condition_id], "reason": "修正条件"})
        self.assertEqual(error["code"], "DEPENDENTS_REQUIRE_RETRACTION")
        self.assertIn(rid, error["data"]["dependent_record_ids"])

    def test_condition_reference_rejects_wrong_tool_retracted_or_unknown_record(self):
        poi_rid = self.host.execute("submit_poi_facts", self.poi_args())["data"]["record_ids"][0]
        cid = self.host.execute("submit_conditional_advice", conditional())["data"]["record_ids"][0]
        self.host.execute("retract_records", {"record_ids": [cid], "reason": "修正"})
        for invalid in [poi_rid, cid, "P5OBS-0000000000000000"]:
            args = self.poi_args(); args["condition_record_ids"] = [invalid]
            self.assertEqual(self.host.execute("submit_poi_facts", args)["code"], "CONDITION_RECORD_NOT_AVAILABLE")

    def test_poi_with_no_properties_can_preserve_an_occurrence(self):
        args = self.poi_args(); args.pop("normalizations"); args["facts"] = []
        self.assertTrue(self.host.execute("submit_poi_facts", args)["ok"])

    def test_route_sequence_preserves_repeated_visits_and_rejects_missing_steps(self):
        args = self.route_args()
        result = self.host.execute("submit_route", args)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["step_ids"], [s["step_id"] for s in args["steps"]])
        args["steps"] = args["steps"][:-1]
        self.assertEqual(self.host.execute("submit_route", args)["code"], "ROUTE_STEPS_MISMATCH")

    def test_new_route_allocates_ids_and_keeps_repeated_visits(self):
        args = self.route_args(); args["route_id"] = None
        args["steps"] = [{"step_id": None, "text": "到达同一地点", "poi_ids": [self.poi], "transport_ids": []} for _ in range(2)]
        result = self.host.execute("submit_route", args)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["data"]["route_ids"][0].startswith("ROUTEOBS-"))
        self.assertEqual(len(set(result["data"]["step_ids"])), 2)
        again = self.host.execute("submit_route", args)
        self.assertEqual(result["data"]["route_ids"], again["data"]["route_ids"])
        restored = ToolHost(self.package, self.contracts); restored.restore(self.host.snapshot())
        poi_args = self.poi_args(); poi_args["route_id"] = result["data"]["route_ids"][0]
        poi_args["step_id"] = result["data"]["step_ids"][0]
        linked = restored.execute("submit_poi_facts", poi_args)
        self.assertTrue(linked["ok"], linked)
        self.assertIn(result["data"]["record_ids"][0], restored.records[linked["data"]["record_ids"][0]]["depends_on"])

    def test_finish_requires_poi_records_for_submitted_route(self):
        args = self.route_args()
        self.assertTrue(self.host.execute("submit_route", args)["ok"])
        self.assertEqual(self.host.execute("finish_chunk", finish())["code"], "MISSING_POI_RECORDS")
        for pid in {p for s in args["steps"] for p in s["poi_ids"]}:
            poi = self.poi_args(pid); poi["facts"] = []
            self.assertTrue(self.host.execute("submit_poi_facts", poi)["ok"])
        self.assertTrue(self.host.execute("finish_chunk", finish())["ok"])

    def test_provider_tools_have_parameter_guidance_without_local_mention_inputs(self):
        tools = self.contracts.api_tools()
        for tool in tools:
            self.assertNotIn("normalizations", tool["function"]["parameters"]["properties"])
            for value in tool["function"]["parameters"]["properties"].values():
                self.assertTrue(value.get("description"))

    def test_messages_are_raw_text_and_optional_routes_without_recognizer_time_entries(self):
        messages = initial_messages(self.package["context"])
        self.assertTrue(messages[1]["content"].startswith("当前原文包："))
        self.assertIn("相关线路及步骤：", messages[1]["content"])
        for word in ["local_mentions", "eligible_references", "mention_id", "clock_texts", "duration_texts", "P2", "P3", "directionality"]:
            self.assertNotIn(word, messages[0]["content"] + messages[1]["content"])
        plain = deepcopy(self.package["context"]); plain["p3_routes"] = []
        self.assertNotIn("相关线路及步骤：", initial_messages(plain)[1]["content"])

    def test_reply_schema(self):
        from jsonschema import Draft202012Validator
        validator = Draft202012Validator(read_json(ASSETS / "tool_result.schema.json"))
        for name, args in [("submit_conditional_advice", conditional()), ("submit_poi_facts", self.poi_args("POI-0000000000000000")),
                           ("finish_chunk", finish())]:
            validator.validate(self.host.execute(name, args))


class ScriptedClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        return next(self.replies)


def reply(*calls, reason="tool_calls"):
    return {"message": {"role": "assistant", "content": None,
                        "tool_calls": [{"id": f"call-{i}", "type": "function", "function": {"name": name, "arguments": compact(args)}}
                                       for i, (name, args) in enumerate(calls)]},
            "finish_reason": reason, "usage": {"prompt_tokens": 7000, "completion_tokens": 150, "total_tokens": 7150}}


class LoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AgentTests.setUpClass()
        cls.package, cls.contracts = AgentTests.package, AgentTests.contracts

    def run_loop(self, client, settings=None):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            path = Path(folder) / "checkpoint.json"
            state = run_package(self.package, client, self.contracts, TokenBudget(settings or Settings()), path)
            self.assertEqual(state, read_json(path))
            # Completed checkpoint resumes without another model call.
            if state["host"]["finished"]:
                run_package(self.package, client, self.contracts, TokenBudget(settings or Settings()), path)
            return state

    def test_tool_cycle_and_resume(self):
        client = ScriptedClient([reply(("submit_conditional_advice", conditional())), reply(("finish_chunk", finish()))])
        state = self.run_loop(client)
        self.assertEqual(state["host"]["status"], "COMPLETED")
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(state["host"]["records"]), 1)

    def test_truncated_response_has_no_side_effects(self):
        state = self.run_loop(ScriptedClient([reply(("submit_conditional_advice", conditional()), reason="length"),
                                              reply(("finish_chunk", finish("no_relevant_information")))]))
        self.assertFalse(state["host"]["records"])
        self.assertEqual(state["host"]["status"], "COMPLETED")

    def test_round_exhaustion_is_not_success(self):
        state = self.run_loop(ScriptedClient([reply(reason="stop")]), Settings(max_rounds=1))
        self.assertEqual(state["host"]["status"], "EXHAUSTED")
        self.assertEqual(state["stop_reason"], "MAX_ROUNDS")

    def test_finish_before_business_is_rejected(self):
        state = self.run_loop(ScriptedClient([reply(("finish_chunk", finish("no_relevant_information")),
                                                  ("submit_conditional_advice", conditional())),
                                             reply(("finish_chunk", finish()))]))
        self.assertEqual(len(state["host"]["records"]), 1)
        self.assertEqual(state["host"]["status"], "COMPLETED")
        self.assertEqual(state["host"]["events"][0]["result"]["code"], "INVALID_FINISH_DECLARATION")

    def test_compaction_preserves_ledger_and_stops_if_still_too_large(self):
        from p5_agent.runner import compact_messages
        host = ToolHost(self.package, self.contracts)
        host.execute("submit_conditional_advice", conditional())
        messages = compact_messages(self.package, host)
        ledger = json.loads(messages[-1]["content"])
        self.assertEqual(ledger["accepted"][0]["arguments"]["condition"]["text"], "带老人")
        self.assertIn(ledger["accepted"][0]["record_id"], host.records)

    def test_checkpoint_refuses_changed_inputs(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            checkpoint = Path(folder) / "state.json"
            client = ScriptedClient([reply(("finish_chunk", finish("no_relevant_information")))])
            run_package(self.package, client, self.contracts, TokenBudget(Settings()), checkpoint)
            changed = deepcopy(self.package); changed["context"]["heading"] = "changed"
            with self.assertRaisesRegex(ValueError, "输入、配置或实现"):
                run_package(changed, client, self.contracts, TokenBudget(Settings()), checkpoint)

    def test_api_error_is_sanitized(self):
        class Failing:
            def complete(self, messages, tools):
                raise ConnectionError("SECRET-should-not-appear")
        state = self.run_loop(Failing())
        self.assertEqual(state["host"]["status"], "FAILED")
        self.assertNotIn("SECRET", compact(state))

    def test_sdk_uses_requested_model_non_streaming_and_credentials(self):
        captured = {}
        class Message:
            content, tool_calls = "", []
        class Choice:
            message, finish_reason = Message(), "stop"
        class Response:
            choices, usage, id, model = [Choice()], None, "test", "deepseek-flash"
        class FakeSDK:
            def __init__(self, **kwargs):
                captured["constructor"] = kwargs
                self.chat = self.completions = self
            def create(self, **kwargs):
                captured["request"] = kwargs
                return Response()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_BASE_URL": "https://example.invalid/v1"}), patch("openai.OpenAI", FakeSDK):
            DeepSeekClient(Settings()).complete([], [])
        self.assertEqual(captured["request"]["model"], "deepseek-flash")
        self.assertIs(captured["request"]["stream"], False)
        self.assertEqual(captured["request"]["max_tokens"], 4096)
        self.assertEqual(captured["request"]["extra_body"], {"thinking": {"type": "disabled"}})


class CorpusExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus(ROOT)
        cls.contracts = Contracts()
        cls.budget = TokenBudget(Settings())
        cls.packages = cls.corpus.build(cls.budget, cls.contracts.api_tools())

    def test_all_prepared_contexts_follow_schema(self):
        from jsonschema import Draft202012Validator
        validator = Draft202012Validator(read_json(ASSETS / "context.schema.json"))
        for package in self.packages:
            validator.validate(package["context"])

    def test_full_release_rebuild_preserves_production_graph(self):
        import shutil
        from p5_agent.export import export_run
        pointer = ROOT / 'processed/P5条件建议抽取/ACTIVE_RUN'
        source = ROOT / 'processed/P5条件建议抽取/runs' / pointer.read_text('utf-8').strip()
        parent = ROOT / '.test_tmp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent, prefix='p5_release_') as temp:
            output = Path(temp)
            for name in ('raw_observations.json', 'summary.json', 'input_manifest.json'):
                shutil.copy2(source / name, output / name)
            export_run(output, self.corpus, [])
            self.assertEqual(read_json(output / 'integrated/graph.json'),
                             read_json(source / 'integrated/graph.json'))

    def test_selection_is_accounted_for_every_paragraph(self):
        expected = {(d["doc_id"], p["i"]) for d in self.corpus.documents.values() for p in d["paragraphs"]}
        actual = [(r["doc_id"], r["para_id"]) for r in self.corpus.coverage]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(expected))
        # Every selected paragraph's entire character interval is really sent,
        # even where a route occupies just part of that paragraph.
        intervals = {}
        for package in self.packages:
            for block in package["provenance"]["blocks"]:
                intervals.setdefault((package["provenance"]["doc_id"], block["para_id"]), []).append((block["start"], block["end"]))
        for row in self.corpus.coverage:
            key = row["doc_id"], row["para_id"]
            if row["reason"] == "not_selected":
                self.assertNotIn(key, intervals)
                continue
            cursor = row["start"]
            for a, b in sorted(intervals[key]):
                self.assertLessEqual(a, cursor)
                cursor = max(cursor, b)
            self.assertEqual(cursor, row["end"])

    def test_all_p3_steps_preserved(self):
        expected = {s["step_id"] for r in self.corpus.routes for s in r["steps"]}
        actual = {s["step_id"] for p in self.packages for r in p["context"]["p3_routes"] for s in r["steps"]}
        self.assertEqual(actual, expected)
        self.assertTrue(all(self.budget.fits(initial_messages(p["context"]), self.contracts.api_tools(), initial=True) for p in self.packages))

    def test_model_context_excludes_source_metadata(self):
        for package in self.packages:
            content = compact(package["context"])
            for key in ['"source_url":', '"author":', '"input_hashes":', '"start":', '"end":']:
                self.assertNotIn(key, content)
            for block, evidence in zip(package["context"]["blocks"], package["provenance"]["blocks"]):
                doc = self.corpus.documents[package["provenance"]["doc_id"]]
                self.assertEqual(block["text"], doc["content"][evidence["start"]:evidence["end"]])

    def test_export_shared_pois_routes_and_standalone_conditions(self):
        from p5_agent.export import export_run
        package = self.packages[0]
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            run_dir = Path(folder)
            client = ScriptedClient([reply(("submit_conditional_advice", conditional())), reply(("finish_chunk", finish()))])
            run_package(package, client, self.contracts, self.budget, run_dir / "checkpoints/test.json")
            report = export_run(run_dir, self.corpus, self.packages)
            graph = read_json(run_dir / "integrated/graph.json")
            ids = {n["id"] for n in graph["nodes"]}
            self.assertTrue(all(e["source"] in ids and e["target"] in ids for e in graph["edges"]))
            self.assertEqual(report["p3_routes_inherited"], len(self.corpus.routes))
            self.assertEqual(report["p5_complete_conditions"], 1)
            self.assertEqual(read_json(run_dir / "integrated/conditions.json")[0]["targets"], [])
            self.assertTrue(any(e["type"] == "PRECEDES" for e in graph["edges"]))
            self.assertFalse(any(e["type"] == "USES_LEG" for e in graph["edges"]))

    def test_time_observations_keep_scopes_and_summaries_belong_to_p6(self):
        from p5_agent.export import export_run
        package = self.packages[0]
        context = package["context"]
        poi = context["eligible_references"]["poi_ids"][0]
        args = {"entity_id": poi, "route_id": None, "step_id": None,
                "condition_record_ids": [],
                "facts": [{"attribute": "opening_time", "value_text": "09:00—17:00", "related_poi_id": None,
                           "condition": None, "modality": "asserted"}], **resolution()}
        route_args = deepcopy(args); route_args["route_id"] = context["p3_routes"][0]["route_id"]
        route_args["facts"][0].update(attribute="arrival_time", value_text="10:00")
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            run_dir = Path(folder)
            client = ScriptedClient([reply(("submit_poi_facts", args), ("submit_poi_facts", route_args), ("finish_chunk", finish()))])
            run_package(package, client, self.contracts, self.budget, run_dir / "checkpoints/test.json")
            export_run(run_dir, self.corpus, self.packages)
            graph = read_json(run_dir / "integrated/graph.json")
            facts = [n["properties"] for n in graph["nodes"] if n["label"] == "FACT"]
            self.assertEqual({f["attribute"] for f in facts}, {"opening_time", "arrival_time"})
            self.assertTrue(any(f["route_id"] is None for f in facts))
            self.assertTrue(any(f["route_id"] is not None for f in facts))
            self.assertFalse(any(n["label"] == "TIME_SUMMARY" for n in graph["nodes"]))
            self.assertFalse((run_dir / "integrated/time_observation_unions.json").exists())
            self.assertFalse((run_dir / "integrated/entity_time_summaries.json").exists())

    def test_release_export_without_request_checkpoints_preserves_graph_and_usage(self):
        from p5_agent.common import write_json
        from p5_agent.export import export_run
        package = self.packages[0]
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            run_dir = Path(folder)
            checkpoint = run_dir / "checkpoints/test.json"
            client = ScriptedClient([reply(("submit_conditional_advice", conditional())), reply(("finish_chunk", finish()))])
            run_package(package, client, self.contracts, self.budget, checkpoint)
            original = export_run(run_dir, self.corpus, self.packages)
            graph = read_json(run_dir / "integrated/graph.json")
            write_json(run_dir / "input_manifest.json", {"effective_packages": [{
                "package_key": package["context"]["package_key"], "package_kind": package["context"]["package_kind"], "status": "COMPLETED"}]})
            checkpoint.rename(run_dir / "archived_checkpoint.json")
            result = export_run(run_dir, self.corpus, [])
            self.assertEqual(read_json(run_dir / "integrated/graph.json"), graph)
            self.assertEqual(result["usage"], original["usage"])
            self.assertEqual(result["status_counts"], {"COMPLETED": 1})
            manifest = read_json(run_dir / "input_manifest.json")
            manifest["input_hashes"] = {"p1": "changed"}
            write_json(run_dir / "input_manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "哈希"):
                export_run(run_dir, self.corpus, [])

    def test_export_condition_first_links_poi_leg_and_new_route_to_same_rule(self):
        from p5_agent.common import write_json
        from p5_agent.export import export_run
        package = self.packages[0]
        host = ToolHost(package, self.contracts)
        pois = package["context"]["scene_poi_registry"][:2]
        mode = package["context"]["scene_transport_registry"][0]["id"]
        cid = host.execute("submit_conditional_advice", conditional())["data"]["record_ids"][0]
        common = {"condition_record_ids": [cid], "resolves_issue_ids": []}
        self.assertTrue(host.execute("submit_poi_facts", {"entity_id": pois[0]["id"], "route_id": None,
            "step_id": None, "facts": [], **common})["ok"])
        leg = host.execute("submit_transport_leg", {"from_entity_id": pois[0]["id"], "to_entity_id": pois[1]["id"],
            "transport_entity_id": mode, "route_id": None, "from_step_id": None, "to_step_id": None,
            "assertion_kind": "route_recommendation", "service_name_text": None, "facts": [], **common})
        self.assertTrue(leg["ok"], leg)
        route = host.execute("submit_route", {"route_id": None, "steps": [
            {"step_id": None, "text": p["name"], "poi_ids": [p["id"]], "transport_ids": [mode]}
            for p in [pois[0], pois[1], pois[0]]],
            "summary": "游览两个地点并返回起点", "recommended_audiences": [], "audience_basis": "not_stated", **common})
        self.assertTrue(route["ok"], route)
        self.assertTrue(host.execute("submit_poi_facts", {"entity_id": pois[1]["id"], "route_id": None,
            "step_id": None, "facts": [], "condition_record_ids": [], "resolves_issue_ids": []})["ok"])
        self.assertTrue(host.execute("finish_chunk", finish())["ok"])
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            run_dir = Path(folder)
            write_json(run_dir / "checkpoints/test.json", {"host": host.snapshot(), "usage": []})
            report = export_run(run_dir, self.corpus, self.packages)
            rules = read_json(run_dir / "integrated/conditions.json")
            self.assertEqual(len(rules), 1)
            self.assertEqual({t["kind"] for t in rules[0]["targets"]}, {"POI", "LEG", "ROUTE"})
            self.assertEqual(report["new_routes_extracted"], 1)
            new_route = next(r for r in read_json(run_dir / "integrated/routes.json") if r["route_id"] == route["data"]["route_ids"][0])
            self.assertEqual(len(new_route["steps"]), 3)
            self.assertEqual(new_route["rule_ids"], [rules[0]["rule_id"]])
            leg_data = read_json(run_dir / "integrated/transport_legs.json")[0]
            self.assertEqual(leg_data["recommendation_rule_ids"], [rules[0]["rule_id"]])
            poi_data = next(p for p in read_json(run_dir / "integrated/entities.json") if p["id"] == pois[0]["id"])
            self.assertEqual(len(poi_data["observation_ids"]), 1)
            graph = read_json(run_dir / "integrated/graph.json")
            ids = {n["id"] for n in graph["nodes"]}
            self.assertTrue(all(e["source"] in ids and e["target"] in ids for e in graph["edges"]))
            self.assertEqual({e["target"] for e in graph["edges"] if e["type"] == "APPLIES_TO"},
                             {t["id"] for t in rules[0]["targets"]})
            self.assertTrue(any(e["type"] == "MENTIONS_MODE" and e["target"] == mode for e in graph["edges"]))
            route_node = next(n for n in graph["nodes"] if n["id"] == new_route["route_id"])
            self.assertEqual(route_node["properties"]["steps"][0]["poi_ids"], [pois[0]["id"]])
            self.assertIn("recommended_audiences", route_node["properties"])
            self.assertTrue(any(n["label"] == "OBSERVATION" for n in graph["nodes"]))
            self.assertTrue(any(e["type"] == "SUPPORTED_BY" for e in graph["edges"]))

    def test_export_original_smoke_checkpoints_still_supported(self):
        from p5_agent.common import write_json
        from p5_agent.export import export_run
        original = ROOT / "tests/P5/fixtures/legacy_smoke"
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            run_dir = Path(folder)
            for checkpoint in original.glob("*.json"):
                write_json(run_dir / "checkpoints" / checkpoint.name, read_json(checkpoint))
            report = export_run(run_dir, self.corpus, self.packages)
            self.assertEqual(report["packages_attempted"], 3)
            self.assertEqual(report["active_completed_observations"], 4)
            self.assertEqual(report["conditions"], 2)


if __name__ == "__main__":
    unittest.main()
