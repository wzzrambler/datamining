"""Offline regression checks for dangerous P6 publish operations."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
PUBLISHER = ROOT / "tools/P6/publish_cleaned.py"


class P6PublishSafetyTests(unittest.TestCase):
    def test_no_assert_in_destructive_publisher(self):
        tree = ast.parse(PUBLISHER.read_text(encoding="utf-8"))
        self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.Assert)])

    def test_guard_still_raises_in_optimized_python(self):
        code = ("import importlib.util; "
                f"s=importlib.util.spec_from_file_location('pub', {str(PUBLISHER)!r}); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                "m.require(False, 'EXPECTED_BLOCK')")
        result = subprocess.run([sys.executable, "-O", "-c", code], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPECTED_BLOCK", result.stderr)

    def test_preview_does_not_connect_or_modify_database(self):
        result = subprocess.run([sys.executable, str(PUBLISHER)], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["mode"], "preview_only")
        self.assertGreater(plan["new_nodes"], 0)
        self.assertIn("No Neo4j connection", plan["note"])

    def test_cannot_replace_the_same_dataset_id(self):
        release = json.loads((ROOT / "processed/P6知识图谱/release.json").read_text(encoding="utf-8"))
        result = subprocess.run([sys.executable, str(PUBLISHER),
                                 "--replace-dataset", release["dataset"]], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to delete the same dataset", result.stderr)

    def test_apply_requires_backup_directory(self):
        result = subprocess.run([sys.executable, str(PUBLISHER), "--apply"], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--apply requires --backup", result.stderr)

    def test_verification_fields_are_measured_not_hardcoded(self):
        source = PUBLISHER.read_text(encoding="utf-8")
        for field in ("reimport_no_duplicates", "all_node_payloads_roundtrip",
                      "all_edge_payloads_roundtrip", "excluded_routes_in_database"):
            self.assertNotIn(f"{field}=True", source)
            self.assertNotIn(f"{field}=0", source)
        # 幂等性要真比一次两次导入的计数，不能直接写结论。
        self.assertIn("first_edges", source)
        self.assertIn("second_edges", source)
        self.assertIn("导入不幂等", source)


if __name__ == "__main__":
    unittest.main()
