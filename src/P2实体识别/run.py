"""P2正式运行入口，无历史版本切换。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from p2_entities.pipeline import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--figures', type=Path)
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    checked, stats = run(args.output, args.figures, not args.no_plots)
    print(json.dumps({'validation': checked, 'summary': stats}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
