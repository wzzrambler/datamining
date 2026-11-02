"""本地加载诊断：只记录请求路径和状态，不记录凭据、查询串或请求正文。"""
import json
import re
import time
from urllib.parse import urlsplit


def safe_url(value):
    try:
        parsed = urlsplit(value)
        return f'{parsed.scheme}://{parsed.hostname or ""}{parsed.path}' if parsed.scheme in ('http', 'https') else parsed.scheme + ':'
    except ValueError:
        return '[invalid-url]'


def safe_error(value):
    text = re.sub(r'https?://[^\s<>"\']+', lambda m: safe_url(m.group()), str(value))
    return re.sub(r'(?i)\b(token|cookie|authorization|xsec_token)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', text)[:2000]


class LoadDiagnostics:
    def __init__(self, root):
        self.root = root
        self.active = {}
        self.counts = {}
        self.started = time.monotonic()

    def write(self, event, **data):
        self.counts[event] = self.counts.get(event, 0) + 1
        with (self.root / 'network.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'seconds': round(time.monotonic()-self.started, 3), 'event': event, **data}, ensure_ascii=False)+'\n')

    def request(self, request):
        row = {'url': safe_url(request.url), 'method': request.method, 'type': request.resource_type}
        self.active[request] = (time.monotonic(), row)
        self.write('request', **row)

    def response(self, response):
        self.write('response', url=safe_url(response.url), status=response.status, type=response.request.resource_type)

    def end(self, request, failed=False):
        started, row = self.active.pop(request, (time.monotonic(), {'url': safe_url(request.url)}))
        self.write('failed' if failed else 'finished', **row,
                   duration=round(time.monotonic()-started, 3),
                   error=safe_error(request.failure or '') if failed else '')

    def snapshot(self, page, reason):
        # 在关闭浏览器之前保存，避免把主动关闭造成的取消误认为加载根因。
        result = {'reason': reason, 'counts': dict(self.counts),
                  'pending': [{**row, 'seconds': round(time.monotonic()-started, 3)} for started, row in self.active.values()]}
        if page is not None:
            result['url'] = safe_url(page.url)
            try:
                result['page'] = page.evaluate('''() => ({readyState: document.readyState,
                    scripts: [...document.scripts].filter(n => n.src).map(n => ({url: n.src.split(/[?#]/)[0]})),
                    images: {total: document.images.length, loaded: [...document.images].filter(n => n.complete && n.naturalWidth > 0).length}})''')
            except Exception as exc:
                result['snapshot_error'] = type(exc).__name__
        target = self.root / 'load_diagnostics.json'
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return str(target.resolve())
