import argparse
import json
import sys
from pathlib import Path

from .pipeline import run


def main():
    parser = argparse.ArgumentParser(description='小红书归档清洗；默认离线，不连接 OCR 或小红书')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parent.parent / 'config.production.json')
    parser.add_argument('--ocr', choices=('cache-only', 'live'), default='cache-only', help='live 才访问配置的 OCR 服务')
    args = parser.parse_args()
    result = run(args.config, live=args.ocr == 'live', progress=lambda msg: print(msg, file=sys.stderr, flush=True))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result['ocr_stopped'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
