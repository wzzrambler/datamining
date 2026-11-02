from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from .budget import TokenBudget
from .common import ROOT, Settings, compact, read_json, write_json
from .contracts import Contracts
from .packages import Corpus, package_statistics


def main():
    parser = argparse.ArgumentParser(description="P5/P5+ 非流式DeepSeek工具抽取")
    parser.add_argument("command", choices=["prepare", "run", "export"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--scenic", choices=["GZCL", "JZG", "ZHCL"])
    parser.add_argument("--output", type=Path, default=ROOT / "processed/P5条件建议抽取")
    parser.add_argument("--run-id", default="dev-001")
    parser.add_argument("--limit", type=int, help="只运行前N包；不改变prepare的全量覆盖统计")
    parser.add_argument("--package-key", action="append", help="可多次指定需要联调的输入包")
    args = parser.parse_args()
    if not args.run_id or Path(args.run_id).name != args.run_id or args.run_id in {".", ".."}:
        parser.error("run-id必须是单个目录名")
    if args.limit is not None and args.limit < 1:
        parser.error("limit必须为正数")
    settings, contracts = Settings.load(args.config), Contracts()
    budget = TokenBudget(settings)
    corpus = Corpus()
    run_dir = args.output / "runs" / args.run_id
    if args.command == "export" and (run_dir / "input_manifest.json").exists():
        from .export import export_run
        print(compact(export_run(run_dir, corpus, [])))
        return
    packages = corpus.build(budget, contracts.api_tools(), args.scenic)
    input_dir = args.output / "inputs" / (args.scenic or "all")
    stats = package_statistics(packages, budget, contracts.api_tools())
    stats["paragraph_selection"] = dict(Counter(x["reason"] for x in corpus.coverage))
    stats["input_hashes"] = corpus.hashes
    write_json(input_dir / "packages.json", packages)
    write_json(input_dir / "coverage.json", corpus.coverage)
    write_json(input_dir / "statistics.json", stats)
    write_json(input_dir / "registry.json", dict(corpus.registries))
    if args.command == "prepare":
        print(compact(stats))
        return
    selected = [p for p in packages if not args.package_key or p["context"]["package_key"] in args.package_key]
    if args.package_key and set(args.package_key) - {p["context"]["package_key"] for p in selected}:
        parser.error("指定package-key不存在于当前景区输入包")
    if args.limit:
        selected = selected[:args.limit]
    run_dir = args.output / "runs" / args.run_id
    if args.command == "run":
        from .runner import DeepSeekClient, run_package
        try:
            client = DeepSeekClient(settings)
        except ValueError as error:
            parser.error(str(error))
        for i, package in enumerate(selected):
            key = package["context"]["package_key"]
            state = run_package(package, client, contracts, budget, run_dir / "checkpoints" / (key + ".json"), corpus.entity_scenes)
            print(compact({"index": i+1, "package_key": key, "kind": package["context"]["package_kind"],
                           "status": state["host"]["status"], "rounds": state["rounds"],
                           "active_records": sum(r["active"] for r in state["host"]["records"]),
                           "stop_reason": state["stop_reason"]}), flush=True)
            if state["host"]["status"] == "FAILED":
                break  # Authentication/connectivity errors should not burn a batch.
    from .export import export_run
    report = export_run(run_dir, corpus, packages)
    print(compact(report))
