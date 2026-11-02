"""P1离线质量检测：不改正文，不联网；人工复核与自动检查分开记录。"""
import argparse
import csv
import importlib.util
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import paths

CLEAN = ROOT / 'src/P1爬虫与数据清洗/小红书爬虫与数据清洗/数据清洗'
spec = importlib.util.spec_from_file_location('p1_quality_rules', CLEAN / 'src/rules.py')
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)
SCENIC = {
    'GZCL': ('广州长隆旅游度假区', '广州', '主题乐园'),
    'ZHCL': ('珠海横琴长隆国际海洋度假区', '珠海', '主题乐园'),
    'JZG': ('九寨沟风景名胜区', '阿坝州', '自然遗产'),
}
ISSUE_FIELDS = ['严重程度', '文档编号', '检测项', '字段或段落', '证据', '建议', '处理状态']
DUP_FIELDS = ['类型', '文档A', '文档B', '相似度', '包含率', '证据', '处理状态']
NOISE_PATTERNS = {
    'HTML标签残留': re.compile(r'</?[A-Za-z][^>]*>'),
    '网址残留': re.compile(r'https?://\S+|www\.\S+'),
    '已知表情残留': rules.XHS_EMOJI,
    'Unicode表情残留': rules.EMOJI,
    '话题标记残留': re.compile(r'\[话题\]'),
    '替换字符乱码': re.compile('\ufffd'),
    '控制字符残留': rules.INVISIBLE,
    '疑似未知表情': re.compile(r'\[[^\]\n]{1,12}R\]'),
    '网页控件独立行': re.compile(r'(?m)^(?:展开|收起|查看更多|点击展开)(?:全部)?(?:\d+条)?(?:回复|评论)?$'),
    '疑似模板元数据': re.compile(r'(?m)^(?:责任编辑|编辑|责编|审核|排版|图源|图片来源|供图|来源)\s*[:：丨|].*$'),
    '邮箱残留': re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'),
    '疑似电话号码': re.compile(r'(?<!\d)(?:1[3-9]\d{9}|400[- ]?\d{3}[- ]?\d{4})(?!\d)'),
    '空白格式异常': re.compile(r'\r|\t|\n{2,}| {2,}|(?m:^ +| +$)'),
    '重复标点': re.compile(r'([！？。，、；：,.!?;:])\1{2,}'),
    '全角ASCII残留': re.compile('[\uff01-\uff5e\u3000\u00a0]'),
}


def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def normalize(text):
    return re.sub(r'\W+', '', unicodedata.normalize('NFKC', text)).casefold()


def source_identity(url):
    parsed = urlsplit(url)
    if parsed.hostname and (parsed.hostname == 'xiaohongshu.com' or parsed.hostname.endswith('.xiaohongshu.com')):
        note = re.search(r'/(?:explore|search_result)/([0-9a-fA-F]{24})(?:/|$)', parsed.path)
        if note:
            return 'xiaohongshu:' + note[1].lower()
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.hostname == 'mp.weixin.qq.com' and parsed.path.rstrip('/') == '/s':
        # 微信/s的__biz、mid、idx等参数标识文章，不能全部丢弃。
        article = [(k,v) for k,v in query if k in ('__biz','mid','idx','sn')]
        query = article or query
    else:
        tracking = {'xsec_token','xsec_source','spm','from','source','share_token'}
        query = [(k,v) for k,v in query if k not in tracking and not k.startswith('utm_')]
    return (parsed.hostname or '').lower() + parsed.path.rstrip('/') + ('?' + urlencode(sorted(query)) if query else '')


def issue(doc, item, field, evidence, advice, level='复核'):
    # 报告中不复制访问令牌、长URL或整篇正文。
    evidence = re.sub(r'https?://\S+', '[链接已省略]', str(evidence))
    return dict(zip(ISSUE_FIELDS, [level, doc, item, field, evidence[:350], advice, '待处理']))


