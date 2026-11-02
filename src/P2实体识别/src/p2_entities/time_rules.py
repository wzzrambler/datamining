"""正式时间规则：核心值边界、完整日期优先，保留提示词上下文。"""
import json
import re
from datetime import date
from pathlib import Path

CN = r'[零〇一二两三四五六七八九十百]+'


def definitions():
    return json.loads((Path(__file__).resolve().parents[2] / 'config/time_rules.json').read_text(encoding='utf-8'))


def chinese_int(text):
    if text.isdigit():
        return int(text)
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    if text == '十':
        return 10
    if '十' in text and text.count('十') == 1:
        a, b = text.split('十')
        return (digits.get(a, 1) if a else 1) * 10 + (digits.get(b, 0) if b else 0)
    return digits.get(text, -1)

def valid_time(text):
    for match in re.finditer(rf'({CN}|\d+)点(?:\s*({CN}|\d+)分?)?', text):
        if not 0 <= chinese_int(match[1]) <= 23:
            return False
        if match[2] and not 0 <= chinese_int(match[2]) <= 59:
            return False
    for match in re.finditer(r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})', text):
        try:
            date(*(int(x) for x in match.groups()))
        except ValueError:
            return False
    for match in re.finditer(r'(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})[日号]', text):
        try:
            date(int(match[1] or 2000), int(match[2]), int(match[3]))
        except ValueError:
            return False
    return True

def extract_times(text, rules):
    candidates = []
    for priority, rule in enumerate(rules):
        for match in re.finditer(rule['pattern'], text, re.IGNORECASE):
            a, b = match.span()
            key = next((k for k in ('core', 'interval') if k in match.groupdict() and match.group(k) is not None), None)
            start, end = match.span(key) if key else (a, b)
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start >= end:
                continue
            # “演出时间:19:30”合法；“25:00”内部的“5:00”仍须排除。
            if start and (text[start - 1].isdigit() or
                          (text[start - 1] in ':：' and start >= 2 and text[start - 2].isdigit()) or
                          (text[start].isascii() and text[start].isalpha() and text[start - 1].isascii() and text[start - 1].isalpha())):
                continue
            if end < len(text) and text[end].isdigit():
                continue
            value = text[start:end]
            if not valid_time(value):
                continue
            if rule['subtype'] == 'clock' and re.fullmatch(rf'{CN}点', value):
                before, after = text[max(0, start - 8):start], text[end:end + 8]
                if re.search(r'建议|注意|原因|提醒|特点|不同', after):
                    continue
                if value == '一点':
                    degree = re.search(r'早|晚|快|慢|多|少|长|短|久|好|累|便宜|明显|顺手|传统|提前|给|买|带|补充|完善', before)
                    anchor = re.search(r'中午|下午|凌晨|已经|现在|到了', before) or re.match(r'钟|左右|多|前|后|开园|闭园|开始|结束|发车|出发|到达', after)
                    if degree or not anchor:
                        continue
            candidates.append((priority, -(end - start), start, end, a, b, rule))
    chosen = []
    for _, _, start, end, a, b, rule in sorted(candidates):
        if any(start < r['end'] and r['start'] < end for r in chosen):
            continue
        item = {'start': start, 'end': end, 'subtype': rule['subtype'], 'rule_id': rule['id']}
        if (a, b) != (start, end):
            item.update(expression_start=a, expression_end=b)
        chosen.append(item)
    return sorted(chosen, key=lambda r: (r['start'], r['end']))
