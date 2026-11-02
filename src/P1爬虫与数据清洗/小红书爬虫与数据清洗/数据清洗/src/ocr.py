"""PaddleOCR HTTP 客户端；默认仅使用本地缓存。"""
import hashlib
import io
import json
import math
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def validate_response(data):
    if not isinstance(data, dict) or not isinstance(data.get('lines'), list):
        raise ValueError('OCR 响应缺少 lines 列表')
    size = data.get('image', {})
    if any(type(size.get(k)) is not int or size[k] <= 0 for k in ('width', 'height')):
        raise ValueError('OCR 响应图像尺寸无效')
    for line in data['lines']:
        if not isinstance(line, dict) or not isinstance(line.get('text'), str):
            raise ValueError('OCR line.text 无效')
        score = line.get('confidence')
        box = line.get('box')
        if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('OCR confidence 无效')
        if not isinstance(box, list) or len(box) != 4 or any(
            not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p)
            for p in box
        ):
            raise ValueError('OCR 四点框无效')
    return data


class OCRClient:
    def __init__(self, settings, cache, live=False):
        self.settings, self.cache, self.live = settings, Path(cache), live
        self.stopped = False
        self.ready = False
        self.session = requests.Session()
        self.session.trust_env = False
        endpoint = settings['base_url'].rstrip('/')
        url = urlsplit(endpoint)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('OCR base_url 必须是无凭据、无查询参数的 HTTP(S) 地址')
        self.endpoint = endpoint

    def close(self):
        self.session.close()

    def recognize(self, path):
        path = Path(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        namespace = hashlib.sha256(self.settings['cache_namespace'].encode()).hexdigest()[:12]
        cache_path = self.cache / namespace / f'{digest}.json'
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            validate_response(cached['response'])
            if cached.get('source_sha256') != digest:
                raise ValueError('OCR 缓存图片校验失败')
            return {**cached, 'status': 'cached', 'cache_file': str(cache_path)}
        if not self.live or self.stopped:
            return {'status': 'pending', 'source_sha256': digest, 'reason': '未启用在线 OCR 或本轮服务已停止'}
        received = None
        try:
            timeout = (self.settings['connect_timeout_seconds'], self.settings['read_timeout_seconds'])
            if not self.ready:
                health = self.session.get(self.endpoint + '/health', timeout=timeout, allow_redirects=False)
                if health.status_code != 200 or health.json().get('status') != 'ready':
                    raise ValueError(f'OCR 健康检查未就绪，HTTP {health.status_code}')
                self.ready = True
            with Image.open(path) as image:
                if image.width * image.height > self.settings['max_pixels']:
                    raise ValueError('图片超过客户端像素限制')
                # 协议首期只支持 JPEG/PNG；WebP 离线转 PNG，不执行 EXIF 自动旋转。
                if image.format in ('JPEG', 'PNG'):
                    payload = path.read_bytes()
                    mime = 'image/jpeg' if image.format == 'JPEG' else 'image/png'
                else:
                    buffer = io.BytesIO()
                    image.convert('RGB').save(buffer, format='PNG')
                    payload, mime = buffer.getvalue(), 'image/png'
                width, height = image.size
            request_id = 'clean-' + digest[:24]
            prepared = self.session.prepare_request(requests.Request('POST',
                self.endpoint + '/v1/ocr',
                files={'image': ('image.jpg' if mime == 'image/jpeg' else 'image.png', payload, mime)},
                data={'request_id': request_id},
            ))
            # 实际上限约束整个 multipart 请求体，包含 boundary 和字段头。
            if len(prepared.body) > self.settings['max_request_bytes']:
                raise ValueError('整个 multipart 请求体超过客户端大小限制')
            started = time.perf_counter()
            response = self.session.send(prepared, timeout=timeout, allow_redirects=False)
            round_trip_ms = (time.perf_counter() - started) * 1000
            received = {'http_status': response.status_code, 'body': response.text}
            data = response.json()
            if response.status_code != 200:
                write_json(self.cache / 'errors' / f'{digest}.json', {'http_status': response.status_code, 'response': data})
                raise ValueError(f'OCR HTTP {response.status_code}')
            validate_response(data)
            if data.get('request_id') != request_id or data['image'] != {'width': width, 'height': height}:
                raise ValueError('OCR request_id 或解码尺寸不匹配')
            result = {'status': 'recognized', 'source_sha256': digest, 'upload_mime': mime,
                      'request_body_bytes': len(prepared.body),
                      'round_trip_ms': round_trip_ms, 'response': data}
            write_json(cache_path, result)
            return {**result, 'cache_file': str(cache_path)}
        except (requests.RequestException, ValueError, OSError) as exc:
            self.stopped = True
            if received is not None:
                write_json(self.cache / 'errors' / f'{digest}_raw.json', received)
            return {'status': 'error', 'source_sha256': digest, 'reason': type(exc).__name__ + ': ' + str(exc)}
