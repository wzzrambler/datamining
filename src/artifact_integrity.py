"""Portable text fingerprints and repository paths; binary hashes stay exact."""
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath

TEXT_SUFFIXES = {'.json', '.jsonl', '.csv', '.md', '.py', '.txt', '.yml', '.yaml',
                 '.html', '.svg', '.js', '.css', '.cypher'}


def file_sha256(path):
    path = Path(path)
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def matches_sha256(path, expected):
    """Accept historical LF/CRLF byte hashes, without ignoring any other change."""
    path = Path(path)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() == expected:
        return True
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    lf = data.replace(b'\r\n', b'\n')
    candidates = [lf, lf.replace(b'\n', b'\r\n')]
    if path.suffix.lower() == '.csv':
        # csv.writer uses CRLF for records, but quoted source excerpts keep LF.
        # Restore only record separators, preserving every quote and field byte.
        restored = bytearray()
        quoted = False
        for byte in lf:
            if byte == 34:
                quoted = not quoted
            if byte == 10 and not quoted:
                restored.append(13)
            restored.append(byte)
        candidates.append(bytes(restored))
    return any(hashlib.sha256(value).hexdigest() == expected for value in candidates)


def repository_path(root, name):
    """Read both old Windows manifest keys and portable POSIX keys safely."""
    root = Path(root).resolve()
    name = str(name).replace('\\', '/')
    if (PureWindowsPath(name).drive or PurePosixPath(name).is_absolute()
            or '..' in PurePosixPath(name).parts):
        raise ValueError(f'清单路径必须位于仓库内：{name}')
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f'清单路径必须位于仓库内：{name}')
    return path
