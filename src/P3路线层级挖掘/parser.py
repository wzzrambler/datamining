"""Generic span/step parsing. Span coordinates are computed by discovery at run time."""
import re
from collections import Counter

ARROW = re.compile(r'→|➡\ufe0f?|➜|➝|——|(?<=[\u4e00-\u9fffA-Za-z])-(?=[\u4e00-\u9fffA-Za-z0-9])')
CLOCK = re.compile(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)')
NUMBERED = re.compile(r'^\s*(第([一二三四五六七八九十百\d]+)站|([1-9]\d*)[\ufe0f\u20e3]+|([1-9]\d*)[.、]|([①②③④⑤⑥⑦⑧⑨⑩]))')
PERIOD = r'清晨|早上|上午|中午|午后|下午|傍晚|晚上|夜间|全天'
PERIOD_HEADING = re.compile(r'^\s*[【\[]?('+PERIOD+r')[】\]]?(?:\s*[—:：|｜]+\s*(.*)|\s*$)')
DAY = re.compile(r'^\s*[【\[]?\s*(?:Day\s*0?(\d+)|第([一二三四五六七八九十\d]+)[天日])',re.I)
CN = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}


def number(text):
    if text.isdigit():return int(text)
    if text in CN:return CN[text]
    if text.startswith('十'):return 10+CN.get(text[1:],0)
    if '十' in text:
        a,b=text.split('十',1);return CN.get(a,0)*10+CN.get(b,0)
    return None


def evidence(doc,start,end,sources):
    if not 0<=start<end<=len(doc['content']):raise ValueError('Invalid computed span')
    paras=[p['i'] for p in doc['paragraphs'] if p['start']<end and start<p['end']]
    origins=[sources.get((doc['doc_id'],p),{'kind':'legacy','para_id':p}) for p in paras]
    return dict(doc_id=doc['doc_id'],start=start,end=end,text=doc['content'][start:end],para_ids=paras,origins=origins)


def inside(mentions,ev,kind=None):
    return [m for m in mentions if ev['start']<=m['start'] and m['end']<=ev['end'] and (kind is None or m['type']==kind)]


def trim_span(doc,start,end,sources):
    while start<end and doc['content'][start].isspace():start+=1
    while end>start and doc['content'][end-1].isspace():end-=1
    return evidence(doc,start,end,sources) if start<end else None


def clean_label(text):
    text=NUMBERED.sub('',text).strip()
    text=re.sub(r'^[（(]?\d{1,2}:\d{2}(?:\s*[-—至]\s*\d{1,2}:\d{2})?[）)]?\s*[：:]?','',text)
    text=re.sub(r'^(?:'+PERIOD+r')','',text)
    text=re.sub(r'^(?:从|再|先|坐|乘坐|乘车|步行|前往|直奔|到达|进入|去|到)+','',text)
    text=re.split(r'[（(,，。;；]',text)[0].strip(' :：·-')
    text=re.sub(r'(?:入园|进园|出园|进入|出来|换乘|中转|乘车|游览|游玩|探秘)$','',text)
    return text


def split_chain(ev,doc,sources):
    """Only split top-level arrows; clock hyphens and bracket notes stay intact."""
    text=ev['text'];seps=[];depth=0
    matches={m.start():m for m in ARROW.finditer(text)}
    for i,ch in enumerate(text):
        if ch in '(（【[':depth+=1
        elif ch in ')）】]':depth=max(0,depth-1)
        elif depth==0 and i in matches:seps.append(matches[i])
    spans=[];pos=0
    for sep in seps+[None]:
        end=sep.start() if sep else len(text)
        frag=trim_span(doc,ev['start']+pos,ev['start']+end,sources)
        if frag:spans.append(frag)
        if sep:pos=sep.end()
    return spans


def period_for_clock(h,m=0):
    v=h*60+m
    if v<360:return '夜间'
    if v<720:return '上午'
    if v<810:return '中午'
    if v<1080:return '下午'
    return '晚上'