def inspect_document(d, expected):
    out = []
    doc = str(d.get('doc_id', '未知'))
    add = lambda item, field, evidence, advice, level='复核': out.append(issue(doc, item, field, evidence, advice, level))
    if set(d) != set(expected):
        add('字段集合不一致', 'schema', '缺少:' + str(sorted(set(expected)-set(d))) + ';多出:' + str(sorted(set(d)-set(expected))), '修复导出字段', '错误')
    for field, sample in expected.items():
        if field not in d:
            continue
        if type(d[field]) is not type(sample):
            add('字段类型异常', field, type(d[field]).__name__, '按既有字段定义修复', '错误')
        elif isinstance(sample, int) and d[field] < 0:
            add('数值字段为负', field, d[field], '核对统计计算；净缩减量应截断为非负值', '错误')
    for field in ('has_time_expr', 'is_valid', 'is_near_duplicate'):
        if d.get(field) not in (0, 1):
            add('标记枚举异常', field, d.get(field), '应为0或1', '错误')
    if any(v is None for v in d.values()):
        add('null值', 'schema', '存在null', '核实来源，按字段约定处理', '错误')
    for field in ('title', 'author', 'publish_date', 'crawl_time', 'content', 'source_url', 'raw_html_path'):
        if not d.get(field):
            add('字段缺失', field, '空值', '先查原始证据；无法补齐时保留空值及原因', '错误' if field in ('content', 'source_url') else '记录')
    if any(i['检测项'] == '字段类型异常' for i in out) or not isinstance(d.get('content'), str) or not isinstance(d.get('paragraphs'), list):
        return out
    content, paras = d['content'], d['paragraphs']
    if d.get('noise_char_removed') != max(0, d.get('raw_char_len',0) - len(content)):
        add('删减统计口径不一致', 'noise_char_removed', d.get('noise_char_removed'), '应等于max(0,raw_char_len-clean_char_len)', '错误')
    if d.get('sentence_count') != rules.count_sentences(content):
        add('句数统计不一致', 'sentence_count', d.get('sentence_count'), '统一按中英文句末/分号及换行切分，长度至少5字符', '错误')
    if d.get('clean_char_len') != len(content) or d.get('paragraph_count') != len(paras):
        add('正文统计不一致', 'clean_char_len/paragraph_count', '与实际正文不一致', '重新生成正文统计', '错误')
    cursor, texts = 0, []
    for i, p in enumerate(paras, 1):
        if not isinstance(p, dict) or not isinstance(p.get('text'), str):
            add('段落结构异常', f'paragraphs[{i}]', str(p), '重新生成段落', '错误')
            continue
        text = p['text']
        if p.get('i') != i or p.get('start') != cursor or p.get('end') != cursor + len(text):
            add('段落位置异常', f'paragraphs[{i}]', f'预期起点{cursor}', '重新生成段落位置', '错误')
        texts.append(text)
        cursor += len(text) + 1
    if '\n'.join(texts) != content:
        add('段落重组不一致', 'paragraphs', '段落拼接不等于content', '重新生成段落', '错误')
    code = d.get('scenic_code')
    if code not in SCENIC or tuple(d.get(k) for k in ('scenic_area', 'city', 'category')) != SCENIC.get(code):
        add('景区字段不匹配', 'scenic_code/scenic_area/city/category', str(code), '核对分组配置', '错误')
    match = re.fullmatch(r'([A-Z]+)-(NOTE|OFF)-(\d+)', doc)
    kind = d.get('text_type')
    if not match or match[1] != code or match[2] != {'travel_note': 'NOTE', 'official_route': 'OFF'}.get(kind):
        add('编号与类型不匹配', 'doc_id/text_type', doc, '核对编号与类型', '错误')
    if d.get('text_type_cn') != {'travel_note': '游客游记', 'official_route': '官方推荐路线'}.get(kind):
        add('中文类型待核对', 'text_type_cn', d.get('text_type_cn'), '对照旧版枚举确认')
    for field in ('publish_date', 'crawl_time'):
        value = d.get(field)
        if value:
            try:
                datetime.fromisoformat(value.replace('Z', '+00:00'))
                if field == 'publish_date' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                add('日期格式异常', field, value, '核对原值并规范格式')
    if d.get('source_url'):
        u = urlsplit(d['source_url'])
        if u.scheme not in ('http', 'https') or not u.hostname:
            add('来源链接格式异常', 'source_url', '非HTTP(S)来源', '核对原始来源', '错误')
        if u.hostname != d.get('source_site'):
            add('来源域名不一致', 'source_site', d.get('source_site'), '核对source_site与来源链接')
    for name, pattern in NOISE_PATTERNS.items():
        matches = list(pattern.finditer(content))
        if matches:
            examples = [content[max(0,m.start()-15):m.end()+25].replace('\n', ' / ') for m in matches[:3]]
            advice = '对照原文复核，确认后修复；不自动删除'
            if name == '全角ASCII残留':
                advice = '核对跨来源格式口径；旧版保留中文标点，不代表乱码，不直接删除'
            add(name, 'content', f'{len(matches)}处；' + '；'.join(examples), advice)
    if len(content) < 200:
        add('正文较短', 'content', f'{len(content)}字符', '确认正文完整性和有效性')
    short = sum(len(p) <= 10 for p in texts)
    if texts and short / len(texts) >= .5:
        add('短段落比例较高', 'paragraphs', f'{short}/{len(texts)}段不超过10字符', '对照原图检查断行；不直接删短段或自动合并')
    if content and len(content) < d.get('raw_char_len', 0) * .5:
        add('清洗删减比例较高', 'raw_char_len/clean_char_len', f"{d.get('raw_char_len')}→{len(content)}", '检查误删；原始长度仅统计参与合并的文本，不是整页HTML')
    if not any(word in content for word in {'GZCL': ('长隆', '野生动物', '欢乐世界'), 'ZHCL': ('长隆', '海洋王国', '宇宙飞船'), 'JZG': ('九寨', '五花海', '诺日朗')}.get(code, ())):
        add('景区相关性待确认', 'content', '未命中基础景区别名', '人工判定，不据此自动排除')
    return out


