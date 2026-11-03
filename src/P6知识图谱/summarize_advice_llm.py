"""Plan or run real entity theme summaries; does not mutate the release graph."""
import argparse
import json
import os
from pathlib import Path

from p6_quality.llm_themes import (make_package,merge_assembled,run,assemble,save,sha,split_group,
                                   MODEL,CONTEXT_LIMIT,OUTPUT_TOKENS,PROMPT,VERSION)

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'processed/P6知识图谱'


def publish(base,run_id):
    """把跑完的一轮主题结果写成发布清单，哈希现算，不手抄。"""
    run=base/'llm_advice/runs'/run_id
    completion=json.loads((run/'completion.json').read_text('utf-8'))
    usage=json.loads((run/'usage.json').read_text('utf-8'))
    results=json.loads((run/'entity_theme_summaries.json').read_text('utf-8'))
    if not completion.get('completed') or completion.get('results_sha256')!=sha(results):
        raise SystemExit('主题结果不完整或哈希不符，不能发布')
    release={'version':VERSION,'completed':True,
             'results':'runs/%s/entity_theme_summaries.json'%run_id,
             'results_sha256':completion['results_sha256'],
             'completion':'runs/%s/completion.json'%run_id,
             'usage':'runs/%s/usage.json'%run_id,
             'entities':completion['entities'],
             'themes':usage['theme_count'],
             'independent_semantic_review':False,
             'unique_entity_ids':len({r['entity_id'] for r in results})}
    save(base/'llm_advice/release.json',release)
    print(json.dumps(release,ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['plan','run','publish'])
    parser.add_argument('--run-id',default='themes-20261101-v2')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--summary-id',action='append')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    if args.command=='publish':
        return publish(BASE,args.run_id)
    if not args.run_id or Path(args.run_id).name!=args.run_id or args.run_id in {'.','..'}:
        parser.error('run-id必须是单一目录名')
    graph=json.loads((BASE/'cleaned-v1/graph.json').read_text('utf-8'))
    groups=json.loads((BASE/'cleaned-v1/condition_recommendation_summaries.json').read_text('utf-8'))
    nodes={n['id']:n for n in graph['nodes']}
    eligible=[g for g in groups if g['target_id'] and not g['scope_status']]
    # 超大实体组切成分片跑，跑完按原始 summary_id 并回去；分片号写进 checkpoint 名。
    packages=[]
    for g in eligible:
        packages.extend(p for p,_,_ in split_group(g,nodes))
    # Start with the richest contexts across object types to expose limits.
    packages.sort(key=lambda p:(-len(p['refs']),p['summary_id']))
    if args.summary_id:
        packages=[p for p in packages if p['summary_id'].split('#')[0] in args.summary_id]
        if {p['summary_id'].split('#')[0] for p in packages}!=set(args.summary_id):
            parser.error('summary-id不在可总结的实体组中')
    if args.limit is not None:
        packages=packages[:args.limit]
    output=BASE/'llm_advice/runs'/args.run_id
    plan={'run_id':args.run_id,'model':MODEL,'context_limit':CONTEXT_LIMIT,'max_output_tokens':OUTPUT_TOKENS,
          'eligible_entities':len(eligible),
          'selected_entities':len({p['summary_id'].split('#')[0] for p in packages}),
          'selected_packages':len(packages),
          'chunked_entities':sorted({p['summary_id'].split('#')[0] for p in packages if '#' in p['summary_id']}),
          'estimated_mean_prompt_tokens':round(sum(p['estimated_prompt_tokens'] for p in packages)/max(1,len(packages)),1),
          'estimated_max_prompt_tokens':max((p['estimated_prompt_tokens'] for p in packages),default=0),
          'estimated_max_prompt_plus_output_and_safety':max((p['estimated_prompt_tokens'] for p in packages),default=0)+OUTPUT_TOKENS+1024,
          'estimation_method':'offline_CJK_ASCII_estimate_not_official_tokenizer',
          'prompt_sha256':sha(PROMPT.read_text('utf-8')),'packages':[{'summary_id':p['summary_id'],
          'entity_id':p['target_id'],'scenic_code':p['scenic_code'],'items':len(p['refs']),
          'estimated_prompt_tokens':p['estimated_prompt_tokens'],'fingerprint':p['fingerprint']} for p in packages]}
    save(output/'plan.json',plan)
    print(json.dumps({k:v for k,v in plan.items() if k!='packages'},ensure_ascii=False),flush=True)
    if args.command=='plan':return
    # checkpoint 体积不小，默认放在仓库外的资源目录；换机器用 P6_THEME_CHECKPOINTS 指过去。
    checkpoints=Path(os.environ.get('P6_THEME_CHECKPOINTS')
                     or ROOT.parent.parent/'小组仓库外我自己的资源/P6_LLM主题汇总_20261101'/args.run_id/'checkpoints')
    results=run(packages,checkpoints,args.workers)
    # 分片跑完要按原始实体组合并，否则同一实体在结果里会出现两条。
    by_group={}
    for pkg,state in results:
        by_group.setdefault(pkg['summary_id'].split('#')[0],[]).append((pkg,assemble(pkg,state['result'])))
    assembled=[]
    for sid,pairs in sorted(by_group.items()):
        pairs.sort(key=lambda x:x[0]['summary_id'])
        assembled.append(pairs[0][1] if len(pairs)==1 else merge_assembled(sid,pairs))
    attempts=[a for _,s in results for a in s['attempts']]
    usage={'requests':len(attempts),'entities':len(assembled),
           'chunked_groups':sum(1 for v in by_group.values() if len(v)>1),
           'theme_count':sum(len(x['topics']) for x in assembled),
           'prompt_tokens':sum(a['usage'].get('prompt_tokens',0) for a in attempts),
           'completion_tokens':sum(a['usage'].get('completion_tokens',0) for a in attempts),
           'max_actual_total_tokens':max((a['usage'].get('total_tokens',0) for a in attempts),default=0),
           'max_actual_prompt_tokens':max((a['usage'].get('prompt_tokens',0) for a in attempts),default=0),
           'all_outputs_json_and_refs_validated':True,'semantic_correctness_independently_verified':False,
           'checkpoints':str(checkpoints)}
    usage['mean_actual_prompt_tokens']=round(usage['prompt_tokens']/max(1,len(attempts)),1)
    save(output/'entity_theme_summaries.json',assembled)
    save(output/'usage.json',usage)
    if len(assembled)==len(eligible):
        save(output/'completion.json',{'version':VERSION,'completed':True,'entities':len(eligible),
             'results_sha256':sha(assembled),
             'input_fingerprints':{sid:([f[0]['fingerprint'] for f in v] if len(v)>1 else v[0][0]['fingerprint'])
                                   for sid,v in by_group.items()}})
    print(json.dumps(usage,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
