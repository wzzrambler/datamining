"""从 JSON 任务列表顺序采集；先校验全部配置，异常即停止整批。"""
import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from . import crawler


WAIT_DEFAULTS = {'min_seconds': 10, 'max_seconds': 60, 'long_probability': .05,
                 'long_min_seconds': 300, 'long_max_seconds': 600}
BUDGET_DEFAULTS = {'max_requests': 15000, 'daily_requests': 20000,
                   'task_requests': 15000, 'total_requests': None, 'max_minutes': 180}
WAIT_FLAGS = dict(zip(WAIT_DEFAULTS, ('--min-wait', '--max-wait', '--long-probability', '--long-min', '--long-max')))
BUDGET_FLAGS = {key: '--' + key.replace('_', '-') for key in BUDGET_DEFAULTS}


def load_tasks(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, list) or not data:
        raise ValueError('配置顶层必须是非空任务数组 [{...}, ...]。')
    tasks, seen = [], set()
    for index, item in enumerate(data, 1):
        label = f'第 {index} 项'
        if not isinstance(item, dict) or set(item) - {'keyword', 'target_count', 'wait', 'budget'}:
            raise ValueError(label + '只支持 keyword、target_count、wait、budget。')
        keyword = item.get('keyword')
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError(label + ' keyword 尚未填写；整批未启动，不会联网。')
        keyword = keyword.strip()
        if keyword in seen:
            raise ValueError(label + '关键词重复，请合并为一个任务。')
        seen.add(keyword)
        count = item.get('target_count', 15)
        if type(count) is not int or not 1 <= count <= 100:
            raise ValueError(label + ' target_count 必须是 1–100 的整数。')
        options = {}
        for name, defaults in (('wait', WAIT_DEFAULTS), ('budget', BUDGET_DEFAULTS)):
            values = item.get(name, {})
            if not isinstance(values, dict) or set(values) - defaults.keys():
                raise ValueError(label + ' ' + name + ' 含未知字段或不是对象。')
            merged = {**defaults, **values}
            for key, value in merged.items():
                if key == 'total_requests' and value is None:
                    continue
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(label + ' ' + key + ' 必须为有限数字。')
                if name == 'budget' and (value <= 0 or (key != 'max_minutes' and type(value) is not int)):
                    raise ValueError(label + '请求预算必须为正整数，时长必须为正数。')
            options[name] = merged
        wait = options['wait']
        if not (10 <= wait['min_seconds'] <= wait['max_seconds']
                and 300 <= wait['long_min_seconds'] <= wait['long_max_seconds']
                and 0 <= wait['long_probability'] <= 1):
            raise ValueError(label + '等待时间须满足短等待至少 10 秒、长等待至少 300 秒，概率为 0–1。')
        tasks.append({'keyword': keyword, 'target_count': count, **options})
    return tasks


def task_argv(task):
    args = ['--keyword', task['keyword'], '--limit', str(task['target_count']), '--details',
            '--profile', crawler.ACTIVE_PROFILE]
    for name, flags in (('wait', WAIT_FLAGS), ('budget', BUDGET_FLAGS)):
        for key, value in task[name].items():
            if value is not None:
                args.extend((flags[key], str(value)))
    return args


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=crawler.BASE / 'tasks.json')
    parser.add_argument('--check', action='store_true', help='仅校验与展示计划，不联网')
    args = parser.parse_args(argv)
    try:
        tasks = load_tasks(args.config)
    except (OSError, ValueError) as exc:
        print('配置未通过：' + str(exc))
        return 2
    for index, task in enumerate(tasks, 1):
        print(f'{index}. {task["keyword"]}：目标 {task["target_count"]} 篇完整正文（跳过的不计数，完整缓存计入）')
    if args.check:
        print('校验通过；未启动浏览器，没有联网。')
        return 0
    report = {'config': str(args.config.resolve()), 'tasks': [], 'status': 'running'}
    output = crawler.OUT
    output.mkdir(parents=True, exist_ok=True)
    path = output / ('batch_report_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
    try:
        for task in tasks:
            code = crawler.main(task_argv(task))
            report['tasks'].append({'keyword': task['keyword'], 'target_count': task['target_count'],
                                    'exit_code': code, 'output': str(crawler.keyword_output(task['keyword']))})
            if code != 0:
                report['status'] = 'stopped'
                print('当前任务未完成，停止后续关键词；进度与停止状态保留。')
                return code
        report['status'] = 'completed'
        return 0
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        return 2
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('批次报告：' + str(path))


if __name__ == '__main__':
    raise SystemExit(main())