def find_duplicates(docs):
    out = []
    texts = [normalize(d['content']) for d in docs]
    sets = [{t[i:i+3] for i in range(max(0, len(t)-2))} for t in texts]
    for j, b in enumerate(docs):
        for i in range(j):
            a = docs[i]
            common = len(sets[i] & sets[j])
            union = len(sets[i] | sets[j])
            smaller = min(len(sets[i]), len(sets[j]))
            similarity = common / union if union else 0
            containment = common / smaller if smaller else 0
            kinds = []
            if a['doc_id'] == b['doc_id']:
                kinds.append('编号重复')
            if a.get('source_url') and b.get('source_url') and source_identity(a['source_url']) == source_identity(b['source_url']):
                kinds.append('来源重复')
            if texts[i] and texts[i] == texts[j]:
                kinds.append('规范化正文完全重复')
            elif similarity >= .8:
                kinds.append('近重复')
            elif min(len(texts[i]), len(texts[j])) >= 200 and containment >= .9:
                kinds.append('疑似包含关系')
            if kinds:
                out.append(dict(zip(DUP_FIELDS, ['；'.join(kinds), a['doc_id'], b['doc_id'], round(similarity,4), round(containment,4), 'NFKC及标点/空白归一；三字符集合', '待人工判定'])))
    repeated = defaultdict(dict)
    for d in docs:
        for p in d['paragraphs']:
            key = normalize(p['text'])
            if len(key) >= 30:
                repeated[key].setdefault(d['doc_id'], p)
    for group in repeated.values():
        if len(group) > 1:
            ids = sorted(group)
            out.append(dict(zip(DUP_FIELDS, ['跨文档重复段落', ids[0], '；'.join(ids[1:]), '', '', group[ids[0]]['text'][:180], '可能为路线共用内容，待人工判定'])))
    return out


