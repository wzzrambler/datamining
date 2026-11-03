"""定位 P5 正式批次目录。

P6 只认一个 P5 批次，但批次目录名会随重跑变（full-final-20261101 → full-20261103）。
旧版把路径写死在 clean_routes.py、quality_pipeline.py、run.py 三处，重跑一次就得改三处
代码，漏改一处就会拿着旧批次算出新结果，而且产物里的 source_run 也跟着撒谎。

解析顺序（越靠前越优先）：

    1. 显式指定：--p5-run 参数，或环境变量 P5_RUN
    2. 指针文件：processed/P5条件建议抽取/ACTIVE_RUN，内容是一行 run_id
    3. runs/ 下唯一一个跑完的批次（summary 里疑似状态都为 COMPLETED）

三条都落空就报错并列出候选，不猜最新目录——目录 mtime 在复制、解压、git checkout
之后完全不可信。

命令行：

    python -X utf8 src/P6知识图谱/p5run.py                 # 打印当前解析到的批次
    python -X utf8 src/P6知识图谱/p5run.py --set 批次名      # 写指针文件
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / 'processed/P5条件建议抽取/runs'
POINTER = ROOT / 'processed/P5条件建议抽取/ACTIVE_RUN'
POINTER_NAME = 'ACTIVE_RUN'
DONE = {'COMPLETED'}
# 这些状态说明包没跑完，批次不算完；EXHAUSTED 是重试到上限仍失败。
UNFINISHED = {'PENDING', 'RUNNING', 'FAILED', 'EXHAUSTED', 'ABANDONED'}


def runs_root(root=None):
    return Path(root) / 'processed/P5条件建议抽取/runs' if root else RUNS


def pointer_path(root=None):
    return Path(root) / 'processed/P5条件建议抽取' / POINTER_NAME if root else POINTER


def summary_of(run_dir):
    path = Path(run_dir) / 'summary.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text('utf-8'))
    except ValueError:
        return None


def incomplete_reason(run_dir):
    """跑完返回 None，没跑完返回一句人话。"""
    s = summary_of(run_dir)
    if s is None:
        return '缺 summary.json'
    counts = s.get('status_counts') or {}
    bad = {k: v for k, v in counts.items() if k in UNFINISHED and v}
    if bad:
        return '有未完成/失败的包：' + ', '.join('%s=%d' % kv for kv in sorted(bad.items()))
    prepared, attempted = s.get('packages_prepared'), s.get('packages_attempted')
    if isinstance(prepared, int) and isinstance(attempted, int) and attempted < prepared:
        return '只跑了 %d/%d 个包' % (attempted, prepared)
    if not counts:
        return 'summary 里没有 status_counts'
    return None


def candidates(root=None):
    base = runs_root(root)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and summary_of(p) is not None)


def resolve(root=None, run_id=None, hint=''):
    """返回批次目录；解析不到就抛 ValueError，绝不退回某个"看起来还行"的目录。"""
    base = runs_root(root)
    if run_id:
        path = base / run_id
        if not path.is_dir():
            raise ValueError('%s指定的 P5 批次不存在：%s' % (hint, path))
        return path

    ptr = pointer_path(root)
    if ptr.exists():
        name = ptr.read_text('utf-8').strip()
        if name:
            path = base / name
            if not path.is_dir():
                raise ValueError('ACTIVE_RUN 指向的批次不存在：%s（%s）' % (name, ptr))
            return path

    found = candidates(root)
    if not found:
        raise ValueError('找不到任何 P5 批次：%s' % base)
    done = [p for p in found if incomplete_reason(p) is None]
    if len(done) == 1:
        return done[0]
    detail = '\n'.join('  %s  %s' % (p.name, incomplete_reason(p) or '已完成') for p in found)
    if not done:
        raise ValueError('没有跑完的 P5 批次：\n%s' % detail)
    raise ValueError('有 %d 个跑完的 P5 批次，无法自动选择：\n%s\n'
                     '用 --p5-run 或 P5_RUN 指定，或写 %s。' % (len(done), detail, POINTER))


def describe(path):
    s = summary_of(path) or {}
    return {'run_dir': str(path), 'run_id': s.get('run_id', path.name),
            'packages_attempted': s.get('packages_attempted'),
            'packages_prepared': s.get('packages_prepared'),
            'status_counts': s.get('status_counts') or {},
            'complete': incomplete_reason(path) is None}


def set_pointer(run_id, root=None):
    path = runs_root(root) / run_id
    if not path.is_dir():
        raise ValueError('批次不存在：%s' % path)
    ptr = pointer_path(root)
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(run_id + '\n', encoding='utf-8')
    return ptr


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', metavar='RUN_ID', help='把指针文件指向这个批次')
    ap.add_argument('--p5-run', help='只解析这个批次，不读指针')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    try:
        if args.set:
            ptr = set_pointer(args.set)
            print('%s -> %s' % (ptr, args.set))
            return 0
        path = resolve(run_id=args.p5_run or os.environ.get('P5_RUN'))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    info = describe(path)
    print(json.dumps(info, ensure_ascii=False, indent=1) if args.json else
          '%s（%s/%s 包，%s）' % (info['run_dir'], info['packages_attempted'],
                                info['packages_prepared'], '已完成' if info['complete'] else '未完成'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
