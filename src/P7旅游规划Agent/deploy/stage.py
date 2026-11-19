"""Stage the exact inputs needed to build the deployment images."""
from hashlib import sha256
from pathlib import Path
from shutil import copy2
import tempfile

P7 = Path(__file__).resolve().parents[1]
REPO = P7.parents[1]


def stage():
    graph = REPO / 'processed/P6知识图谱'
    paths = [
        REPO / 'src/artifact_integrity.py',
        REPO / 'processed/P1爬虫数据清洗/documents.json',
        graph / 'release.json',
        P7 / 'requirements.txt',
        P7 / 'alembic.ini',
    ]
    paths.extend(graph / 'cleaned-v1' / f'{name}.json' for name in (
        'graph', 'quality_manifest', 'routes', 'transport_legs', 'entities',
        'entity_time_summaries', 'condition_recommendation_summaries',
        'conditions', 'entity_advice_theme_summaries',
    ))
    for directory, suffixes in [('p7', {'.py'}), ('migrations', {'.py', '.sql'})]:
        paths.extend(f for f in (P7 / directory).rglob('*') if f.is_file() and f.suffix in suffixes)
    paths.extend(P7 / 'deploy' / name for name in ('Dockerfile.api', 'Dockerfile.web', 'nginx.conf', 'compose.yaml'))
    dist = P7 / 'web/dist'
    if not (dist / 'index.html').is_file():
        raise RuntimeError('请先执行 npm --prefix web run build')
    paths.extend(f for f in dist.rglob('*') if f.is_file() and f.suffix in {'.html', '.js', '.css'})

    scratch = REPO / '.test_tmp'
    scratch.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix='p7_1_release_', dir=scratch))
    token = sha256()
    for source in sorted(paths):
        rel = source.relative_to(REPO)
        token.update(rel.as_posix().encode())
        token.update(sha256(source.read_bytes()).digest())
        target_file = target / rel
        target_file.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, target_file)
    return target, token.hexdigest()[:12]


if __name__ == '__main__':
    directory, version = stage()
    print(directory)
    print(version)
