"""Plan or run real entity theme summaries; does not mutate the release graph."""
import argparse
import json
from pathlib import Path

from p6_quality.llm_themes import make_package,run,assemble,save,sha,MODEL,CONTEXT_LIMIT,OUTPUT_TOKENS,PROMPT,VERSION

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'processed/P6知识图谱'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['plan','run'])
    parser.add_argument('--run-id',default='themes-20261101-v2')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--summary-id',action='append')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    if not args.run_id or Path(args.run_id).name!=args.run_id or args.run_id in {'.','..'}:
        parser.error('run-id必须是单一目录名')
    graph=json.loads((BASE/'cleaned-v1/graph.json').read_text('utf-8'))
    groups=json.loads((BASE/'cleaned-v1/condition_recommendation_summaries.json').read_text('utf-8'))
    nodes={n['id']:n for n in graph['nodes']}
    eligible=[g for g in groups if g['target_id'] and not g['scope_status']]
    packages=[make_package(g,nodes) for g in eligible]
    # Start with the richest contexts across object types to expose limits.
    packages.sort(key=lambda p:(-len(p['refs']),p['summary_id']))
    if args.summary_id:
        packages=[p for p in packages if p['summary_id'] in args.summary_id]
        if len(packages)!=len(set(args.summary_id)):
            parser.error('summary-id不在可总结的实体组中')
    if args.limit is not None:
        packages=packages[:args.limit]
    output=BASE/'llm_advice/runs'/args.run_id
    plan={'run_id':args.run_id,'model':MODEL,'context_limit':CONTEXT_LIMIT,'max_output_tokens':OUTPUT_TOKENS,
          'eligible_entities':len(eligible),'selected_entities':len(packages),
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
    checkpoints=ROOT.parent.parent/'小组仓库外我自己的资源/P6_LLM主题汇总_20261101'/args.run_id/'checkpoints'
    results=run(packages,checkpoints,args.workers)
    assembled=sorted([assemble(p,s['result']) for p,s in results],key=lambda x:x['advice_summary_id'])
    attempts=[a for _,s in results for a in s['attempts']]
    usage={'requests':len(attempts),'entities':len(results),'theme_count':sum(len(x['topics']) for x in assembled),
           'prompt_tokens':sum(a['usage'].get('prompt_tokens',0) for a in attempts),
           'completion_tokens':sum(a['usage'].get('completion_tokens',0) for a in attempts),
           'max_actual_total_tokens':max((a['usage'].get('total_tokens',0) for a in attempts),default=0),
           'max_actual_prompt_tokens':max((a['usage'].get('prompt_tokens',0) for a in attempts),default=0),
           'all_outputs_json_and_refs_validated':True,'semantic_correctness_independently_verified':False,
           'checkpoints':str(checkpoints)}
    usage['mean_actual_prompt_tokens']=round(usage['prompt_tokens']/max(1,len(attempts)),1)
    save(output/'entity_theme_summaries.json',assembled)
    save(output/'usage.json',usage)
    if len(packages)==len(eligible):
        save(output/'completion.json',{'version':VERSION,'completed':True,'entities':len(eligible),
             'results_sha256':sha(assembled),'input_fingerprints':{p['summary_id']:p['fingerprint'] for p in packages}})
    print(json.dumps(usage,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
