"""段内匹配、明确语境约束与可回查的实体提及。"""
from collections import defaultdict
import hashlib
import re

from .time_rules import extract_times

WIDTH_MAP = str.maketrans({chr(i): chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)})
PARKS = {
    '长隆野生动物世界': ('长隆野生动物世界', '野生动物世界', '野生动物园', '长隆动物园', '动物园'),
    '长隆欢乐世界': ('长隆欢乐世界', '欢乐世界'),
    '长隆飞鸟乐园': ('长隆飞鸟乐园', '飞鸟乐园'),
    '长隆水上乐园': ('长隆水上乐园', '水上乐园'),
    '长隆海洋王国': ('长隆海洋王国', '海洋王国'),
    '长隆飞船乐园': ('长隆飞船乐园', '飞船乐园'),
}


def stable_id(prefix, *parts):
    return prefix + '-' + hashlib.sha256('\x1f'.join(map(str, parts)).encode('utf-8')).hexdigest()[:16]


def normalized(text):
    # 长度不变，索引无需逆映射；不使用可能展开字符的NFKC。
    return text.translate(WIDTH_MAP)


def term_pattern(term):
    parts = []
    for i, char in enumerate(normalized(term)):
        if i and term[i - 1].isascii() and term[i - 1].isalnum() and '\u4e00' <= char <= '\u9fff':
            parts.append(r'[ \t]*')
        parts.append(re.escape(char))
    return re.compile(''.join(parts))


def origin_index(rows):
    result = defaultdict(list)
    for row in rows:
        result[(row['doc_id'], row['para_id'])].append(dict(row))
    return result


def origin_for(doc_id, para_id, origins):
    return origins.get((doc_id, para_id)) or [{'doc_id': doc_id, 'para_id': para_id, 'kind': 'legacy'}]


def source_key(rows):
    return tuple(sorted((r['kind'], r.get('source', '')) for r in rows))


def previous_context(doc, index, origins, policy):
    p = doc['paragraphs'][index]
    source = source_key(origin_for(doc['doc_id'], p['i'], origins))
    previous = []
    for j in range(index - 1, max(-1, index - policy['context_paragraphs'] - 1), -1):
        q = doc['paragraphs'][j]
        if source_key(origin_for(doc['doc_id'], q['i'], origins)) != source:
            break
        previous.append(q['text'])
    return '\n'.join(reversed(previous))[-policy['context_max_chars']:]


def resolve_park(local, title=''):
    parks = {name for name, aliases in PARKS.items() if any(a in local for a in aliases)}
    if len(parks) == 1:
        return parks.pop()
    if not parks:
        parks = {name for name, aliases in PARKS.items() if any(a in title for a in aliases)}
        if len(parks) == 1:
            return parks.pop()
    return ''


def classify(row, text, start, end, previous, title):
    """返回覆盖字段与拒绝原因。每种guard均有明确可检查的条件。"""
    guard = row['guard']
    local = text[max(0, start - 35):min(len(text), end + 40)]
    context = previous + '\n' + text
    term = row['term']
    if row['guard'] == 'route_label':
        # “花海”不能从“五花海/火花海”内部截取，“熊森林”不从“母熊森林”截取。
        if start and '\u4e00' <= text[start - 1] <= '\u9fff':
            return None, '短名称嵌在更长汉字串中，不能独立成地点'
        if term == '火烈鸟' and not re.search(r'→|—|路线', text + previous):
            return None, '裸动物名不因独立成行就作为地点'
        left, right = text[:start].rstrip(), text[end:].lstrip()
        if (not left or left[-1] in '→—、,，;；') and (not right or right[0] in '→—、,，;；:：('):
            return {}, ''
        return None, '仅接纳独立路线/列表项，不把动物或自然景物泛称当地点'
    if row['guard'] == 'station_exit':
        match = re.search(r'(汉溪长隆(?:地铁)?站)\s*$', text[max(0, start - 20):start])
        if match:
            return {'canonical_name': match[1] + term}, ''
        return None, '站口缺少同段明确站名，不补造出口归属'
    if term == '步行' and text[max(0, start - 1):start] == '细' and text[end:end + 1] == '走':
        return None, '叙事细步行走的内部子串，不是步行交通方式提及'
    if guard == 'named':
        if row['term'] == '老虎嘴' and '五花海' not in context:
            return None, '老虎嘴缺少五花海观景点定位语境'
        if row['type'] == 'TRANSPORT' and text[end:end + 1] == '站':
            return None, '交通词后接站：应识别具体站点，不抽取内部方式子串'
        return {}, ''
    if guard == 'deferred':
        return None, '指向或命名未确定，按冻结决策暂缓'
    if guard == 'gate':
        park = resolve_park(previous + '\n' + text[:end], title)
        if not park:
            return None, '未确定出入口所属园区，不合并同名门'
        return {'canonical_name': park + row['term']}, ''
    if guard == 'forest':
        if re.search(r'遍布|大片|茂密|覆盖|生长', local) and not re.search(r'站|路线|→|前往|抵达|终点', local):
            return None, '原始森林疑为自然描述而非命名地点'
        if re.search(r'路线|→|站|前往|到达|抵达|直奔|坐车|乘车|右线|第一站|终点|起点|游览|景点', context):
            return {}, ''
        return None, '命名原始森林地点语境不足'
    if guard == 'animal_site':
        if re.search(r'→|展区|展馆|馆区|游览路线|\d[⃣、.]', local):
            return {}, ''
        return None, '动物名称未明确作为独立游览地点'
    if guard == 'dual_mobility':
        before = text[max(0, start - 16):start]
        after = text[end:end + 25]
        transport = re.search(r'乘坐|搭乘|换乘|坐|上车|下车|发车|接驳|排队|车程', before + after)
        if transport:
            return {}, ''
        if re.search(r'必冲|必玩|体验|打卡|项目|全景|720|俯瞰|俯视|观景|看动物', before + row['term'] + after):
            return {'type': 'POI', 'poi_role': 'attraction', 'subtype': 'ride', 'canonical_name': '',
                    'scope': 'in_area'}, ''
        return None, '交通/游览体验双功能，缺少明确语境'
    if guard == 'shuttle_boat':
        if re.search(r'酒店|海洋王国|接驳', context) and re.search(r'坐|乘|抵达|前往|去|往返', local):
            return {'canonical_name': '小船'}, ''
        return None, '小船未明确作为酒店/园区接驳交通'
    if guard == 'aviation':
        if row['term'] == '飞':
            if re.match(r'(?:往|到)?(?:成都|深圳|广州|珠海|南京|重庆|九寨)', text[end:]):
                return {'canonical_name': '飞机'}, ''
        elif row['term'] == '直飞' or re.search(r'航班|机场|起飞|坐|乘|返程|抵达', context):
            return {'canonical_name': '飞机'}, ''
        return None, '缺少明确航空出行语境'
    raise ValueError('未实现的语境约束：' + guard)


