"""保存浏览器实际收到的响应与渲染快照；不发起请求、不执行离线网页。"""
import hashlib
import json
import re
import shutil
from urllib.parse import urlparse, unquote
from datetime import datetime
from pathlib import Path


class Archive:
    def __init__(self, root, keyword, search_root=None):
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.root = Path(root) / stamp
        self.search_root = Path(search_root) / stamp if search_root is not None else self.root / 'search'
        self.keyword = keyword
        self.current = None
        self.serial = 0
        self.image_urls = set()

    def begin(self, kind, note_id=None):
        self.serial += 1
        folder = self.root / 'posts' if kind == 'detail' else self.search_root
        if note_id:
            folder /= re.sub(r'[^A-Za-z0-9_-]', '_', note_id)[:80]
        folder /= f'{self.serial:04d}'
        folder.mkdir(parents=True, exist_ok=True)
        self.current = folder
        self.write_json('manifest.json', {'kind': kind, 'note_id': note_id, 'keyword': self.keyword,
                                        'responses': [], 'rendered_html': None,
                                        'image_pool': str((self.root / 'received_images/index.jsonl').resolve()),
                                        'captured_at': datetime.now().astimezone().isoformat()})
        return folder

    def write_json(self, filename, value, folder=None):
        ((folder or self.current) / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def response(self, url, status, resource_type, content_type, body, folder=None):
        folder = folder or self.current
        if not folder:
            return None
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        suffix = '.html' if resource_type == 'document' and 'html' in content_type else '.json'
        filename = f'response_{len(manifest["responses"])+1:04d}' + suffix
        path = folder / filename
        # 浏览器 API 提供的响应正文 bytes；不格式化、不插入脚本，不是 TCP 包或压缩传输字节。
        path.write_bytes(body)
        manifest['responses'].append({'file': filename, 'url': url, 'status': status,
                                      'resource_type': resource_type, 'content_type': content_type,
                                      'sha256': hashlib.sha256(body).hexdigest()})
        self.write_json('manifest.json', manifest, folder)
        return path

    def image_response(self, url, content_type, body):
        """仅接受回调已拿到的字节；此方法没有任何下载逻辑。"""
        mime = content_type.split(';')[0].strip().lower()
        suffix = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
                  'image/gif': '.gif', 'image/avif': '.avif', 'image/svg+xml': '.svg',
                  'image/bmp': '.bmp', 'image/x-icon': '.ico'}.get(mime)
        if not suffix or not body or url in self.image_urls:
            return None
        digest = hashlib.sha256(body).hexdigest()
        folder = self.root / 'received_images'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + suffix)
        if not path.exists():
            path.write_bytes(body)
        record = {'url': url, 'file': path.name, 'content_type': mime, 'size': len(body),
                  'sha256': digest, 'received_at': datetime.now().astimezone().isoformat()}
        with (folder / 'index.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        self.image_urls.add(url)
        return record

    def snapshot(self, html, state, links, url):
        if not self.current:
            raise RuntimeError('尚未开始归档阶段')
        (self.current / 'rendered.html').write_text(html, encoding='utf-8')
        self.write_json('state.json', state)
        self.write_json('links.json', links)
        manifest = json.loads((self.current / 'manifest.json').read_text(encoding='utf-8'))
        manifest.update(rendered_html='rendered.html', url=url,
                        snapshot_at=datetime.now().astimezone().isoformat())
        self.write_json('manifest.json', manifest)
        return self.current


def image_specs(data, note_id):
    """只取目标笔记的 image_list，不混入头像、推荐笔记和页面图标。"""
    found = []
    def visit(obj):
        nonlocal found
        if isinstance(obj, list):
            for item in obj:
                visit(item)
        elif isinstance(obj, dict):
            nid = obj.get('noteId') or obj.get('note_id') or obj.get('id')
            images = obj.get('imageList') or obj.get('image_list')
            if str(nid) == note_id and isinstance(images, list) and len(images) > len(found):
                found = images
            for value in obj.values():
                visit(value)
    visit(data)
    return found


def image_urls(obj):
    urls = []
    def visit(value):
        if isinstance(value, str) and value.startswith(('https://', 'http://')):
            urls.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key not in ('stream', 'livePhoto', 'live_photo'):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(obj)
    return list(dict.fromkeys(urls))


def asset_key(url):
    parsed = urlparse(url)
    host = parsed.hostname or ''
    if not (host == 'xhscdn.com' or host.endswith('.xhscdn.com')):
        return None
    name = unquote(parsed.path).rsplit('/', 1)[-1].split('!', 1)[0]
    return name if len(name) >= 16 else None


def attach_images(folder):
    """纯离线匹配并复制本轮已经接收的图片，不尝试填补未加载图片。"""
    folder = Path(folder)
    meta = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if meta.get('kind') != 'detail':
        return {}
    specs = []
    files = [folder / r['file'] for r in meta['responses'] if r['file'].endswith('.json')]
    if (folder / 'state.json').exists():
        files.append(folder / 'state.json')
    for path in files:
        try:
            candidates = image_specs(json.loads(path.read_bytes()), meta['note_id'])
            if len(candidates) > len(specs):
                specs = candidates
        except (ValueError, UnicodeError):
            continue
    pool = Path(meta['image_pool']) if meta.get('image_pool') else None
    received = []
    if pool and pool.exists():
        for line in pool.read_text(encoding='utf-8').splitlines():
            try:
                received.append(json.loads(line))
            except ValueError:
                continue
    image_dir = folder / 'images'
    image_dir.mkdir(exist_ok=True)
    entries = []
    for index, spec in enumerate(specs, 1):
        urls = image_urls(spec)
        keys = {key for url in urls if (key := asset_key(url))}
        matches = [r for r in received if r['url'] in urls or (asset_key(r['url']) and asset_key(r['url']) in keys)]
        variants = []
        seen = set()
        for record in matches:
            if record['sha256'] in seen:
                continue
            source = pool.parent / Path(record['file']).name
            if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != record['sha256']:
                continue
            dest = image_dir / f'{index:03d}_{len(variants)+1:02d}{source.suffix}'
            if not dest.exists():
                shutil.copyfile(source, dest)
            variants.append({'file': dest.name, 'source_url': record['url'], 'sha256': record['sha256'],
                             'size': record['size'], 'content_type': record['content_type'],
                             'matched_by': 'exact_url' if record['url'] in urls else 'asset_variant'})
            seen.add(record['sha256'])
        entries.append({'index': index, 'width': spec.get('width'), 'height': spec.get('height'),
                        'urls': urls, 'status': 'saved' if variants else 'not_received', 'variants': variants})
    saved = sum(bool(e['variants']) for e in entries)
    result = {'expected_count': len(specs), 'saved_count': saved, 'missing_count': len(specs)-saved,
              'policy': 'received_responses_only', 'images': entries}
    path = image_dir / 'index.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'image_count': len(specs), 'saved_image_count': saved, 'image_manifest': str(path.resolve())}


def choose_step(rng):
    p = rng.random()
    if p < .60:
        return 1
    if p < .80:
        return 2
    if p < .90:
        return 3
    if p < .95:
        return 4
    return 5


def extend_plan(plan, note_ids, limit, rng):
    """严格按发现顺序前进；尾部不够时保留抽签，发现更多记录后继续。"""
    plan = plan or {'cursor': -1, 'selected': [], 'skipped': [], 'pending': None}
    while len(plan['selected']) < limit:
        if plan['pending'] is None:
            step = choose_step(rng)
            plan['pending'] = {'position': plan['cursor'] + step, 'step': step}
        pending = plan['pending']
        target = pending['position']
        if target >= len(note_ids):
            break
        for pos in range(plan['cursor'] + 1, target):
            plan['skipped'].append({'note_id': note_ids[pos], 'position': pos + 1})
        plan['selected'].append({'note_id': note_ids[target], 'position': target + 1, 'step': pending['step']})
        plan.update(cursor=target, pending=None)
    return plan
