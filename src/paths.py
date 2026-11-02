"""仓库路径与原始归档定位；不启动任何实验阶段。"""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
PROCESSED = ROOT / 'processed'
P1 = PROCESSED / 'P1爬虫数据清洗'
OFFICIAL_RAW = DATA / '微信公众号与景区官方'
OUTPUTS = ROOT / 'outputs'
XHS_ARCHIVE = DATA / '小红书爬虫.zip'
XHS_CACHE = ROOT / '.cache/小红书爬虫'


def evidence_path(value):
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare_archive(archive=XHS_ARCHIVE, cache=XHS_CACHE):
    """本地安全解压，无网络；原始文件保持字节不变。"""
    archive, cache = Path(archive).resolve(), Path(cache).resolve()
    if not cache.is_relative_to(ROOT) or cache == ROOT:
        raise ValueError('解压缓存必须位于仓库内')
    fingerprint = sha256(archive)
    stamp = cache / '.archive.sha256'
    if stamp.exists():
        if stamp.read_text(encoding='utf-8').strip() != fingerprint:
            raise ValueError('归档版本与现有缓存不一致，请指定新的缓存目录')
        return cache
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not (cache / name).resolve().is_relative_to(cache):
                raise ValueError('归档中存在越界路径')
        if z.testzip() is not None:
            raise ValueError('原始归档CRC校验失败')
        cache.mkdir(parents=True, exist_ok=True)
        z.extractall(cache)
    stamp.write_text(fingerprint, encoding='utf-8')
    return cache


def archive_relocator(cache):
    cache = Path(cache)
    metadata = json.loads((cache / '原始归档说明.json').read_text(encoding='utf-8'))
    prefixes = [(metadata['source_root'], cache)]

    def relocate(value):
        if isinstance(value, str):
            normalized = value.replace('\\', '/')
            for old, target in prefixes:
                old = old.replace('\\', '/')
                if normalized == old or normalized.startswith(old + '/'):
                    return str(target) + normalized[len(old):]
        return value
    return relocate
