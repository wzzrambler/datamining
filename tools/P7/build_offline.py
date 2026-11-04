# -*- coding: utf-8 -*-
"""把 Web 前端与七个场景的真实结果打包成一个 HTML，双击就能看。

    python tools/P7/build_offline.py
    python tools/P7/build_offline.py --out /tmp/p7.html

数据来自 outputs/P7/agent_runs.json（规则策略的实际运行记录），
不重新规划、不改写答案；结构复用 api._public_result，与在线页面完全一致。
产物不依赖网络、不依赖后端，也不调用模型。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P7 = ROOT / 'src/P7旅游规划Agent'
sys.path.insert(0, str(P7))

import api  # noqa: E402  复用结果整形，避免离线包和在线页两套字段

DEFAULT_OUT = ROOT / 'outputs/P7/行迹-离线预览.html'
WEB_DATA = P7 / 'web/offline.json'
RUNS = ROOT / 'outputs/P7/agent_runs.json'


def _script_safe(text: str) -> str:
    """内联到 <script> 里，必须让 </script> 无法提前闭合。"""
    return text.replace('</', '<\\/')


def load_scenes(runs_path: Path = RUNS) -> list[dict]:
    runs = json.loads(runs_path.read_text(encoding='utf-8'))
    if not runs:
        raise SystemExit('没有运行记录：%s' % runs_path)
    scenes = []
    for run in runs:
        slots = run.get('slots') or {}
        result = api._public_result(run, int(round(float(run.get('elapsed') or 0) * 1000)))
        scenes.append({
            'input': run.get('input', ''),
            'status': run.get('status', ''),
            'area': result['plan']['area'] if result.get('plan') else slots.get('area', ''),
            'days': result['plan']['days'] if result.get('plan') else slots.get('days'),
            'visitor': result['plan']['visitor'] if result.get('plan') else slots.get('visitor', ''),
            'result': result,
        })
    return scenes


def build(out: Path = DEFAULT_OUT, runs_path: Path = RUNS) -> Path:
    scenes = load_scenes(runs_path)
    counts = {
        'routes': len(api.engine.ROUTES),
        'documents': len(api.engine.ENTS),
        'entities': sum(len(d.get('entities') or []) for d in api.engine.ENTS),
        'rules': len(api.engine.CONDS),
    }
    payload = {
        'meta': {
            'version': api.VERSION,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'counts': counts,
        },
        'scenes': scenes,
    }

    html = (P7 / 'web/index.html').read_text(encoding='utf-8')
    css = (P7 / 'web/styles.css').read_text(encoding='utf-8')
    js = (P7 / 'web/app.js').read_text(encoding='utf-8')

    html = html.replace('<title>行迹 · 知识图谱旅游规划</title>',
                        '<title>行迹 · 离线预览</title>')
    html = html.replace('<link rel="stylesheet" href="./styles.css">',
                        '<style>\n%s\n</style>' % css)
    data = '<script>window.__P7_OFFLINE__ = %s;</script>\n' % _script_safe(
        json.dumps(payload, ensure_ascii=False))
    html = html.replace('<script src="./app.js" defer></script>',
                        '%s<script>\n%s\n</script>' % (data, _script_safe(js)))
    if '<link rel="stylesheet"' in html or '<script src=' in html:
        raise SystemExit('index.html 的外链写法变了，离线包没有替换干净')

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding='utf-8')
    return out


def write_web_data(path: Path = WEB_DATA, runs_path: Path = RUNS) -> Path:
    """静态托管用：web/offline.json 与单文件里的数据同源，各生成一次。"""
    payload = {
        'meta': {
            'version': api.VERSION,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'counts': {
                'routes': len(api.engine.ROUTES),
                'documents': len(api.engine.ENTS),
                'entities': sum(len(d.get('entities') or []) for d in api.engine.ENTS),
                'rules': len(api.engine.CONDS),
            },
        },
        'scenes': load_scenes(runs_path),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='生成 P7 单文件离线预览与静态数据')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--runs', type=Path, default=RUNS)
    parser.add_argument('--no-web-data', action='store_true', help='只生成单文件，不更新 web/offline.json')
    args = parser.parse_args(argv)
    path = build(args.out, args.runs)
    scenes = len(load_scenes(args.runs))
    size = path.stat().st_size / 1024
    print('已生成 %s（%.0f KB，%d 个场景）' % (
        path.relative_to(ROOT) if path.is_relative_to(ROOT) else path, size, scenes))
    if not args.no_web_data:
        data = write_web_data(runs_path=args.runs)
        print('已更新 %s（静态托管用，%.0f KB）' % (
            data.relative_to(ROOT), data.stat().st_size / 1024))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
