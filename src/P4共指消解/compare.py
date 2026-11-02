"""Development-only ablation on frozen annotations; never imported by run.py."""
import copy
import hashlib
import json
from pathlib import Path
from core import process_document
from evaluate import load_annotations, evaluate_rows
from pipeline import dump, digest, validate_records

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]


def run(root=ROOT):
    root=Path(root)
    out=root/'outputs/P4/改进对照'
    rows,validation,sample=load_annotations(root/'outputs/P4/P4样本原文与段落.json',
        root/'outputs/P4/P4人工标注样本.csv',root/'processed/P1爬虫数据清洗/documents.json')
    if validation['errors']:
        raise ValueError(validation['errors'])
    docs=[d for d in json.loads((root/'processed/P1爬虫数据清洗/documents.json').read_text('utf-8-sig')) if d['doc_id'] in sample]
    p2={d['doc_id']:d for d in json.loads((root/'processed/P2实体识别/entities.json').read_text('utf-8-sig'))['documents']}
    sources={(s['doc_id'],s['para_id']):s for s in json.loads((root/'processed/P1爬虫数据清洗/辅助资料/paragraph_sources.json').read_text('utf-8-sig'))}
    final=json.loads((BASE/'config.json').read_text('utf-8'))
    stages={}
    for name in ['A','B','C','D']:
        c=copy.deepcopy(final)
        if name in {'A','B'}:
            c.update(window_chars=60,same_paragraph=True)
            c['v2'].update(cross_paragraph=False,expanded_references=False)
        if name=='A':
            c['v2']['events']=False
        c['v2']['advanced_ranking']=name=='D'
        stages[name]=c
    result={'scope':'frozen_seen_development_samples_not_independent_test',
            'annotation_sha256':digest(root/'outputs/P4/P4人工标注样本.csv'),
            'sample_sha256':digest(root/'outputs/P4/P4样本原文与段落.json'),
            'code_sha256':{p.name:digest(p) for p in sorted(BASE.glob('*.py'))},
            'stage_results':{}}
    # Frozen V1 report remains an immutable comparison, including its original definitions.
    original=json.loads((out/'V1/评估/evaluation.json').read_text('utf-8'))
    result['stage_results']['V1']=original['rules']
    for name,config in stages.items():
        records=[]
        for doc in docs:
            records.extend(process_document(doc,p2[doc['doc_id']],sources,config))
        structural=validate_records(records,docs)
        report=evaluate_rows(rows,records)
        stage=out/name
        dump(stage/'config.json',config)
        dump(stage/'sample_predictions.json',{'config':config,'records':records,'structural_validation':structural})
        dump(stage/'evaluation.json',report)
        result['stage_results'][name]=report
        print(name,json.dumps(report['counts'],ensure_ascii=False),flush=True)
    if not all(digest(p)==h for p,h in validation['input_sha256'].items()):
        raise ValueError('核验过程中人工文件发生改变')
    result['stage_output_sha256']={str(p.relative_to(out)):digest(p) for name in stages for p in sorted((out/name).glob('*.json'))}
    dump(out/'comparison.json',result)
    lines=['# P4固定开发样本分阶段对照','','人工文件未修改。全部为已接触的五篇开发样本，不代表独立测试准确率。',
           '阶段配置、预测、代码及输出哈希见comparison.json与A—D目录。',
           'A：Jieba名词；B：增加事件；C：扩展检索与表达规则；D：增加距离衰减与泛称置信度规则。','',
           '|阶段|检测/41|候选/33|输出关联|正确|错误|单先行词正确/33|事件候选/14|事件正确/14|',
           '|---|---|---|---|---|---|---|---|---|']
    for name,r in result['stage_results'].items():
        c=r['counts'];e=r.get('single_antecedent_by_gold_type',{}).get('event',{})
        lines.append(f"|{name}|{c['detected_targets']}|{c['candidate_hits']}|{c['emitted_links']}|{c['correct_links']}|{c['emitted_links']-c['correct_links']}|{c.get('correct_single_links',c['correct_links'])}|{e.get('candidate_hits',0)}|{e.get('correct_links',0)}|")
    lines+=['','严格按指代区间和先行词区间匹配；多先行词要求完整集合一致；边界重叠不记正确。',
            '原51行、41目标、33单先行词、1多先行词均保留。未对齐自动命中保持未知，不要求补标。',
            '单先行词包含14个事件；后指、整篇概括和远距离来源受限样本仍保留在失败分母。','']
    (out/'P4改进对照.md').write_text('\n'.join(lines),encoding='utf-8')
    return result


if __name__=='__main__':
    run()
