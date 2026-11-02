"""P3 offline entry point; all paths are anchored to this repository."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import run

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='全文自动发现路线、关联P2 v2、建立时间层级并自动配对；无需选段配置')
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    run(plots=not args.no_plots)