def time_info(core,context):
    matches=list(CLOCK.finditer(core['text']))
    clocks=[int(m[1])*60+int(m[2]) for m in matches]
    # A Chinese clock is recognized only as a local time, not an opening-hours inference.
    chinese=re.match(r'^\s*(\d{1,2})点(?:(\d{1,2})分?|半|多)?',NUMBERED.sub('',core['text'])) if not clocks else None
    local=re.match(r'^\s*(?:'+PERIOD+r')',core['text'])
    inherited=context.get('period_evidence')
    if inherited:
        p,basis,ev=context['period'],'explicit_heading',inherited
    elif local:
        p,basis,ev=local[0].strip(),'explicit_local',core
    elif clocks:
        p,basis,ev=period_for_clock(clocks[0]//60,clocks[0]%60),'clock_mapping',core
    elif chinese and int(chinese[1])<24:
        p,basis,ev=period_for_clock(int(chinese[1])),'clock_mapping',core
    else:p,basis,ev='未说明','unknown',None
    p={'早上':'上午','午后':'下午'}.get(p,p)
    return dict(period=p,basis=basis,evidence=ev,clock_texts=[m[0] for m in matches],
        start_minute=clocks[0] if clocks else None,
        end_minute=clocks[1] if len(clocks)>=2 and re.search(r'\d:\d{2}\s*[-—~至]\s*\d',core['text']) else None,
        day=context.get('day'),day_evidence=context.get('day_evidence'))


def duration_records(doc,ev,mentions,sources):
    out=[]
    for m in inside(mentions,ev,'TIME'):
        if m['subtype']!='duration':continue
        pos=m['start']-ev['start'];before=ev['text'][max(0,pos-12):pos];after=ev['text'][pos+len(m['text']):pos+len(m['text'])+8]
        if re.search(r'每|班次|一场|/次',before+after):role='frequency'
        elif re.search(r'排队|等待|提前',before):role='waiting_or_lead'
        elif re.search(r'用餐|休息',before):role='meal_rest'
        elif re.search(r'游览时间|停留|逗留',before):role='visit'
        elif re.search(r'步行|乘坐|车程|路程',before) or '车程' in after:role='travel'
        else:role='unresolved'
        out.append(dict(mention_id=m['mention_id'],text=m['text'],role=role,scope='whole_step',evidence=evidence(doc,m['start'],m['end'],sources)))
    return out


def make_step(ev,mode,doc,mentions,sources,context,aliases,primary_start=None):
    refs=[{k:m.get(k,'') for k in ('mention_id','entity_id','text','canonical_name','poi_role','scope','subtype','start','end')} for m in inside(mentions,ev,'POI')]
    route_refs=refs
    if mode=='segment':
        border=re.search(r'[（(]',ev['text'])
        if border:route_refs=[m for m in refs if m['end']<=ev['start']+border.start()]
    if primary_start is not None:route_refs=[m for m in route_refs if m['start']>=primary_start]
    excluded=[]
    for m in route_refs:
        local=m['start']-ev['start'];end=m['end']-ev['start']
        if re.search(r'没去|不去|不肯去|放弃了?|不看',ev['text'][max(0,local-6):local]) or re.match(r'没必要去|不去|没去|不建议去',ev['text'][end:]):excluded.append(m['mention_id'])
    route_refs=[m for m in route_refs if m['mention_id'] not in excluded]
    links=[]
    # Generic, unique suffix matching only for an entire named node; no per-document alias list.
    label=clean_label(ev['text'])
    if not route_refs and len(label)>=4 and label in aliases:
        target=aliases[label]
        pos=ev['text'].find(label)
        links=[dict(text=label,canonical_name=target['canonical_name'],entity_id=target['entity_id'],
                    target_p2_mention_id=target['mention_id'],start=ev['start']+pos,end=ev['start']+pos+len(label),
                    method='unique_canonical_suffix',poi_role='attraction',scope='in_area')]
    actions=[dict(text=m[0],start=ev['start']+m.start(),end=ev['start']+m.end(),method='literal_action') for m in re.finditer(r'入园|出园|出沟|乘坐|换乘|步行|吃饭|用餐|午餐|休整|休息|观看|游览|参观|投喂|拍照|购物|表演|巡游',ev['text'])]
    t=time_info(ev,context)
    unresolved=[]
    if not route_refs and not links:unresolved.append('no_p2_poi_in_step')
    if not actions:unresolved.append('activity_unspecified')
    if t['period']=='未说明':unresolved.append('time_unspecified')
    order_text=re.split(r'[（(]',ev['text'])[0] if mode=='segment' else ev['text']
    if re.search(r'或者|二选一|也可|(?<!\d)/|\+',order_text):unresolved.append('alternative_or_group_requires_review')
    st=dict(parser=mode,evidence=ev,context_evidence=[],poi_refs=refs,route_poi_refs=route_refs,p3_normalized_links=links,
            excluded_unrealized_poi_mentions=excluded,
            p2_mention_ids=[m['mention_id'] for m in inside(mentions,ev)],
            transport_refs=[{k:m[k] for k in ('mention_id','text','canonical_name')} for m in inside(mentions,ev,'TRANSPORT')],
            context_transport_mentions=[],time=t,durations=duration_records(doc,ev,mentions,sources),activity=actions,
            unresolved=unresolved,order_group=context.get('order_group'),source_step_label=None)
    numbered=NUMBERED.match(ev['text'])
    if numbered:st['source_step_label']=numbered[0].strip()
    if mode=='segment':
        path=re.split(r'[（(]',ev['text'])[0]
        labels=[clean_label(v) for v in ARROW.split(path)]
        st.update(path_labels=labels,movement=dict(from_label=labels[0],to_label=labels[-1],basis='explicit_arrow',duration_scope='whole_segment'))
    return st


def finalize_route(route):
    steps=route['steps'];groups=[];hierarchy=[]
    for i,s in enumerate(steps,1):
        s.update(index=i,step_id=f"{route['route_id']}-S{i:03d}")
        key=s['order_group'] or s['step_id']
        if not groups or groups[-1][0]!=key:groups.append((key,[]))
        groups[-1][1].append(s)
        if not hierarchy or hierarchy[-1]['day']!=s['time']['day']:hierarchy.append(dict(day=s['time']['day'],period_groups=[]))
        buckets=hierarchy[-1]['period_groups']
        if not buckets or buckets[-1]['period']!=s['time']['period']:buckets.append(dict(period=s['time']['period'],step_ids=[]))
        buckets[-1]['step_ids'].append(s['step_id'])
    route['order_relations']=[dict(before=a['step_id'],after=b['step_id'],basis='automatic_format_or_connective',evidence_steps=[a['step_id'],b['step_id']]) for (_,left),(_,right) in zip(groups,groups[1:]) for a in left for b in right]
    route['hierarchy']=hierarchy
    return route


def poi_key(ref):return ref['canonical_name'] or '未归一:'+ref['text']


def visit_sequence(route):
    seq=[]
    for s in route['steps']:
        for m in s['route_poi_refs']+s['p3_normalized_links']:
            if m['poi_role']!='attraction' or m['scope']=='outside':continue
            name=poi_key(m)
            if not seq or seq[-1]!=name:seq.append(name)
    return seq


def lcs(a,b):
    row=[0]*(len(b)+1)
    for x in a:
        nxt=[0]
        for j,y in enumerate(b):nxt.append(row[j]+1 if x==y else max(nxt[-1],row[j+1]))
        row=nxt
    return row[-1]


def compare(a,b):
    if (a['scenic_code'],a['scope'],a['granularity'])!=(b['scenic_code'],b['scope'],b['granularity']):raise ValueError('范围或粒度不一致')
    sa,sb=visit_sequence(a),visit_sequence(b);A,B=set(sa),set(sb);common=A&B
    ca,cb=[x for x in sa if x in common],[x for x in sb if x in common]
    ok=len(common)>=2 and not any(s['order_group'] or 'alternative_or_group_requires_review' in s['unresolved'] for r in (a,b) for s in r['steps'])
    return dict(official_route_id=a['route_id'],visitor_route_id=b['route_id'],visitor_nature=b['nature'],
        official_sequence=sa,visitor_sequence=sb,shared=sorted(common),official_only=sorted(A-B),visitor_only=sorted(B-A),
        official_coverage=len(common)/len(A) if A else None,jaccard=len(common)/len(A|B) if A|B else None,
        common_sequence_lcs=lcs(ca,cb) if ok else None,common_order_similarity=lcs(ca,cb)/max(len(ca),len(cb)) if ok else None,
        order_status='applicable' if ok else 'not_applicable',
        time_distribution={r['route_id']:dict(Counter(s['time']['period'] for s in r['steps'])) for r in (a,b)},
        limit='自动配对的文本路线，不保证游客实际到访；时段步骤数不代表停留时长。')
