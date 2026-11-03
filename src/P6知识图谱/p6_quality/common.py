import hashlib
import json


def compact(value):
    # Preserve the historic time ID encoding while migrating ownership.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(compact(value).encode('utf-8')).hexdigest()


def require(condition, message):
    """完整性检查用它，不要用 assert。

    assert 在 python -O 下整条被编译掉，而这几处是"图里节点边不自洽就别导出"
    的最后一道闸，不能因为启动参数就消失。
    """
    if not condition:
        raise ValueError(message)
