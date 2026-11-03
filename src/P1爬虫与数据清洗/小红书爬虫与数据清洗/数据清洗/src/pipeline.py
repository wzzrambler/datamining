import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from .ocr import OCRClient, write_json
from .repo_support import paths, portable
from .rules import (STAT_FIELDS, TIME, clean_text, count_sentences, effective_len, grams,
                    normalize_legacy_documents, paragraphs, repeats_body, shingles, squash)

FIELDS = '''doc_id scenic_code scenic_area city category text_type text_type_cn doc_subtype
source_platform source_site source_url title author publish_date crawl_time extract_rule
raw_html_path raw_char_len clean_char_len html_tag_count noise_char_removed noise_line_removed
dup_line_removed invisible_removed html_tag_removed url_removed email_removed tel_removed
wxid_removed emoji_removed paragraph_count sentence_count has_time_expr content paragraphs
quality_flags is_valid is_near_duplicate'''.split()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def local_path(value, base):
    p = Path(value)
    return p if p.is_absolute() else base / p


def read_notes(path):
    if path.suffix.lower() == '.csv':
        import csv
        with path.open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    rows = load(path)
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
        raise ValueError(f'笔记输入必须是对象列表：{path}')
    return rows


def publish_date(value):
    if not value:
        return ''
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            return datetime.fromtimestamp(number / (1000 if number > 1e11 else 1), timezone(timedelta(hours=8))).date().isoformat()
        return datetime.fromisoformat(str(value)).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ''


