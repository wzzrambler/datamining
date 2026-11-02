import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/P5条件建议抽取"))
spec = importlib.util.spec_from_file_location("recovery", ROOT / "tools/P5/recover_packages.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryCoverageTests(unittest.TestCase):
    def test_real_dense_route_fragments_preserve_all_text_and_steps(self):
        corpus, contracts = recovery.Corpus(), recovery.Contracts()
        packages = corpus.build(recovery.TokenBudget(recovery.Settings()), contracts.api_tools())
        key = "PKG-5d828f01be4d1bf82620"
        parent = next(p for p in packages if p["context"]["package_key"] == key)
        children, manifest = recovery.prepare_replacements(corpus, packages, {key})
        self.assertEqual(len(children), 4)
        self.assertTrue(all(p["context"]["package_kind"] == "route_fragment" for p in children))
        self.assertEqual([b["text"] for p in children for b in p["context"]["blocks"]],
                         [b["text"] for b in parent["context"]["blocks"]])
        self.assertTrue(manifest[key]["step_coverage_verified"])
        steps = [s["step_id"] for p in children for r in p["context"]["p3_routes"] for s in r["steps"]]
        self.assertEqual(len(steps), 15)
        self.assertEqual(len(set(steps)), 15)
