"""从检查点倒推发布清单（input_manifest.json）。

发布包只保留 raw_observations.json、不带检查点时，离线重建与回归测试都靠这张清单
还原"跑过哪些包、各是什么类型"。批次自己跑完就应当写一份，不必再手工补。
"""
from p5_agent.common import read_json

DONE = {'COMPLETED', 'COMPLETED_WITH_SKIPS'}


def build_manifest(run_dir, corpus, packages):
    states = {p.stem: read_json(p) for p in (run_dir / 'checkpoints').glob('*.json')}
    by_key = {p['context']['package_key']: p for p in packages}
    fragment_parent = {}
    agg = run_dir / 'aggregation_manifest.json'
    if agg.exists():
        spec = read_json(agg).get('replacement_manifest') or {}
        for parent, item in spec.items():
            for child in item.get('replacement_keys', []):
                fragment_parent[child] = parent
    effective, missing = [], []
    for key in sorted(states):
        state = states[key]
        if state['host']['status'] not in DONE:
            missing.append({'package_key': key, 'status': state['host']['status']})
            continue
        if key in by_key:
            ctx = by_key[key]['context']
            effective.append({'package_key': key, 'package_kind': ctx['package_kind'],
                              'status': state['host']['status'],
                              'doc_id': ctx.get('doc_id'),
                              'scenic_code': ctx.get('active_scenic_code')})
            continue
        parent = fragment_parent.get(key)
        if parent is None:
            missing.append({'package_key': key, 'status': 'NOT_IN_CORPUS'})
            continue
        parent_kind = by_key[parent]['context']['package_kind'] if parent in by_key else 'paragraph'
        effective.append({'package_key': key,
                          'package_kind': 'route_fragment' if parent_kind == 'route' else 'paragraph',
                          'status': state['host']['status'], 'doc_id': None, 'scenic_code': None,
                          'recovered_from': parent})
    return {'input_hashes': corpus.hashes,
            'original_packages': len(by_key),
            'effective_packages': effective,
            'attempted_packages': len(states),
            'incomplete_packages': missing,
            'coverage': corpus.coverage,
            'archived_requests_are_not_required_for_offline_export': True,
            'note': '由 p5_agent.release_manifest 从检查点倒推；分片包按父包类型登记'}