def cross_format(folder, docs):
    out = []
    def compare(ok, label):
        if not ok:
            out.append(issue('全量', '输出一致性异常', label, '与documents.json不一致', '重新导出并核对', '错误'))
    with (folder / 'documents.csv').open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields = [k for k in docs[0] if k not in ('content', 'paragraphs')]
        compare(reader.fieldnames == fields, 'documents.csv表头')
        compare(list(reader) == [{k: str(d[k]) for k in fields} for d in docs], 'documents.csv全字段')
    with (folder / 'paragraphs.csv').open(encoding='utf-8-sig', newline='') as f:
        actual = list(csv.DictReader(f))
    expected = [dict(doc_id=d['doc_id'], para_id=str(p['i']), char_start=str(p['start']), char_end=str(p['end']), char_count=str(len(p['text'])), scenic_area=d['scenic_area'], text_type=d['text_type'], text=p['text']) for d in docs for p in d['paragraphs']]
    compare(actual == expected, 'paragraphs.csv全字段')
    wb = load_workbook(folder / 'dataset.xlsx', read_only=True, data_only=False)
    try:
        compare(wb.sheetnames == ['文档清单', '正文', '段落', '字段说明'], 'Excel工作表')
        mappings = {
            '文档清单': ['doc_id','scenic_area','scenic_code','category','text_type_cn','source_platform','source_site','title','author','publish_date','raw_char_len','clean_char_len','paragraph_count','sentence_count','has_time_expr','is_valid','quality_flags','source_url'],
            '正文': ['doc_id','scenic_area','text_type_cn','title','clean_char_len','content'],
        }
        for sheet, fields in mappings.items():
            headers = fields if sheet == '文档清单' else ['doc_id','景区','文本类型','标题','字数','正文（清洗后）']
            compare(list(next(wb[sheet].values)) == headers, 'Excel/' + sheet + '表头')
            got = [[c.value if c.value is not None else '' for c in row] for row in list(wb[sheet].rows)[1:]]
            compare(got == [[d[k] for k in fields] for d in docs], 'Excel/' + sheet)
        got = [tuple(c.value for c in row) for row in list(wb['段落'].rows)[1:]]
        compare(list(next(wb['段落'].values)) == ['doc_id','段号','起始','结束','字数','段落文本'], 'Excel/段落表头')
        compare(got == [(d['doc_id'],p['i'],p['start'],p['end'],len(p['text']),p['text']) for d in docs for p in d['paragraphs']], 'Excel/段落')
        compare([r[0] for r in list(wb['字段说明'].values)[1:]] == list(docs[0]), 'Excel/字段说明')
        compare(not any(c.data_type == 'f' for ws in wb for row in ws for c in row), 'Excel意外公式')
    finally:
        wb.close()
    return out


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def select_review(docs, details):
    weights = {}
    for d in details:
        body = sum(len(p['clean_text']) for p in d['parts'] if p['kind'] == 'body' and p['included'])
        image = sum(len(p['clean_text']) for p in d['parts'] if p['kind'] == 'image' and p['included'])
        weights[d['doc_id']] = image / max(body + image, 1)
    selected = []
    for code in SCENIC:
        official = [d for d in docs if d['scenic_code'] == code and d['text_type'] == 'official_route']
        travel = [d for d in docs if d['scenic_code'] == code and d['text_type'] == 'travel_note' and d['doc_id'] in weights]
        if official:
            selected.append((official[0], '官方路线'))
        if travel:
            ordered = sorted(travel, key=lambda d: (weights[d['doc_id']], d['doc_id']))
            selected.append((ordered[0], '相对偏正文的游客游记'))
            if len(ordered) > 1:
                selected.append((ordered[-1], '相对偏图片的游客游记'))
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deep', action='store_true', help='解压本地归档，逐项验证原始证据；不联网')
    parser.add_argument('--output', type=Path, default=paths.P1 / '质量检查/自动质检')
    args = parser.parse_args(argv)
    folder, dest = paths.P1, args.output.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    docs = load(folder / 'documents.json')
    expected = load(folder / '辅助资料/legacy_documents.json')[0]
    details = load(folder / '辅助资料/raw_and_clean_parts.json')
    issues = [x for d in docs for x in inspect_document(d, expected)]
    issues.extend(cross_format(folder, docs))
    duplicates = find_duplicates(docs)
    for d in duplicates:
        issues.append(issue(d['文档A'], '重复候选', 'content/source_url', d['类型'] + '；对比文档:' + d['文档B'], '结合原文判定；不自动删除', '错误' if '编号重复' in d['类型'] else '复核'))
    counts = []
    for code, (name, _, _) in SCENIC.items():
        official = sum(d['scenic_code'] == code and d['text_type'] == 'official_route' for d in docs)
        travel = sum(d['scenic_code'] == code and d['text_type'] == 'travel_note' for d in docs)
        counts.append({'scenic_code': code, 'scenic_area': name, 'official': official, 'travel': travel})
        if official < 1 or travel < 3:
            issues.append(issue(code, '景区数量不足', 'counts', f'官方{official}，游记{travel}', '补齐有效来源', '错误'))
    origins = load(folder / '辅助资料/paragraph_sources.json')
    actual_origins = [(r['doc_id'], r['para_id']) for r in origins]
    expected_origins = [(d['doc_id'], p['i']) for d in docs if d['doc_id'] in {a['doc_id'] for a in details} for p in d['paragraphs']]
    if Counter(actual_origins) != Counter(expected_origins):
        issues.append(issue('新增30篇', '段落来源覆盖异常', 'paragraph_sources', '来源映射缺失、重复或越界', '重新导出来源映射', '错误'))
    image_count, low_score, nonempty, included_low = 0, 0, 0, 0
    original_parts, rejected, accepted = Counter(), Counter(), Counter()
    evidence_checks = []
    for d in details:
        for part in d['parts']:
            kind = part['kind']
            original_parts[kind] += 1
            (accepted if part['included'] else rejected)[kind] += 1
            if kind == 'image':
                image_count += 1
                result = part['ocr']
                if result['status'] not in ('cached', 'recognized'):
                    issues.append(issue(d['doc_id'], 'OCR未完成', 'image', result['status'], '核实缓存', '错误'))
                lines = [l for l in result.get('response',{}).get('lines',[]) if l.get('text','').strip()]
                low = sum(l['confidence'] < .8 for l in lines)
                low_score += low
                nonempty += len(lines)
                included_low += low if part['included'] else 0
                evidence_checks.append((d['doc_id'], part['source'], result.get('source_sha256')))
                if part['included'] and low:
                    issues.append(issue(d['doc_id'], '纳入图片存在低分OCR', f"image[{part['image_index']}]", f'{low}/{len(lines)}非空行得分<0.8', '结合原图抽查；得分不是正确概率，不自动删除'))
            if kind == 'comment' and part['included'] and (not part.get('author_match') or part['effective_chars'] <= 30):
                issues.append(issue(d['doc_id'], '评论纳入规则不符', 'comment', part['source'], '检查作者身份或长度门槛', '错误'))
            if kind == 'image' and part['included'] and part['effective_chars'] <= 30:
                issues.append(issue(d['doc_id'], '图片纳入规则不符', 'image', part['source'], '检查长度门槛', '错误'))
    raw_notes, raw_images, raw_comments = 0, 0, 0
    if args.deep:
        paths.prepare_archive()
        evidence_checks.extend((d['doc_id'], d['raw_html_path'], None) for d in docs if d['raw_html_path'])
        # 评论与图片索引、原始笔记必须存在；不以评论条数推断全站评论完整性。
        detail_lookup = {(d['doc_id'].split('-')[0], d['note_id']): d for d in details}
        seen_raw = set()
        for group in load(CLEAN / 'config.production.json')['groups']:
            group_dir = (CLEAN / group['input_dir']).resolve()
            for notes_file in group_dir.rglob('notes_*.json'):
                mapper = paths.archive_relocator(paths.XHS_CACHE)
                for note in load(notes_file):
                    identity = (group['scenic_code'], note['note_id'])
                    if identity in seen_raw:
                        continue
                    seen_raw.add(identity)
                    raw_notes += 1
                    detail = detail_lookup.get(identity)
                    if not detail:
                        issues.append(issue(note['note_id'], '原始笔记未进入清洗', 'notes', group['scenic_code'], '检查输入与编号映射', '错误'))
                        continue
                    body = next(p for p in detail['parts'] if p['kind'] == 'body')
                    if body['raw_text'] != (note.get('content') or ''):
                        issues.append(issue(detail['doc_id'], '原始正文与清洗输入不一致', 'raw_text', '原始笔记正文不同', '检查输入版本', '错误'))
                    for field in ('image_manifest', 'comment_manifest'):
                        if note.get(field):
                            manifest = Path(mapper(note[field]))
                            evidence_checks.append((detail['doc_id'], str(manifest), None))
                            if not manifest.is_file():
                                continue
                            index = load(manifest)
                            if field == 'image_manifest':
                                raw_images += index['saved_count']
                                if index.get('missing_count'):
                                    issues.append(issue(detail['doc_id'], '原始图片采集缺失', 'images', index['missing_count'], '记录采集边界，不自动重访'))
                                processed = sum(p['kind'] == 'image' for p in detail['parts'])
                                if processed != len(index['images']):
                                    issues.append(issue(detail['doc_id'], '图片处理覆盖不一致', 'images', f"索引{len(index['images'])}，处理{processed}", '检查缺失文件和OCR输入', '错误'))
                            else:
                                comments_file = manifest.parent / 'comments.json'
                                evidence_checks.append((detail['doc_id'], str(comments_file), None))
                                if comments_file.is_file():
                                    comments = load(comments_file)
                                    raw_comments += len(comments)
                                    processed = [p['raw_record'] for p in detail['parts'] if p['kind']=='comment']
                                    if processed != comments:
                                        issues.append(issue(detail['doc_id'], '评论处理覆盖不一致', 'comments', f'原始{len(comments)}，处理{len(processed)}', '核对去重和笔记归属过滤原因', '错误'))
        if seen_raw != set(detail_lookup):
            issues.append(issue('新增数据', '原始输入覆盖不一致', 'notes', '原始笔记与清洗笔记集合不一致', '核对配置与输入', '错误'))
        for doc_id, value, digest in evidence_checks:
            p = paths.evidence_path(value)
            if not p.is_file():
                issues.append(issue(doc_id, '原始证据缺失', 'evidence', str(value), '检查归档', '错误'))
            elif digest and paths.sha256(p) != digest:
                issues.append(issue(doc_id, '原始图片哈希不符', 'evidence', str(value), '检查图片和OCR缓存是否匹配', '错误'))
    summary = {
        'date': datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
        'document_count': len(docs), 'characters': sum(len(d['content']) for d in docs),
        'paragraphs': sum(len(d['paragraphs']) for d in docs), 'counts': counts,
        'missing': {k: sum(not d.get(k) for d in docs) for k in ('title','author','publish_date','crawl_time','content','source_url','raw_html_path')},
        'issue_levels': dict(Counter(i['严重程度'] for i in issues)),
        'issue_rules': dict(Counter(i['检测项'] for i in issues)),
        'duplicate_types': dict(Counter(t for row in duplicates for t in row['类型'].split('；'))),
        'noise_matches': {name: sum(len(list(pattern.finditer(d['content']))) for d in docs) for name,pattern in NOISE_PATTERNS.items()},
        'input_parts': dict(original_parts), 'included_parts': dict(accepted), 'excluded_parts': dict(rejected),
        'ocr_images': image_count, 'nonempty_ocr_lines': nonempty, 'ocr_lines_score_below_0_8': low_score,
        'included_image_lines_score_below_0_8': included_low,
        'deep_evidence_check': args.deep, 'evidence_checks': len(evidence_checks) if args.deep else 0,
        'raw_coverage': {'notes':raw_notes,'saved_images':raw_images,'saved_comments':raw_comments} if args.deep else '未执行',
        'manual_review_status': '待人工复核', 'source_sha256': paths.sha256(folder / 'documents.json'),
        'automatic_structure_passed': not any(i['严重程度'] == '错误' for i in issues),
    }
    write_csv(dest / '质量问题明细.csv', ISSUE_FIELDS, issues)
    write_csv(dest / '重复候选.csv', DUP_FIELDS, duplicates)
    review = []
    for d, reason in select_review(docs, details):
        review.append(dict(zip(['文档编号','景区','抽查理由','原始证据','正文摘录','误删','漏删','OCR错字及顺序','内容相关性','结论','复核人','复核日期'],
                              [d['doc_id'],d['scenic_area'],reason,d['raw_html_path'],d['content'][:160],'待复核','待复核','待复核','待复核','待人工复核','',''])))
    # 复核运行不得覆盖用户已经填写的人工记录。
    review_file = dest / '人工抽查记录_已确认.csv'
    if not review_file.exists():
        review_file = dest / '人工抽查记录.csv'
    if not review_file.exists():
        write_csv(review_file, list(review[0]), review)
        (dest / '人工抽查任务版本.json').write_text(json.dumps({'documents_sha256':summary['source_sha256'],'status':'仅生成待复核任务，非人工结论'},ensure_ascii=False,indent=2),encoding='utf-8')
    confirmed = dest / '人工抽查确认记录.json'
    confirmation = load(confirmed) if confirmed.exists() else {}
    if confirmation.get('record_file') == '人工抽查记录_已确认.csv':
        review_file = dest / confirmation['record_file']
    with review_file.open(encoding='utf-8-sig', newline='') as f:
        manual_rows = list(csv.DictReader(f))
    manual_done = (len(manual_rows) == len(review) and bool(review)
                   and {r['文档编号'] for r in manual_rows} == {r['文档编号'] for r in review}
                   and all(r['结论'] == '通过' for r in manual_rows)
                   and confirmation.get('documents_sha256') == summary['source_sha256'])
    if manual_done:
        summary['manual_review_status'] = f'用户确认抽查完成：{len(manual_rows)}篇通过'
    manual_note = (f'用户已确认抽查的{len(manual_rows)}篇均未发现问题，已代填“通过”，抽查人按要求留空。当前结论文件：{review_file.name}。该结论来自用户确认，非程序自动判定；不代表全量文本验收或自动关闭质检问题。'
                   if manual_done else '人工抽查仍待确认；现有表格如对应旧版数据，需要重新核对证据。')
    if manual_done and confirmation.get('carry_forward_note'):
        manual_note += confirmation['carry_forward_note']
    (dest / '质量检测汇总.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    examples = ['# 清洗前后代表性对比', '', '以下为真实原始片段与规则处理结果；不代表已经人工核验OCR。完整明细见上级cleaning_examples.md。', '']
    labels = [('已知表情', lambda p: bool(rules.XHS_EMOJI.search(p['raw_text']))),
              ('话题标记', lambda p: '[话题]' in p['raw_text']),
              ('全角格式', lambda p: bool(re.search('[\uff01-\uff5e]',p['raw_text']))),
              ('多余空白', lambda p: bool(re.search(r'\t|\n\s*\n',p['raw_text']))),
              ('图片OCR纳入', lambda p: p['kind']=='image' and p['included']),
              ('作者评论纳入', lambda p: p['kind']=='comment' and p['included']),
              ('短附加文本排除', lambda p: p['kind']!='body' and 0 < p['effective_chars'] <= 30),
              ('正常方括号保留', lambda p: '[往返时间-游玩路线-行李寄存]' in p['clean_text'])]
    for label, predicate in labels:
        selected = next(((d,p) for d in details for p in d['parts'] if predicate(p)),None)
        if not selected:
            continue
        d,p = selected
        raw = p['raw_text']
        # 从命中位置附近截取片段，再执行同一规则，保证前后片段直接对应。
        hit = rules.XHS_EMOJI.search(raw) if label=='已知表情' else re.search(r'\[话题\]',raw) if label=='话题标记' else None
        start = max(0,hit.start()-60) if hit else 0
        fragment = raw[start:start+360]
        after = rules.clean_text(fragment)[0]
        examples.extend([f'## {label}：{d["doc_id"]} / {p["kind"]}', '', f'整部分有效字符：{p["effective_chars"]}；纳入正文：{p["included"]}。以下仅为对应片段。', '', '清洗前：', '', '```text', fragment, '```', '', '清洗后：', '', '```text', after or '（清洗后为空）', '```', ''])
    (dest / '清洗前后代表性对比.md').write_text('\n'.join(examples),encoding='utf-8')
    lines = ['# P1数据质量检测报告', '', f'检测日期：{summary["date"]}。全程离线；不修改正式正文及原始文件。', '',
             f'检测当前正式数据：{len(docs)}篇、{summary["characters"]}字符、{summary["paragraphs"]}段。', '',
             '## 自动检查结论', '',
             f'结构及一致性硬错误：{summary["issue_levels"].get("错误",0)}项；复核提示：{summary["issue_levels"].get("复核",0)}项；缺失等记录项：{summary["issue_levels"].get("记录",0)}项。', '',
             '提示条数不是错误率；同一文档、同一图片可产生多个提示。结构通过不等于语义合格，is_valid也不是人工验收标记。', '',
             '| 景区 | 官方路线 | 游客游记 | 最低数量要求 |','|---|---:|---:|---|']
    lines.extend(f'| {r["scenic_area"]} | {r["official"]} | {r["travel"]} | {"满足" if r["official"]>=1 and r["travel"]>=3 else "不满足"} |' for r in counts)
    lines.extend(['', '## 检测范围与规则', '',
                  '- 全量检查字段集合、类型、编号、景区与类型枚举、日期、来源链接、正文统计及段落连续位置。',
                  '- JSON、文档CSV、段落CSV、Excel逐值对照，检查意外公式。',
                  '- 检查HTML、URL、已知及疑似表情、话题标记、控制字符、乱码、空白与重复标点；全角残留属于跨来源格式提示。',
                  '- 全47篇两两比较：规范化正文相等、规范化来源相同、三字符集合Jaccard≥0.8；短文≥200字符且集合包含率≥0.9列为包含候选。跨文档相同段落仅在规范化长度≥30时列出。相似内容不直接删除。',
                  '- 检查新增文档OCR完成状态、作者评论及图片>30有效字符门槛、段落来源完整覆盖；统计所有纳入/排除部分。',
                  '- 原17篇合并时统一全角ASCII、派生统计与非负净缩减量；历史原件不改动，不对旧正文重复删词或重排。新增30篇另核对原始笔记、图片与评论处理覆盖。',
                  '- 每篇短段落(≤10字符)比例≥50%、图片非空OCR行得分<0.8、清洗删减超过50%列为复核线索，阈值不是正确性判定。',
                  f'- 原始证据与图片哈希检查：{"已执行" if args.deep else "未执行；使用--deep启用"}。', '',
                  '## 噪声残留统计', '', '| 检测项 | 命中次数 |', '|---|---:|'])
    lines.extend(f'| {k} | {v} |' for k,v in summary['noise_matches'].items())
    lines.extend(['', '## 元数据缺失', '', '| 字段 | 缺失文档数 |', '|---|---:|'])
    lines.extend(f'| {k} | {v} |' for k,v in summary['missing'].items())
    lines.extend(['',
                  '## 本次发现', ''])
    lines.extend(f'- {k}：{v}项。' for k,v in summary['issue_rules'].items())
    lines.extend(['', f'OCR共{image_count}张，非空识别行{nonempty}条，其中得分<0.8的{low_score}条；纳入图片中相应行数{included_low}条。得分不是校准概率，不能换算准确率。', '',
                  'noise_char_removed统一为max(0,raw_char_len-clean_char_len)，表示非负净缩减量，不能解释为实际删除字符次数；历史负值修正保存在旧17篇格式统一记录中。', '',
                  f'深度检查的批处理覆盖：{raw_notes}篇原始笔记、{raw_images}张已保存图片、{raw_comments}条已保存评论。' if args.deep else '本次未执行原始文件覆盖检查。', '',
                  '## 人工复核与处理', '',
                  '人工抽查记录为每景区1篇官方、2篇游客游记，游客游记兼顾正文与图片占比。重复运行保留已存在的人工表，数据更新后应核对抽查证据。' + manual_note, '',
                  '原始无标题、历史日期/采集时间缺失应先查证据，无法补齐时如实保留；不编造值。OCR断行不自动合并，短景点名不直接删除。', '',
                  '## 与P1报告衔接', '',
                  '1. 数据来源与规模：引用上方三景区数量表，说明原17篇与新增30篇来源。',
                  '2. 清洗实现：引用数据清洗README规则与清洗前后代表性对比；解释30字门槛、行去重、原始证据保留。',
                  '3. 质量验证：引用本报告自动检查结论、缺失统计与重复候选；人工表填写完成后再报告抽查结果。',
                  '4. 问题与局限：如实报告元数据缺失、OCR错字/断行及评论采集范围，不写“全部文本准确无误”。',
                  '5. 复现：仓库根运行python tools/quality_p1.py --deep；默认命令无需大归档，--deep需要本地原始归档。', '',
                  '本报告不改写历史报告，历史P2—P7结果不能作为新语料的验证证据。'])
    (dest / '质量检测报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    insert = ['# P1报告中的质检内容', '',
              '以下材料用于更新原报告P1部分；不直接覆盖旧Word报告。' + manual_note, '',
              '| 老师要求 | 本次证据 | 放入原报告的位置 |', '|---|---|---|',
              '| 批量读取与处理 | 质量检测汇总.json的raw_coverage、input_parts、included_parts与excluded_parts | 采集与清洗流程 |',
              '| HTML、网址、元数据及异常符号清理 | 噪声残留统计、质量问题明细、清洗前后代表性对比 | 清洗规则及效果 |',
              '| 空白、换行、标点、编码与冗余 | 噪声统计、重复候选、短段落复核项 | 预处理规则与局限 |',
              '| 空值、重复、异常字段 | 元数据缺失表、重复候选、旧版负删减统计 | 数据质量检查 |',
              '| 规则解释与代码结构 | 数据清洗README、rules.py、pipeline.py及tools/quality_p1.py | 实现方法与关键代码 |',
              '| 结构化输出 | JSON/CSV/Excel逐值核验、段落连续位置及来源映射检查 | 输出格式与复现 |',
              '| 清洗前后对比 | 清洗前后代表性对比.md中的8个真实案例 | 选择3～4个案例正文展示，其余作附件 |',
              '| 景区数量、缺失、重复、格式 | 质量检测报告.md及质量检测汇总.json | 结果表与问题说明 |', '',
              '## 可引用的自动检测结果', '',
              f'本阶段整合{len(docs)}篇景区文本，共{summary["characters"]}字符、{summary["paragraphs"]}段。三个景区均包含至少1篇官方路线及3篇游客游记。对全部文档执行字段、类型、段落位置、来源、缺失及重复检查，并逐值比较JSON、CSV与Excel输出，发现结构及跨格式硬错误{summary["issue_levels"].get("错误",0)}项。', '',
              f'元数据存在标题缺失{summary["missing"]["title"]}篇、发布日期缺失{summary["missing"]["publish_date"]}篇、采集时间缺失{summary["missing"]["crawl_time"]}篇，原始证据不能确认的值暂不补造。完整问题定位与处理建议保存在质量问题明细.csv。', '',
              f'对新增小红书文本保留逐部分处理记录：{original_parts["body"]}份正文、{original_parts["image"]}张图片OCR及{original_parts["comment"]}条评论；正文最终纳入{accepted["image"]}张图片文字、{accepted["comment"]}条作者评论，其余内容保留在辅助资料中。', '',
              f'OCR非空识别行{nonempty}条，其中{low_score}条得分低于0.8。该得分不等于经过校准的正确概率，低分与短段落仅作为复核线索。OCR错字、断行和语义相关性尚需对照原图检查，不能宣称全部识别准确。', '',
              '## 还不能直接写入的结论', '',
              '- 全量OCR准确率、清洗误删/漏删率：尚无逐字标注，9篇抽查结论不能替代全量准确率。',
              '- 全部文本完整、没有任何噪声：规则检查不能证明绝对不存在问题。',
              '- 原17篇与新增30篇全部采用同一抽取及去噪算法：当前仅统一格式、派生计数与净缩减量，原有来源抽取方式仍不同。', '',
              '原报告中的17篇规模、旧图表和旧统计需替换为本次数据；保留原17篇来源说明，并补充小红书浏览器采集、图片OCR、作者评论融合及离线复现过程。']
    (dest / '报告融入说明.md').write_text('\n'.join(insert)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0 if summary['automatic_structure_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
