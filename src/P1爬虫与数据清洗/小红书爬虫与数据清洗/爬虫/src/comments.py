"""从已经归档的评论响应提取数据，不请求接口。"""
import csv
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs

COMMENT_FIELDS = ['note_id', 'comment_id', 'parent_comment_id', 'content', 'author', 'author_id',
                  'likes', 'created_at', 'declared_reply_count', 'reply_has_more', 'source_file']


def parse_comments(folder):
    folder = Path(folder)
    meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    nid = meta.get('note_id')
    if meta.get('kind') != 'detail':
        return {}
    records, pages = {}, []
    for response in meta['responses']:
        if '/comment/' not in urlparse(response['url']).path or not response['file'].endswith('.json'):
            continue
        source = folder / response['file']
        try:
            payload = json.loads(source.read_bytes())
        except (ValueError, UnicodeError):
            continue
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict) or payload.get('success') is False:
            continue
        comments = data.get('comments') or []
        if not isinstance(comments, list):
            continue
        query = parse_qs(urlparse(response['url']).query)
        root_id = data.get('root_comment_id') or query.get('root_comment_id', [''])[0]
        pages.append({'source': source.name, 'has_more': data.get('has_more'), 'root_comment_id': root_id})

        def visit(comment, parent=''):
            if not isinstance(comment, dict) or str(comment.get('note_id') or nid) != nid:
                return
            cid = comment.get('id') or comment.get('comment_id')
            if not cid:
                return
            user = comment.get('user_info') or {}
            records[str(cid)] = {'note_id': nid, 'comment_id': str(cid), 'parent_comment_id': str(parent),
                                 'content': comment.get('content') or '', 'author': user.get('nickname') or '',
                                 'author_id': user.get('user_id') or user.get('userId') or '',
                                 'likes': comment.get('like_count', ''), 'created_at': comment.get('create_time', ''),
                                 'declared_reply_count': comment.get('sub_comment_count', ''),
                                 'reply_has_more': comment.get('sub_comment_has_more'), 'source_file': source.name}
            for reply in comment.get('sub_comments') or []:
                visit(reply, str(cid))
        for comment in comments:
            visit(comment, root_id)
    output = folder / 'comments'
    output.mkdir(exist_ok=True)
    rows = list(records.values())
    (output / 'comments.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'comments.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COMMENT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    index = {'saved_count': len(rows), 'top_level_count': sum(not row['parent_comment_id'] for row in rows),
             'reply_count': sum(bool(row['parent_comment_id']) for row in rows), 'pages': pages,
             'scope': 'received_responses_only'}
    path = output / 'index.json'
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'saved_comment_count': len(rows), 'comment_manifest': str(path.resolve())}
