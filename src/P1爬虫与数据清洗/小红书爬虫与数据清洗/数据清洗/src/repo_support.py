from pathlib import Path
import importlib.util

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "paths.py").is_file())
_spec = importlib.util.spec_from_file_location("datamining_paths", ROOT / "src" / "paths.py")
paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paths)


def portable(value):
    if isinstance(value, dict):
        return {k: portable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [portable(v) for v in value]
    if isinstance(value, str):
        prefix = str(ROOT).replace("\\", "/")
        normalized = value.replace("\\", "/")
        if normalized.startswith(prefix + "/"):
            return normalized[len(prefix) + 1:]
    return value
