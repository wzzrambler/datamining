# -*- coding: utf-8 -*-
"""用模型对主题复核底表逐条判定，口径与 outputs/P6/主题复核说明.md 一致。

分批请求、每批校验完整性、判完即写回（可中断续跑）；已有判定的行自动跳过。
"""
import csv
import json
import os
import sys
import time
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / 'outputs/P6/主题复核底表525条.csv'
BATCH = 8
MODEL = os.environ.get('OPENAI_MODEL', 'deepseek-chat')

SYSTEM = (
    '你是知识图谱主题的语义审核员。对每个主题，比对"主题摘要"与"成员条目原文"，输出判定：\n'
    '- 成立：摘要忠实概括成员条目，摘要中的每个含义都能在条目里找到依据；同义改写与适度归纳不算问题。\n'
    '- 牵强：摘要引入了条目里没有的含义；或把单一条目的特性说成整组普遍适用；或明显遗漏大多数条目的主要内容。\n'
    '- 不成立：摘要与条目相悖、张冠李戴、或主题与条目毫无关系。\n'
    '- 无法核对：成员条目原文为空。\n'
    '备注仅当判定为 牵强/不成立/无法核对 时填写（不超过24字，给出具体原因），成立则备注为空字符串。\n'
    '摘要以"历史资料记载，"开头是固定模板，不算摘要内容。只依据给出的文字判断，不要引入外部知识。\n'
    '只输出 JSON 对象：{"results": [{"序号": 整数, "判定": "成立|牵强|不成立|无法核对", "备注": "字符串"}]}，'
    '按给定顺序、每条一个元素，不要输出其他内容。'
)


def client():
    key = os.environ.get('OPENAI_API_KEY')
    if not key:
        sys.exit('缺少 OPENAI_API_KEY')
    return OpenAI(api_key=key, base_url=os.environ.get('OPENAI_BASE_URL', 'https://api.deepseek.com/v1'))


def judge_batch(cli, batch):
    lines = []
    for r in batch:
        lines.append('{"序号": %s, "主题标签": %s, "主题摘要": %s, "成员条目原文": %s}' % (
            r['序号'], json.dumps(r['主题标签'], ensure_ascii=False),
            json.dumps(r['主题摘要'], ensure_ascii=False),
            json.dumps(r['成员条目原文(截断)'], ensure_ascii=False)))
    user = '待判定主题：\n' + '\n'.join(lines) + '\n请按顺序输出判定 JSON。'
    last_err = None
    for attempt in range(3):
        try:
            resp = cli.chat.completions.create(
                model=MODEL, temperature=0,
                messages=[{'role': 'system', 'content': SYSTEM},
                          {'role': 'user', 'content': user}])
            text = resp.choices[0].message.content.strip()
            if text.startswith('```'):
                text = text.strip('`')
                text = text[text.index('{'):] if '{' in text else text
            data = json.loads(text)
            out = {str(x['序号']): (x['判定'], x.get('备注', '')) for x in data['results']}
            if set(out) != {r['序号'] for r in batch}:
                raise ValueError('返回序号不完整')
            for verdict, _ in out.values():
                if verdict not in {'成立', '牵强', '不成立', '无法核对'}:
                    raise ValueError('非法判定: ' + verdict)
            return out
        except Exception as exc:  # noqa: BLE001 - 记录后重试
            last_err = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError('批次判定失败: ' + str(last_err))


def main():
    rows = list(csv.DictReader(open(CSV_PATH, encoding='utf-8-sig')))
    pending = [r for r in rows if not r['判定(成立/牵强/不成立)'].strip()]
    print('总数 %d，待判 %d' % (len(rows), len(pending)), flush=True)
    if not pending:
        return
    cli = client()
    done = 0
    for i in range(0, len(pending), BATCH):
        batch = pending[i:i + BATCH]
        forced = [r for r in batch if not r['成员条目原文(截断)'].strip()]
        need = [r for r in batch if r['成员条目原文(截断)'].strip()]
        out = {}
        if need:
            out.update(judge_batch(cli, need))
        for r in forced:
            out[r['序号']] = ('无法核对', '成员条目未展示')
        for r in batch:
            verdict, note = out[r['序号']]
            r['判定(成立/牵强/不成立)'] = verdict
            r['备注'] = note
        with open(CSV_PATH, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        done += len(batch)
        print('进度 %d/%d' % (done, len(pending)), flush=True)
    counts = {}
    for r in rows:
        counts[r['判定(成立/牵强/不成立)']] = counts.get(r['判定(成立/牵强/不成立)'], 0) + 1
    print(json.dumps(counts, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
