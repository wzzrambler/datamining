"""Bounded parallel real requests, separate scenic batches, resumable checkpoints."""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/P5条件建议抽取"))
from p5_agent.budget import TokenBudget
from p5_agent.common import Settings, compact, write_json, read_json
from p5_agent.contracts import Contracts
from p5_agent.packages import Corpus, package_statistics
from p5_agent.runner import DeepSeekClient, run_package
from p5_agent.export import export_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--retry-incomplete", action="store_true")
    args = parser.parse_args()
    if Path(args.run_id).name != args.run_id or args.run_id in {".", ".."}:
        parser.error("run-id must be one directory name")
    if not 1 <= args.workers <= 12:
        parser.error("workers must be 1..12")
    settings, contracts, corpus = Settings.load(), Contracts(), Corpus()
    packages = corpus.build(TokenBudget(settings), contracts.api_tools())
    run_dir = ROOT / "processed/P5条件建议抽取/runs" / args.run_id
    write_json(run_dir / "input_statistics.json", package_statistics(packages, TokenBudget(settings), contracts.api_tools()))
    write_json(run_dir / "coverage.json", corpus.coverage)

    def execute(package):
        key = package["context"]["package_key"]
        path = run_dir / "checkpoints" / (key + ".json")
        if path.exists():
            old = read_json(path)
            if old["host"]["status"] not in {"COMPLETED", "COMPLETED_WITH_SKIPS"} and not args.retry_incomplete:
                return key, old
        return key, run_package(package, DeepSeekClient(settings), contracts, TokenBudget(settings), path, corpus.entity_scenes)

    failed = False
    for scene in ("GZCL", "JZG", "ZHCL"):
        selected = iter(p for p in packages if p["context"]["active_scenic_code"] == scene)
        print(compact({"scenic": scene, "event": "START"}), flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = set()
            for _ in range(args.workers):
                p = next(selected, None)
                if p is not None:
                    pending.add(pool.submit(execute, p))
            done_count = 0
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    key, state = future.result()
                    done_count += 1
                    print(compact({"scenic": scene, "finished": done_count, "package_key": key,
                        "status": state["host"]["status"], "rounds": state["rounds"],
                        "stop_reason": state["stop_reason"]}), flush=True)
                    if state["host"]["status"] == "FAILED":
                        failed = True  # Stop scheduling after authentication/connectivity failures.
                    if not failed:
                        p = next(selected, None)
                        if p is not None:
                            pending.add(pool.submit(execute, p))
        report = export_run(run_dir, corpus, packages)
        print(compact(report), flush=True)
        if failed:
            break

    # 批次跑完才把指针转向它。半成品批次一旦被 P6 当成正式输入，产物会静默缺包。
    summary = read_json(run_dir / "summary.json")
    counts = summary.get("status_counts") or {}
    unfinished = {k: v for k, v in counts.items() if k != "COMPLETED" and v}
    prepared, attempted = summary.get("packages_prepared"), summary.get("packages_attempted")
    short = isinstance(prepared, int) and isinstance(attempted, int) and attempted < prepared
    pointer = ROOT / "processed/P5条件建议抽取/ACTIVE_RUN"
    if failed or unfinished or short:
        print(compact({"event": "ACTIVE_RUN_NOT_SET", "run_id": args.run_id,
                       "why": "批次没跑完或还有失败包", "status_counts": counts,
                       "packages": [attempted, prepared],
                       "next": "先补包（tools/P5/recover_packages.py），再 "
                               "python -X utf8 src/P6知识图谱/p5run.py --set %s" % args.run_id}), flush=True)
    else:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(args.run_id + "\n", encoding="utf-8")
        print(compact({"event": "ACTIVE_RUN_SET", "run_id": args.run_id, "pointer": str(pointer)}), flush=True)


if __name__ == "__main__":
    main()
