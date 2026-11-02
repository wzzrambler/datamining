"""Verify V2 replay, frozen inputs, token offsets and ablation consistency."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    os.chdir(ROOT)
    logs=ROOT/'.test_tmp/p4_verification'
    logs.mkdir(parents=True,exist_ok=True)
    protected=json.loads((ROOT/'outputs/P4/改进对照/V1/protected_hashes.json').read_text('utf-8'))
    assert all(sha(ROOT/p)==h for p,h in protected.items()), 'P1/P2/P3或人工文件在实施期间改变'
    env={**os.environ,'PYTHONIOENCODING':'utf-8'}
    tests=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests/P4','-v'],
                         capture_output=True,text=True,encoding='utf-8',env=env)
    test_log=tests.stdout+tests.stderr
    (logs/'v2_tests.log').write_text(test_log,encoding='utf-8')
    assert tests.returncode==0,test_log[-4000:]
    count=int(re.search(r'Ran (\d+) tests',test_log).group(1))
    automatic=[ROOT/'processed/P4共指消解'/f for f in ['coreferences.json','summary.json','validation.json','tokenization.json']]
    automatic += [ROOT/'outputs/P4'/f for f in ['P4自动消解检查表.csv','P4消解文本_仅供检查.json','P4自动结果说明.md']]
    evaluation=[ROOT/'outputs/P4/评估'/f for f in ['annotation_validation.json','evaluation.json','P4样本核验说明.md']]
    before={p:sha(p) for p in automatic+evaluation}
    for name,cmd in [('v2_replay',[sys.executable,'src/P4共指消解/run.py']),
                     ('v2_evaluation',[sys.executable,'src/P4共指消解/evaluate.py'])]:
        result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',env=env)
        (logs/(name+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
        assert result.returncode==0,result.stderr
    assert all(sha(p)==h for p,h in before.items()), '重复运行输出不一致'
    assert all(sha(ROOT/p)==h for p,h in protected.items()), '输入被改写'
    docs={d['doc_id']:d for d in json.loads((ROOT/'processed/P1爬虫数据清洗/documents.json').read_text('utf-8'))}
    tokenized=json.loads((ROOT/'processed/P4共指消解/tokenization.json').read_text('utf-8'))['documents']
    assert set(docs)=={d['doc_id'] for d in tokenized}
    ntokens=0
    for d in tokenized:
        source=docs[d['doc_id']]
        assert d['metadata']['version']=='0.42.1' and d['metadata']['HMM'] is False
        assert len(d['paragraphs'])==len(source['paragraphs'])
        for p,original in zip(d['paragraphs'],source['paragraphs']):
            assert p['para_id']==original['i']
            assert ''.join(t['text'] for t in p['tokens'])==original['text']
            cursor=original['start']
            for t in p['tokens']:
                assert t['start']==cursor and source['content'][t['start']:t['end']]==t['text']
                cursor=t['end'];ntokens+=1
            assert cursor==original['end']
    full=json.loads((ROOT/'processed/P4共指消解/coreferences.json').read_text('utf-8'))
    ablation=json.loads((ROOT/'outputs/P4/改进对照/D/sample_predictions.json').read_text('utf-8'))
    subset={r['reference_id']:r for r in ablation['records']}
    for r in full['records']:
        if r['reference_id'] in subset:
            assert r==subset.pop(r['reference_id']), '分阶段与全量预测不同'
    assert not subset
    comparison=json.loads((ROOT/'outputs/P4/改进对照/comparison.json').read_text('utf-8'))
    code={p.name:sha(p) for p in sorted((ROOT/'src/P4共指消解').glob('*.py'))}
    assert code==comparison['code_sha256']==full['implementation_sha256'], '代码快照不同'
    for path,h in comparison['stage_output_sha256'].items():
        assert sha(ROOT/'outputs/P4/改进对照'/path)==h
    report={'schema_version':'p4-reproducibility-2','tests_passed':count,
            'automatic_outputs_byte_identical_on_replay':len(automatic),
            'evaluation_outputs_byte_identical_on_replay':len(evaluation),
            'protected_files_unchanged':len(protected),'all_protected_files_unchanged':True,
            'all_document_tokens_roundtrip':True,'documents_tokenized':len(tokenized),'tokens_checked':ntokens,
            'full_run_matches_stage_D':True,'all_stage_output_hashes_valid':True,
            'implementation_sha256':code,'requirements_sha256':sha(ROOT/'requirements.txt'),
            'protected_sha256':protected,'output_sha256':{str(p.relative_to(ROOT)):h for p,h in before.items()}}
    (ROOT/'processed/P4共指消解/reproducibility.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if not k.endswith('sha256')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
