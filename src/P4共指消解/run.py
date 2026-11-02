"""Offline P4 entry point. Annotation files are never read by this command."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline import ROOT, BASE, run

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='全文P4规则消解，不读取人工标注，不需要选段参数')
    parser.add_argument('--root', type=Path, default=ROOT, help='独立仓库输入/输出根；主要用于离线测试')
    parser.add_argument('--config', type=Path, default=BASE/'config.json')
    args = parser.parse_args()
    run(args.root, args.config)
