import hashlib
import json


def compact(value):
    # Preserve the historic time ID encoding while migrating ownership.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(compact(value).encode('utf-8')).hexdigest()