def build_document(note, group, config, ocr, doc_id, note_base):
    parts, flags = [], []
    threshold = config['min_extra_chars']
    body = str(note.get('content') or '')
    parts.append({'kind': 'body', 'source': 'notes.content', 'raw_text': body})
    if note.get('content_scope') != 'detail':
        flags.append('输入不是详情正文')
    image_manifest = note.get('image_manifest')
    if image_manifest and local_path(image_manifest, note_base).exists():
        manifest_path = local_path(image_manifest, note_base)
        index = load(manifest_path)
        if index.get('missing_count', 0):
            flags.append(f"图片缺失{index['missing_count']}张")
        for image in sorted(index.get('images', []), key=lambda x: x['index']):
            variants = image.get('variants', [])
            if not variants:
                continue
            # 每张图片仅取一个已保存版本，避免同图多尺寸重复识别。
            candidates = [manifest_path.parent / v['file'] for v in variants]
            existing = [p for p in candidates if p.is_file()]
            if not existing:
                flags.append('图片文件缺失')
                continue
            image_path = max(existing, key=lambda x: x.stat().st_size)
            result = ocr.recognize(image_path)
            lines = result.get('response', {}).get('lines', [])
            text = '\n'.join(line['text'] for line in lines)
            parts.append({'kind': 'image', 'source': str(image_path), 'image_index': image['index'],
                          'ocr': result, 'raw_text': text})
            if result['status'] in ('pending', 'error'):
                flags.append('OCR待完成' if result['status'] == 'pending' else 'OCR失败')
    elif int(note.get('image_count') or 0):
        flags.append('图片索引缺失')

    comment_path = note.get('comment_manifest')
    comments = []
    if comment_path:
        comment_dir = local_path(comment_path, note_base).parent
        if (comment_dir / 'comments.json').exists():
            comments = load(comment_dir / 'comments.json')
        else:
            flags.append('评论文件缺失')
    seen_comments = set()
    for comment in comments:
        comment_id = str(comment.get('comment_id') or '')
        if not comment_id or comment_id in seen_comments:
            continue
        seen_comments.add(comment_id)
        if str(comment.get('note_id', '')) != str(note['note_id']):
            continue
        author_match = bool(note.get('author_id')) and comment.get('author_id') == note['author_id']
        eligible = config['comment_policy'] == 'all' or author_match
        parts.append({'kind': 'comment', 'source': comment_id, 'raw_record': comment,
                      'raw_text': str(comment.get('content') or ''), 'author_match': author_match,
                      'eligible': eligible})

    stats, seen_lines, merged, provenance = Counter(), set(), [], []
    raw_accepted = []
    body_norm, body_grams = '', set()
    for part in parts:
        cleaned, st = clean_text(part['raw_text'])
        part['clean_text'], part['effective_chars'] = cleaned, effective_len(cleaned)
        accept = bool(cleaned) and (part['kind'] == 'body' or part['effective_chars'] > threshold)
        if part['kind'] == 'comment' and not part['eligible']:
            accept = False
            part['exclude_reason'] = '非作者评论（当前策略）'
        elif not accept:
            part['exclude_reason'] = '清洗后为空或未超过长度阈值'
        part['included'] = accept
        part['stats'] = st
        if part['kind'] == 'body':
            raw_accepted.append(part['raw_text'])
            stats.update(st)
            body_norm = squash(cleaned)
            body_grams = grams(body_norm)
        if not accept:
            continue
        if part['kind'] != 'body':
            raw_accepted.append(part['raw_text'])
            stats.update(st)
        added = 0
        repeated = 0
        for line in cleaned.split('\n'):
            key = re.sub(r'\W+', '', line).casefold()
            # 图片是把作者正文拍成截图再 OCR 回来的，同一段话会以断行残片
            # 的形式二次进入语料，行级去重认不出断行差异，这里单独拦一次。
            if part['kind'] == 'image' and repeats_body(key, body_norm, body_grams):
                stats['dup_line_removed'] += 1
                repeated += 1
                continue
            if key in seen_lines:
                stats['dup_line_removed'] += 1
                continue
            seen_lines.add(key)
            merged.append(line)
            provenance.append({'doc_id': doc_id, 'para_id': len(merged), 'kind': part['kind'], 'source': part['source']})
            added += 1
        if repeated:
            part['ocr_repeat_lines'] = repeated
        part['included'] = bool(added)
        if not added:
            part['exclude_reason'] = '图片文字与作者正文重复' if repeated else '与前文重复'
    content = '\n'.join(merged)
    raw = '\n'.join(raw_accepted)
    if body and not parts[0]['clean_text']:
        flags.append('作者正文清洗后为空（含仅标签情况）')
    if not content:
        flags.append('空文本')
    elif len(content) < config['min_document_chars']:
        flags.append('正文过短')
    if not note.get('title'):
        flags.append('标题缺失')
    date = publish_date(note.get('published_at'))
    if not date:
        flags.append('发布日期缺失')
    if not TIME.search(content):
        flags.append('无时间表达')
    if not group['category']:
        flags.append('景区分类待确认')
    if '\ufffd' in content:
        flags.append('疑似乱码')
    row = {key: '' for key in FIELDS}
    row.update({key: group[key] for key in ('scenic_code', 'scenic_area', 'city', 'category')})
    row.update({key: str(note.get(key) or '') for key in ('title', 'author')})
    html_path = note.get('original_html_path') or note.get('rendered_html_path') or ''
    row.update(doc_id=doc_id, text_type='travel_note', text_type_cn='游客游记',
               source_platform='小红书', source_site=urlsplit(note.get('url', '')).hostname or '',
               source_url=str(note.get('url') or ''), publish_date=date,
               crawl_time=str(note.get('collected_at') or ''),
               extract_rule=f"作者正文+逐图OCR+评论；附加文本有效字符>{threshold}；评论策略={config['comment_policy']}；行去重（图片复读按正文比对剔除）",
               raw_html_path=str(local_path(html_path, note_base)) if html_path else '',
               raw_char_len=len(raw), clean_char_len=len(content), html_tag_count=0,
               noise_char_removed=max(0, len(raw) - len(content)),
               paragraph_count=len(merged), sentence_count=count_sentences(content),
               has_time_expr=int(bool(TIME.search(content))), content=content, paragraphs=paragraphs(content),
               quality_flags='；'.join(dict.fromkeys(flags)),
               is_valid=int(bool(content) and len(content) >= config['min_document_chars'] and note.get('content_scope') == 'detail'),
               is_near_duplicate=0)
    for key in STAT_FIELDS:
        row[key] = stats[key]
    return row, {'doc_id': doc_id, 'note_id': note['note_id'], 'parts': parts}, provenance


