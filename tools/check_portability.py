"""Run P2/P5/P6 in an isolated LF or CRLF checkout; no Git or services."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from artifact_integrity import TEXT_SUFFIXES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--line-ending', choices=('lf', 'crlf'), default='lf')
    args = parser.parse_args()
    parent = ROOT / '.test_tmp'
    parent.mkdir(exist_ok=True)
    failed = []
    with tempfile.TemporaryDirectory(dir=parent, prefix='portability_') as temp:
        snapshot = Path(temp)
        for name in ('src', 'tests', 'tools', 'processed'):
            shutil.copytree(ROOT / name, snapshot / name,
                            ignore=shutil.ignore_patterns('__pycache__', '.test_tmp', '.test-tmp',
                                                         '.cache', '.venv', '~$*'))
        for path in snapshot.rglob('*'):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                data = path.read_bytes().replace(b'\r\n', b'\n')
                if args.line_ending == 'crlf':
                    data = data.replace(b'\n', b'\r\n')
                path.write_bytes(data)
        env = dict(os.environ, PYTHONUTF8='1')
        for phase in ('P2', 'P5', 'P6'):
            print(f'=== Isolated {args.line_ending.upper()} checkout: {phase} ===', flush=True)
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover',
                                     '-s', f'tests/{phase}', '-v'], cwd=snapshot, env=env)
            if result.returncode:
                failed.append(phase)
    print(f'Portability failures: {failed}', flush=True)
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
