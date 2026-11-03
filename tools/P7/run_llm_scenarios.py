# -*- coding: utf-8 -*-
"""用真模型跑全部七个场景，留档供报告与回归对比。

规则策略的批量记录在 p7_agent.py 的主入口里；这里单独一份 LLM 批量记录，
两条路径同场景对照，看模型在哪些场景与规则不同、是否引入新失败。

    OPENAI_API_KEY=... python tools/P7/run_llm_scenarios.py
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P7旅游规划Agent'))
import p7_agent as agent  # noqa: E402

OUT = ROOT / 'outputs/P7'


def main():
    if not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('缺少 OPENAI_API_KEY')
    runs = []
    started = time.time()
    for query in agent.SCENES:
        t0 = time.time()
        run = agent.Agent(query, use_llm=True).run()
        run['elapsed'] = round(time.time() - t0, 2)
        runs.append(run)
        print('%-4s %-2d轮 %-30s' % (run['status'], len(run['turns']), query), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'agent_runs_llm.json').write_text(
        json.dumps(runs, ensure_ascii=False, indent=1), encoding='utf-8')
    with open(OUT / 'agent_llm_all.txt', 'w', encoding='utf-8') as f:
        f.write('真模型批量运行记录（7 场景，模型 %s）\n' % os.environ.get('OPENAI_MODEL', 'deepseek-chat'))
        for r in runs:
            f.write('\n' + '=' * 56 + '\n【问】%s\n' % r['input'])
            for t in r['turns']:
                f.write('  第%d轮 想法：%s\n        动作：%s  %s\n        观察：%s\n' % (
                    t['turn'], t['thought'], t['action'],
                    json.dumps(t['args'], ensure_ascii=False)[:90], t['observation']))
            f.write('  状态：%s\n  答：\n%s\n' % (r['status'], r['answer']))
    print('总用时 %.1fs，记录在 outputs/P7/agent_runs_llm.json' % (time.time() - started))


if __name__ == '__main__':
    main()