def run(config_path, live=False, progress=None):
    from .export import export
    config_path = Path(config_path).resolve()
    config, base = load(config_path), config_path.parent
    validate_config(config)
    relocate = lambda v: v
    if config.get('raw_archive'):
        cache = paths.prepare_archive(local_path(config['raw_archive'], base), local_path(config['archive_cache'], base))
        relocate = paths.archive_relocator(cache)
    output = local_path(config['output_dir'], base).resolve()
    original = local_path(config['existing_documents'], base).resolve()
    legacy = load(original)
    if not legacy or any(set(d) != set(FIELDS) for d in legacy):
        raise ValueError('原组 documents.json 与约定字段不一致')
    legacy = normalize_legacy_documents(legacy)
    if output == original.parent or original.parent in output.parents:
        raise ValueError('输出必须位于原组 processed 目录之外')
    auxiliary = output / config.get('auxiliary_dir_name', 'auxiliary')
    id_map_path = auxiliary / 'id_map.json'
    id_map = load(id_map_path) if id_map_path.exists() else {}
    used = {d['doc_id'] for d in legacy}
    if len(used) != len(legacy):
        raise ValueError('原组 doc_id 重复')
    if used.intersection(id_map.values()) or len(set(id_map.values())) != len(id_map):
        raise ValueError('编号映射与原组编号冲突，需人工核对')
    used.update(id_map.values())
    # 全部输入先读取、去重，再执行可选 OCR；采集中尚未完成的文件应另行生成稳定快照。
    inputs, seen_notes = [], set()
    for group in config['groups']:
        folder = local_path(group['input_dir'], base).resolve()
        direct = folder / 'notes.json'
        files = [direct] if direct.exists() else sorted(folder.rglob('notes_*.json'), reverse=True)
        if not files:
            raise ValueError(f'未发现 notes.json 或 notes_*.json：{folder}')
        for file in files:
            for note in read_notes(file):
                note = {k: relocate(v) for k, v in note.items()}
                note_id = str(note.get('note_id') or '')
                if not re.fullmatch(r'[0-9a-fA-F]{24}', note_id):
                    raise ValueError(f'无效 note_id：{file}')
                key = group['scenic_code'] + ':' + note_id
                if key in seen_notes:
                    continue
                seen_notes.add(key)
                inputs.append((key, note, group, file.parent))
    ocr = OCRClient(config['ocr'], local_path(config['ocr']['cache_dir'], base), live)
    rows, details, origins = [], [], []
    try:
        for key, note, group, note_base in inputs:
            if key not in id_map:
                n = 1
                while f"{group['scenic_code']}-NOTE-{n:02d}" in used:
                    n += 1
                id_map[key] = f"{group['scenic_code']}-NOTE-{n:02d}"
                used.add(id_map[key])
                write_json(id_map_path, id_map)
            if progress:
                progress(f"开始 {id_map[key]} ({len(rows)+1}/{len(inputs)})")
            row, detail, origin = build_document(note, group, config, ocr, id_map[key], note_base)
            rows.append(row)
            details.append(detail)
            origins.extend(origin)
            if progress:
                progress(f"完成 {row['doc_id']}：{row['clean_char_len']}字符，{row['paragraph_count']}段")
    finally:
        ocr.close()
    if config.get('portable_root'):
        rows, details, origins = portable(rows), portable(details), portable(origins)
    merged = legacy + rows
    duplicate_pairs = []
    sets = [shingles(d['content']) for d in merged]
    for j in range(len(legacy), len(merged)):
        for i in range(j):
            a, b = sets[i], sets[j]
            if a and b and len(a & b) / len(a | b) >= .8:
                merged[j]['is_near_duplicate'] = 1
                duplicate_pairs.append([merged[i]['doc_id'], merged[j]['doc_id']])
    write_json(auxiliary / 'raw_and_clean_parts.json', details)
    write_json(auxiliary / 'ocr_results.json', [
        {'doc_id': d['doc_id'], 'note_id': d['note_id'], **p}
        for d in details for p in d['parts'] if p['kind'] == 'image'])
    write_json(auxiliary / 'comments_original.json', [
        {'doc_id': d['doc_id'], **p['raw_record']}
        for d in details for p in d['parts'] if p['kind'] == 'comment'])
    write_json(auxiliary / 'paragraph_sources.json', origins)
    write_json(output / 'new_documents.json', rows)
    export(output, merged, duplicate_pairs, config, details)
    return {'new_documents': len(rows), 'merged_documents': len(merged),
            'valid_new_documents': sum(d['is_valid'] for d in rows),
            'ocr_stopped': ocr.stopped, 'output': str(output)}


def validate_config(config):
    for key in ('min_extra_chars', 'min_document_chars'):
        if type(config.get(key)) is not int or config[key] < 0:
            raise ValueError(f'{key} 必须为非负整数')
    if config.get('comment_policy') not in ('author_only', 'all'):
        raise ValueError('comment_policy 必须为 author_only 或 all')
    if not isinstance(config.get('groups'), list) or not config['groups']:
        raise ValueError('groups 不能为空')
    for group in config['groups']:
        for key in ('input_dir', 'scenic_code', 'scenic_area', 'city', 'category'):
            if not isinstance(group.get(key), str):
                raise ValueError(f'分组字段缺失：{key}')
        if not re.fullmatch('[A-Z]{2,4}', group['scenic_code']) or not group['scenic_area'].strip():
            raise ValueError('景区代码应为2—4位大写字母，景区名称不能为空')
    for key in ('connect_timeout_seconds', 'read_timeout_seconds', 'max_request_bytes', 'max_pixels'):
        value = config.get('ocr', {}).get(key)
        if type(value) is not int or value <= 0:
            raise ValueError(f'OCR {key} 必须为正整数')
    for key in ('cache_namespace', 'cache_dir', 'base_url'):
        if not isinstance(config.get('ocr', {}).get(key), str) or not config['ocr'][key].strip():
            raise ValueError(f'OCR {key} 不能为空')
