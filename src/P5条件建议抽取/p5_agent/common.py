from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parents[1]
ASSETS = BASE / "assets"
sys.path.insert(0, str(ROOT / 'src'))
from artifact_integrity import file_sha256, matches_sha256


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: Any) -> str:
    return hashlib.sha256(compact(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return file_sha256(path)


def write_json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode('utf-8'))
    os.replace(tmp, path)


@dataclass(frozen=True)
class Settings:
    model: str = "deepseek-flash"
    context_limit: int = 30000
    output_tokens: int = 4096
    safety_tokens: int = 1024
    history_reserve: int = 6000
    max_rounds: int = 12
    max_tool_calls: int = 48
    max_same_error: int = 2
    timeout_seconds: int = 120
    api_retries: int = 2
    temperature: float = 0.1
    # Non-streaming is independent from thinking. Disable thinking explicitly to
    # keep a small, observable context and permit tool_choice=required.
    thinking: bool = False

    def __post_init__(self):
        if not 0 < self.output_tokens < self.context_limit:
            raise ValueError("output_tokens必须小于context_limit")
        if min(self.max_rounds, self.max_tool_calls, self.max_same_error) < 1:
            raise ValueError("循环预算必须为正整数")
        if min(self.safety_tokens, self.history_reserve) < 0:
            raise ValueError("预留预算不能为负数")
        if self.context_limit - self.output_tokens - self.safety_tokens - self.history_reserve < 1000:
            raise ValueError("初始上下文预算不足")

    @classmethod
    def load(cls, path: Path | None = None):
        values = read_json(path or BASE / "config.json")
        return cls(**values)

    def public_dict(self):
        return asdict(self)
