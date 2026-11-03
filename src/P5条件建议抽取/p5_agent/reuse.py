"""跨批次复用已完成的包。

P5 一次全量要发两千多次请求，但绝大多数包的内容没变——上游只改了几篇文档，
其余包的输入、配置、实现、提示词完全一致，重跑等于把同样的请求再发一遍。

这里按 checkpoint 里已有的 fingerprint 建索引：指纹相同即认为结果可复用，
直接拷贝检查点到新批次，不发请求。指纹包含包内容、模型配置、实现哈希与提示词资产，
任何一项变了都不会命中，因此不会把旧结果套到新输入上。
"""
from __future__ import annotations

from pathlib import Path

from .common import read_json, write_json

INDEX_NAME = 'checkpoint_index.json'
COMPLETE = {'COMPLETED', 'COMPLETED_WITH_SKIPS'}


def index_run(run_dir):
    """返回该批次 {package_key: fingerprint}，只收已完成的包。

    索引写在批次目录里，第二次扫描直接读；检查点数量变了就重建，
    避免补包之后索引过期。
    """
    run_dir = Path(run_dir)
    folder = run_dir / 'checkpoints'
    if not folder.is_dir():
        return {}
    files = sorted(folder.glob('*.json'))
    cache = run_dir / INDEX_NAME
    if cache.exists():
        saved = read_json(cache)
        if saved.get('count') == len(files):
            return saved.get('by_key') or {}
    by_key = {}
    for path in files:
        try:
            state = read_json(path)
        except (ValueError, OSError):
            continue
        if (state.get('host') or {}).get('status') in COMPLETE:
            by_key[path.stem] = state.get('fingerprint')
    write_json(cache, {'count': len(files), 'by_key': by_key})
    return by_key


def build_map(runs_root, current, sources=None):
    """指纹 → (批次目录, 包键)。同一指纹出现在多个批次时取最新批次。"""
    runs_root = Path(runs_root)
    candidates = [runs_root / name for name in sources] if sources else [
        p for p in runs_root.iterdir() if p.is_dir() and p.name != current.name]
    candidates = [p for p in candidates if p.is_dir() and p.name != current.name]
    mapping = {}
    for run_dir in sorted(candidates, key=lambda p: p.stat().st_mtime):
        for key, fingerprint in index_run(run_dir).items():
            if fingerprint:
                mapping[fingerprint] = (run_dir, key)
    return mapping


def reuse_checkpoint(run_dir, package_key, destination, source_name):
    """把旧检查点拷到新批次，并标明来源；用量单独标记，避免算进本批次开销。"""
    state = read_json(Path(run_dir) / 'checkpoints' / (package_key + '.json'))
    state['reused_from'] = source_name
    state['usage_inherited'] = True
    write_json(destination, state)
    return state


# 注意：指纹不含 P2 的实体归属表。若只改了 P2 的 scenic_code 映射，
# 包内容不变但工具校验会变，这种情况请用 --no-reuse，别拿旧结果顶。
