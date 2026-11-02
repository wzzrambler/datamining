"""Explicit recovery by smaller original-text packages; never rewrite failed logs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/P5条件建议抽取"))
from p5_agent.budget import TokenBudget
from p5_agent.common import Settings, compact, read_json, write_json
from p5_agent.contracts import Contracts
from p5_agent.packages import Corpus
from p5_agent.runner import DeepSeekClient, run_package
from p5_agent.export import export_run

COMPLETE = {"COMPLETED", "COMPLETED_WITH_SKIPS"}


def prepare_replacements(corpus, packages, failed_keys):
    originals = {p["context"]["package_key"]: p for p in packages}
    children, manifest = [], {}
    for key in sorted(failed_keys):
        parent = originals[key]
        doc = corpus.documents[parent["provenance"]["doc_id"]]
        route_id = next((r["route_id"] for r in parent["context"]["p3_routes"]), None)
        route = next((r for r in corpus.routes if r["route_id"] == route_id), None)
        spans = [{"start": b["start"], "end": b["end"], "i": b["para_id"]} for b in parent["provenance"]["blocks"]]
        parts = [corpus.make_package(doc, spans[i:i+4], route, fragment=bool(route)) for i in range(0, len(spans), 4)]
        expected = sorted((s["i"], s["start"], s["end"]) for s in spans)
        actual = sorted((b["para_id"], b["start"], b["end"]) for p in parts for b in p["provenance"]["blocks"])
        assert actual == expected, "Recovery must preserve all selected text exactly"
        expected_steps = {s["step_id"] for r in parent["context"]["p3_routes"] for s in r["steps"]}
        actual_steps = {s["step_id"] for p in parts for r in p["context"]["p3_routes"] for s in r["steps"]}
        assert actual_steps == expected_steps, "Recovery must preserve route steps"
        manifest[key] = {"replacement_keys": [p["context"]["package_key"] for p in parts],
                         "text_coverage_verified": True, "step_coverage_verified": True}
        children.extend(parts)
    return children, manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--recovery-run", required=True)
    parser.add_argument("--combine-run")
    parser.add_argument("--combine-only", action="store_true", help="Only read completed recovery logs; no model calls")
    args = parser.parse_args()
    settings, corpus, contracts = Settings.load(), Corpus(), Contracts()
    packages = corpus.build(TokenBudget(settings), contracts.api_tools())
    source = ROOT / "processed/P5条件建议抽取/runs" / args.source_run
    recovery = source.parent / args.recovery_run
    source_states = {p.stem: read_json(p) for p in (source / "checkpoints").glob("*.json")}
    if any(r["provenance"]["input_hashes"] != corpus.hashes for s in source_states.values() for r in s["host"]["records"]):
        raise RuntimeError("Source corpus changed since the real run; refusing recovery/consolidation")
    failed = {key for key, s in source_states.items() if s["host"]["status"] not in COMPLETE | {"RUNNING"}}
    children, manifest = prepare_replacements(corpus, packages, failed)
    write_json(recovery / "replacement_manifest.json", manifest)
    write_json(recovery / "packages.json", children)
    # More rounds only for recovery; context/model/output limits unchanged.
    extended = replace(settings, max_rounds=32, max_tool_calls=128)

    def execute(package):
        key = package["context"]["package_key"]
        state = run_package(package, DeepSeekClient(extended), contracts, TokenBudget(extended),
                            recovery / "checkpoints" / (key + ".json"), corpus.entity_scenes)
        print(compact({"package_key": key, "status": state["host"]["status"], "rounds": state["rounds"], "stop_reason": state["stop_reason"]}), flush=True)
        return key, state

    if args.combine_only:
        recovered = {p["context"]["package_key"]: read_json(recovery / "checkpoints" / (p["context"]["package_key"] + ".json")) for p in children}
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            recovered = dict(pool.map(execute, children))
    export_run(recovery, corpus, children)
    if any(s["host"]["status"] not in COMPLETE for s in recovered.values()):
        raise RuntimeError("Some recovery fragments remain incomplete; original failure logs retained")
    if not args.combine_run:
        return
    expected_keys = {p["context"]["package_key"] for p in packages}
    if set(source_states) != expected_keys or any(s["host"]["status"] == "RUNNING" for s in source_states.values()):
        raise RuntimeError("Cannot consolidate before all original packages have been attempted")
    combined = source.parent / args.combine_run
    if (combined / "checkpoints").exists():
        raise RuntimeError("Use a new combine-run to avoid stale checkpoint inclusion")
    selected = {key: s for key, s in source_states.items() if key not in failed}
    if set(selected) & set(recovered):
        raise RuntimeError("Recovery package key collides with an unrelated completed package")
    selected.update(recovered)
    for key, state in selected.items():
        state = dict(state, aggregation_origin=args.recovery_run if key in recovered else args.source_run)
        write_json(combined / "checkpoints" / (key + ".json"), state)
    effective_packages = [p for p in packages if p["context"]["package_key"] not in failed] + children
    report = export_run(combined, corpus, effective_packages)
    report["usage_selected_completed"] = report["usage"]
    report["usage"] = {k: sum(u.get(k, 0) for s in list(source_states.values()) + list(recovered.values()) for u in s["usage"])
                       for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    report["original_packages_covered"] = len(expected_keys)
    report["original_failed_packages_replaced"] = len(failed)
    report["recovery_fragments"] = len(children)
    write_json(combined / "summary.json", report)
    write_json(combined / "aggregation_manifest.json", {"source_run": args.source_run, "recovery_run": args.recovery_run,
        "original_packages_covered": len(expected_keys), "replacement_manifest": manifest,
        "all_selected_text_and_steps_covered": True, "original_failure_logs_preserved": True})
    write_json(combined / "input_statistics.json", {"packages": len(effective_packages), "original_packages": len(packages)})
    print(compact(report))


if __name__ == "__main__":
    main()
