"""Real non-streaming DeepSeek theme summaries, validated and resumable."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading

ROOT = Path(__file__).resolve().parents[3]
PROMPT = Path(__file__).resolve().parents[1] / 'assets/advice_theme_system.md'
MODEL = 'deepseek-flash'
CONTEXT_LIMIT = 30000
OUTPUT_TOKENS = 8192
VERSION = 'llm_advice_themes_v2'


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(compact(value).encode('utf-8')).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', 'utf-8')
    os.replace(tmp, path)


def estimate(text):
    runs = re.findall(r'[A-Za-z0-9_]+|[^A-Za-z0-9_\s]', text)
    return math.ceil(1.25 * sum(math.ceil(len(x)/3) if x.isascii() and re.fullmatch(r'\w+',x) else 1 for x in runs))


def make_package(group, nodes):
    target = nodes[group['target_id']]
    def name(nid):
        n = nodes.get(nid, {})
        p = n.get('properties', {})
        if n.get('label') == 'TRANSPORT_LEG':
            return name(p['from_entity_id'])+' → '+name(p['to_entity_id'])+'（'+(name(p['transport_entity_id']) if p.get('transport_entity_id') else '方式未说明')+'）'
        return p.get('name') or p.get('scope') or nid
    items = []
    refs = {}
    for i,item in enumerate(group['items'],1):
        ref = f'E{i:03d}'
        refs[ref] = item
        items.append({'ref': ref, 'condition': item['condition'], 'content': item['text'],
                      'force': item['force'], 'original_kind': item['original_statement_kind'],
                      'joint_targets': [{'id':t['id'],'name':name(t['id'])} for t in item['joint_targets']],
                      'scope': item['scope'],
                      'sources': sorted({s['doc_id']+'（'+s['source_kind']+'，'+str(s.get('publish_date') or '日期未说明')+'）' for s in item['sources']})})
    payload = {'scenic_code':group['scenic_code'],'entity_id':group['target_id'],
               'entity_name':name(target['id']),'entity_type':target['label'],'items':items}
    system = PROMPT.read_text('utf-8')
    messages = [{'role':'system','content':system},{'role':'user','content':compact(payload)}]
    tokens = sum(estimate(m['content'])+16 for m in messages)+64
    if tokens+OUTPUT_TOKENS+1024>CONTEXT_LIMIT:
        raise ValueError('实体输入超过30K预算，不截断：'+group['target_id'])
    return {'summary_id':group['advice_summary_id'], 'target_id':group['target_id'],
            'scenic_code':group['scenic_code'],'messages':messages,'refs':refs,
            'estimated_prompt_tokens':tokens,'fingerprint':sha({'version':VERSION,'messages':messages,
                'model':MODEL,'max_tokens':OUTPUT_TOKENS,'temperature':0.1})}


def validate(result, refs, require_coverage=True):
    if not isinstance(result,dict) or set(result)!={'topics'} or not isinstance(result['topics'],list) or not result['topics']:
        raise ValueError('仅返回非空topics数组')
    used, labels = set(), set()
    for t in result['topics']:
        if not isinstance(t,dict) or set(t)!={'label','summary','item_refs'}:
            raise ValueError('主题字段必须为label、summary、item_refs')
        if not isinstance(t['label'],str) or not 1<=len(t['label'].strip())<=40 or not isinstance(t['summary'],str) or not t['summary'].strip():
            raise ValueError('主题标签或自然语言内容为空/过长')
        if t['label'].strip() in labels:
            raise ValueError('重复主题标签请归并：'+t['label'])
        labels.add(t['label'].strip())
        if not t['summary'].strip().startswith('历史资料记载，'):
            raise ValueError('每个主题summary必须以“历史资料记载，”开头，避免把历史说法写成当前结论')
        if not isinstance(t['item_refs'],list) or not t['item_refs'] or any(not isinstance(x,str) or x not in refs for x in t['item_refs']):
            raise ValueError('主题引用了未提供的ref或引用为空')
        if len(set(t['item_refs']))!=len(t['item_refs']):
            raise ValueError('同一主题不要重复ref')
        used.update(t['item_refs'])
    missing=set(refs)-used
    if missing and require_coverage:
        raise ValueError('缺少这些条目的总结和引用：'+','.join(sorted(missing)))
    return result


def merge_missing_topics(package, previous, missing_raw):
    """Merge a validated partial repair into the prior output without losing coverage."""
    missing=validate(missing_raw,package['refs'],require_coverage=False)
    used={ref for topic in previous['topics'] for ref in topic['item_refs']}
    additions=[]
    for topic in missing['topics']:
        refs=[ref for ref in topic['item_refs'] if ref not in used]
        if not refs:
            continue
        item=dict(topic); item['item_refs']=refs
        additions.append(item); used.update(refs)
    combined={'topics':[dict(t) for t in previous['topics']]}
    labels={t['label'] for t in combined['topics']}
    for topic in additions:
        if topic['label'] in labels:
            existing=next(t for t in combined['topics'] if t['label']==topic['label'])
            existing['summary']=existing['summary'].rstrip('。；;')+'；'+topic['summary'].removeprefix('历史资料记载，')
            existing['item_refs']=list(dict.fromkeys(existing['item_refs']+topic['item_refs']))
        else:
            combined['topics'].append(topic); labels.add(topic['label'])
    return validate(combined,package['refs'])


def assemble(package, raw):
    validate(raw,package['refs'])
    topics=[]
    for topic in raw['topics']:
        items=[package['refs'][ref] for ref in topic['item_refs']]
        topics.append({'topic_id':'THEME-'+sha([package['summary_id'],topic])[:20],
                       'label':topic['label'].strip(),'summary':topic['summary'].strip(),
                       'source_item_ids':sorted({i['item_id'] for i in items}),
                       'source_record_ids':sorted({r for i in items for r in i['source_record_ids']}),
                       'scope_bindings':sorted({compact(i['scope']) for i in items}),
                       'joint_target_sets':[json.loads(x) for x in sorted({compact(i['joint_targets']) for i in items})],
                       'current_validity_verified':False})
    return {'advice_summary_id':package['summary_id'],'entity_id':package['target_id'],
            'scenic_code':package['scenic_code'],'input_fingerprint':package['fingerprint'],
            'method':VERSION,'model':MODEL,'topics':topics,'item_reference_coverage':1.0,
            'semantic_correctness_independently_verified':False}


def split_group(group, nodes):
    """把一个实体组切成能在预算内跑完的分片，返回 [(package, 序号, 总数)]。

    为什么不是直接放宽 30K：单条提示词一旦逼近窗口上限，输出被截断的风险和重试成本
    都会上去，而且截断的记录会被当成"模型没总结完"。分片做两件事——
    条目按 item_id 排序后二分，保证同样输入永远切在同一处；每片单独校验覆盖率，
    两片的并集再和原组比对，缺一条都不算完成。
    """
    items = sorted(group['items'], key=lambda i: i['item_id'])

    def probe(sub):
        trial = dict(group, items=sub, advice_summary_id=group['advice_summary_id'] + '#probe')
        try:
            make_package(trial, nodes)
            return True
        except ValueError:
            return False

    if probe(items):
        return [(make_package(group, nodes), 0, 1)]
    parts = []
    stack = [items]
    while stack:
        sub = stack.pop(0)
        if probe(sub) or len(sub) == 1:
            if not probe(sub):
                raise ValueError('单条建议就超出预算，无法分片：' + group['target_id'])
            parts.append(sub)
            continue
        mid = len(sub) // 2
        stack = [sub[:mid], sub[mid:]] + stack
    packages = []
    for k, sub in enumerate(parts):
        shard = dict(group, items=sub,
                     advice_summary_id='%s#c%d' % (group['advice_summary_id'], k + 1))
        packages.append((make_package(shard, nodes), k + 1, len(parts)))
    return packages


def merge_assembled(summary_id, pairs):
    """把同一实体组的分片主题并成一条结果，同标签的合并引用而不是留两个同名主题。"""
    topics, index = [], {}
    for package, assembled in pairs:
        for topic in assembled['topics']:
            key = topic['label']
            if key not in index:
                item = dict(topic)
                item['source_item_ids'] = list(topic['source_item_ids'])
                item['source_record_ids'] = list(topic['source_record_ids'])
                index[key] = item
                topics.append(item)
                continue
            cur = index[key]
            cur['source_item_ids'] = sorted(set(cur['source_item_ids']) | set(topic['source_item_ids']))
            cur['source_record_ids'] = sorted(set(cur['source_record_ids']) | set(topic['source_record_ids']))
            cur['scope_bindings'] = sorted(set(cur.get('scope_bindings', [])) | set(topic.get('scope_bindings', [])))
            cur['joint_target_sets'] = [json.loads(x) for x in sorted(
                {compact(x) for x in cur.get('joint_target_sets', []) + topic.get('joint_target_sets', [])})]
            cur['summary'] = cur['summary'].rstrip('。；;') + '；' + topic['summary'].removeprefix('历史资料记载，')
    first = pairs[0][1]
    return {'advice_summary_id': summary_id, 'entity_id': first['entity_id'],
            'scenic_code': first['scenic_code'], 'input_fingerprint': None,
            'chunk_fingerprints': [p['fingerprint'] for p, _ in pairs],
            'chunk_count': len(pairs), 'method': VERSION, 'model': MODEL, 'topics': topics,
            'item_reference_coverage': 1.0, 'semantic_correctness_independently_verified': False}


def attach_results(graph, groups, results):
    """Bind every generated theme back to the registered, scene-scoped owner."""
    nodes={n['id']:n for n in graph['nodes']}
    eligible={g['advice_summary_id']:g for g in groups if g['target_id'] and not g['scope_status']}
    indexed={r['advice_summary_id']:r for r in results}
    if len(indexed)!=len(results) or set(indexed)!=set(eligible):
        raise ValueError('LLM结果必须完整覆盖当前可总结实体，不能用小样本替代全量')
    chunked=[]
    for sid,group in eligible.items():
        result=indexed[sid]
        if result['entity_id']!=group['target_id'] or result['scenic_code']!=group['scenic_code']:
            raise ValueError('LLM实体或景区不一致：'+sid)
        shards=split_group(group,nodes)
        if len(shards)==1:
            if result.get('input_fingerprint')!=shards[0][0]['fingerprint']:
                raise ValueError('LLM输入指纹不一致：'+sid)
        else:
            if result.get('chunk_fingerprints')!=[p['fingerprint'] for p,_,_ in shards]:
                raise ValueError('LLM分片输入指纹不一致：'+sid)
            chunked.append({'advice_summary_id':sid,'chunks':len(shards),
                            'items':len(group['items'])})
        items={i['item_id']:i for i in group['items']}
        covered=set()
        for t in result['topics']:
            item_ids=t['source_item_ids']
            if not item_ids or not set(item_ids)<=set(items):
                raise ValueError('LLM主题原始条目引用无效')
            expected={r for iid in item_ids for r in items[iid]['source_record_ids']}
            if expected!=set(t['source_record_ids']):
                raise ValueError('LLM主题原始记录引用不一致')
            covered.update(item_ids)
            owner=nodes[group['target_id']]['properties']
            owner.setdefault('advice_themes',[]).append({'topic_id':t['topic_id'],'label':t['label'],
                'summary':t['summary'],'scenic_code':group['scenic_code'],'advice_summary_id':sid,
                'source_record_ids':t['source_record_ids'],'current_validity_verified':False})
        if covered!=set(items):
            raise ValueError('LLM结果存在未覆盖的原始条目')
        group['llm_theme_result']=result
        group['method']='lossless_aggregation_plus_llm_themes_v2'
        group['semantic_merge_completed']=True
    return {'entity_scene_groups':len(eligible),'unique_entity_ids':len({g['target_id'] for g in eligible.values()}),
            'topics':sum(len(r['topics']) for r in results),
            'all_entity_ids_and_sources_validated':True,'method':VERSION,
            'chunked_groups':chunked,
            'semantic_correctness_independently_verified':False}


def run_one(package, checkpoint):
    if checkpoint.exists():
        state=json.loads(checkpoint.read_text('utf-8'))
        if state['fingerprint']!=package['fingerprint']:
            raise ValueError('输入或提示词变化，必须使用新的run-id：'+package['summary_id'])
        if state.get('status')=='completed':
            assemble(package,state['result'])
            return state
    else:
        state={'fingerprint':package['fingerprint'],'summary_id':package['summary_id'],'status':'running',
               'messages':package['messages'],'estimated_prompt_tokens':package['estimated_prompt_tokens'],'attempts':[]}
    from openai import OpenAI
    key=os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise ValueError('请配置DEEPSEEK_API_KEY，不要把Key写入文件')
    client=OpenAI(api_key=key,base_url=os.environ.get('DEEPSEEK_BASE_URL','https://api.deepseek.com'),timeout=120,max_retries=2)
    error=''
    prior=None
    if state.get('attempts'):
        # Reuse the latest structurally valid model output after an interrupted or
        # failed coverage check, then ask only for source items it omitted.
        for old in reversed(state['attempts']):
            try:
                candidate=json.loads(old.get('content') or '')
                validate(candidate,package['refs'],require_coverage=False)
                prior=candidate
                break
            except Exception:
                pass
    for attempt in range(3):
        if prior is not None:
            used={ref for t in prior['topics'] for ref in t['item_refs']}
            missing=sorted(set(package['refs'])-used)
            if not missing:
                result=validate(prior,package['refs'])
                state.update(status='completed',result=result); save(checkpoint,state); return state
            original=json.loads(package['messages'][1]['content'])
            repair={'scenic_code':original['scenic_code'],'entity_id':original['entity_id'],
                    'entity_name':original['entity_name'],'entity_type':original['entity_type'],
                    'items':[i for i in original['items'] if i['ref'] in missing]}
            repair_text='只补充下面遗漏条目的主题总结，沿用原JSON格式；item_refs只能引用本次给出的条目。已有主题标签如下，若适合可复用：'+compact([t['label'] for t in prior['topics']])+'\n遗漏条目：'+compact(repair)
            messages=package['messages'][:1]+[{'role':'user','content':repair_text}]
        else:
            messages=package['messages']+([{'role':'user','content':'上次输出校验失败，请修正全部主题。错误：'+error}] if error else [])
        if sum(estimate(m['content'])+16 for m in messages)+OUTPUT_TOKENS+1024>CONTEXT_LIMIT:
            raise ValueError('修正请求超过30K预算')
        try:
            reply=client.chat.completions.create(model=MODEL,messages=messages,stream=False,temperature=0.1,
                max_tokens=OUTPUT_TOKENS,response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}})
            record={'response_id':reply.id,'model':reply.model,'finish_reason':reply.choices[0].finish_reason,
                    'usage':reply.usage.model_dump(exclude_none=True) if reply.usage else {},
                    'content':reply.choices[0].message.content}
            state['attempts'].append(record)
            save(checkpoint,state)
            if record['finish_reason']!='stop':
                raise ValueError('模型输出不完整：'+str(record['finish_reason']))
            parsed=json.loads(record['content'])
            if prior is not None:
                result=merge_missing_topics(package,prior,parsed)
            else:
                try:
                    result=validate(parsed,package['refs'])
                except ValueError as exc:
                    if not str(exc).startswith('缺少这些条目的总结和引用：'):
                        raise
                    prior=validate(parsed,package['refs'],require_coverage=False)
                    error=str(exc)
                    continue
            state.update(status='completed',result=result)
            save(checkpoint,state)
            return state
        except Exception as exc:
            # Do not persist SDK exception strings, which can contain endpoint
            # credentials. Only validation errors generated here are echoed.
            error=str(exc) if isinstance(exc,(ValueError,json.JSONDecodeError)) else type(exc).__name__
            state.setdefault('errors',[]).append(error)
            save(checkpoint,state)
    state['status']='failed'
    save(checkpoint,state)
    raise ValueError(package['summary_id']+'三次尝试失败：'+error)


def run(packages,checkpoint_dir,workers=4):
    results=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks={pool.submit(run_one,p,checkpoint_dir/(p['summary_id']+'.json')):p for p in packages}
        for f in as_completed(tasks):
            p=tasks[f]
            state=f.result()
            results.append((p,state))
            print(compact({'completed':len(results),'total':len(packages),'entity_id':p['target_id'],
                           'themes':len(state['result']['topics'])}),flush=True)
    return results