class Extractor:
    def __init__(self, manifest, config, origins):
        self.manifest, self.config = manifest, config
        self.origins = origin_index(origins)
        self.patterns = {}
        for code, data in config['lexicon'].items():
            self.patterns[code] = [(r, term_pattern(r['term'])) for r in data['entries']]

    def classify(self, row, text, start, end, previous, title):
        return classify(row, text, start, end, previous, title)

    def times(self, text):
        return extract_times(text, self.config['time_rules'])

    def extract(self, doc):
        code = doc['scenic_code']
        data = self.config['lexicon'][code]
        mentions, diagnostics = [], []
        for index, para in enumerate(doc['paragraphs']):
            text = para['text']
            match_text = normalized(text)
            previous = previous_context(doc, index, self.origins, self.config['policy'])
            candidates, refused = [], []
            for row, pattern in self.patterns[code]:
                for match in pattern.finditer(match_text):
                    start, end = match.span()
                    fields, reason = self.classify(row, match_text, start, end, normalized(previous), doc.get('title', ''))
                    if not row['enabled'] or fields is None:
                        refused.append((start, end, row['term'], reason or '冻结配置暂缓'))
                        continue
                    item = {k: row.get(k, '') for k in ('type', 'poi_role', 'subtype', 'canonical_name', 'scope', 'rule_id')}
                    item.update(fields)
                    item.update(start=start, end=end, method='dictionary_context' if row['guard'] != 'named' else 'dictionary')
                    candidates.append(item)
            # 同段最长优先，不能把已知站点/餐厅名称内的短词再次提为交通或动物景点。
            chosen = []
            for item in sorted(candidates, key=lambda x: (-(x['end'] - x['start']), x['start'], x['type'], x['rule_id'])):
                if not any(item['start'] < x['end'] and x['start'] < item['end'] for x in chosen):
                    chosen.append(item)
            for row in data['review_terms']:
                if row['action'] == 'activity_not_poi':
                    continue
                for match in term_pattern(row['term']).finditer(match_text):
                    refused.append((*match.span(), row['term'], row['issue']))
            seen = set()
            for start, end, term, reason in refused:
                if any(x['start'] <= start and end <= x['end'] for x in chosen):
                    continue
                key = (start, end, term)
                if key in seen:
                    continue
                seen.add(key)
                diagnostics.append({'doc_id': doc['doc_id'], 'para_id': para['i'], 'term': term,
                                    'start': para['start'] + start, 'end': para['start'] + end,
                                    'reason': reason, 'action': 'deferred',
                                    'context': text[max(0, start - 35):end + 45]})
            for item in self.times(match_text):
                chosen.append({**item, 'type': 'TIME', 'poi_role': '', 'canonical_name': '',
                               'scope': '', 'method': 'time_rule'})
            for item in chosen:
                start, end = item['start'], item['end']
                if 'expression_start' in item:
                    a, b = item.pop('expression_start'), item.pop('expression_end')
                    item['time_context'] = {'expression': text[a:b], 'start': para['start'] + a,
                                            'end': para['start'] + b, 'prefix': text[a:start], 'suffix': text[end:b]}
                item.update(text=text[start:end], start=para['start'] + start, end=para['start'] + end,
                            para_id=para['i'], origin=origin_for(doc['doc_id'], para['i'], self.origins))
                canonical = item['canonical_name']
                item['entity_id'] = stable_id(item['type'], code if item['type'] == 'POI' else '',
                                             item['poi_role'], canonical) if canonical else ''
                item['mention_id'] = stable_id('M', self.manifest['config_hash'], doc['doc_id'], item['start'],
                                               item['end'], item['type'], item['poi_role'])
                mentions.append(item)
        mentions.sort(key=lambda x: (x['start'], x['end'], x['type']))
        return {'doc_id': doc['doc_id'], 'scenic_code': code, 'scenic_area': doc['scenic_area'],
                'text_type': doc['text_type'], 'source_platform': doc['source_platform'],
                'mentions': mentions}, diagnostics
