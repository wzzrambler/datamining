"""Discover route boundaries from every paragraph. No document/paragraph allowlists."""
import hashlib
import re
from collections import Counter, defaultdict

from parser import (ARROW,CLOCK,DAY,NUMBERED,PERIOD,PERIOD_HEADING,clean_label,compare,
                    evidence,finalize_route,inside,lcs,make_step,number,split_chain,trim_span,visit_sequence)

ROUTE_TITLE=re.compile(r'路线|线路|时间线|顺序|行程|动线|[一二三四五六七\d]+日.*游|第.*站')
HARD_STOP=re.compile(r'^(?:[一二三四五六七八九十\d]+[、.、]\s*)?(?:关于|实用|温馨)?(?:交通(?:指南|方式)?|住宿|酒店选择|门票|票价|预约|注意事项|准备|必带|禁带|出行清单|饮食|美食|Tips|小\s*tips|避坑提醒|纪念品|省钱)',re.I)
TIP_HEADING=re.compile(r'^(?:重点提醒|带娃.{0,3}(?:重点|好物)|隐藏玩法|食物携带|拍照.{0,5}技巧|.{0,8}划重点|.{0,5}时间表)$')
TOUR_TITLE=re.compile(r'^[一二三四五六七\d]+(?:日|天).{0,10}游$')
ACTUAL=re.compile(r'实际路线|实际行程|我们的路线|我们的实际|总结.{0,4}我们|我们.{0,8}(?:选择了|走了|去了|是\d|这次)|这次选择的路线|亲测')
ADVICE=re.compile(r'参考|推荐|最优|最佳|建议|照着走|攻略')
NARRATIVE=re.compile(r'第一站[,，]?(?:便是|就是|是)|先去|首先.{0,5}(?:去|到|走进)|接着.{0,5}(?:去|到|换乘)|随后.{0,5}(?:去|到)|继续前行(?:遇见|到达)?|顺着.{0,8}(?:抵达|到达)|从.{1,16}出来.{0,12}(?:去|走进)|然后(?:去|到)|再坐.{0,8}前往|离开.{0,8}来到')


def origin_key(doc,p,sources):
    src=sources.get((doc['doc_id'],p['i']),{'kind':'legacy'})
    # A whole image/comment has its own state; no body->OCR or image->image inheritance.
    return tuple(sorted((k,str(v)) for k,v in src.items() if k not in ('doc_id','para_id')))


def canonical_aliases(entity_docs):
    """Build unique suffix candidates only from existing, nonempty P2 canonical names."""
    pools=defaultdict(lambda:defaultdict(dict))
    for d in entity_docs.values():
        for m in d['mentions']:
            name=m.get('canonical_name','')
            if m['type']!='POI' or m['poi_role']!='attraction' or not name:continue
            for i in range(max(0,len(name)-3)):
                suffix=name[i:]
                if len(suffix)>=4:pools[d['scenic_code']][suffix].setdefault(name,m)
    return {code:{suffix:next(iter(targets.values())) for suffix,targets in pool.items() if len(targets)==1}
            for code,pool in pools.items()}


def route_nature(doc,cue):
    if doc['text_type']=='official_route':return 'official_recommendation'
    # Reference/conditional language overrides a nearby claim of having visited.
    if re.search(r'参考|推荐路线|最优|最佳|建议路线|如果',cue):return 'visitor_recommendation'
    if ACTUAL.search(cue):return 'visitor_actual_claim'
    if ADVICE.search(cue) or ADVICE.search(doc.get('title','')):return 'visitor_recommendation'
    return 'visitor_unspecified'


def infer_scope(doc,steps,mentions,aliases):
    refs=[m for s in steps for m in s['route_poi_refs']+s['p3_normalized_links'] if m['scope']!='outside']
    parents={m['canonical_name'] for m in aliases.values() if m['type']=='POI' and m['subtype']=='scenic'
             and m['scope']!='outside' and re.search(r'世界|乐园|景区|公园|王国',m['canonical_name'])}
    node_parents={m['canonical_name'] for m in refs if m['canonical_name'] in parents}
    children=[m for m in refs if m['poi_role']=='attraction' and m['canonical_name'] not in parents]
    if len(node_parents)>=2 and len(node_parents)>=len(children):return doc['scenic_code'],'park_level'
    if len(node_parents)>=2:
        return doc['scenic_code'], 'mixed' if children else 'park_level'
    if len(node_parents)==1 and not children:return doc['scenic_code'],'park_level'
    in_title=[p for p in parents if p in doc.get('title','') or (p[-4:] in doc.get('title','') and aliases.get(p[-4:],{}).get('canonical_name')==p)]
    if len(in_title)==1:return in_title[0],'in_park'
    title_matches={p for p in parents if any(p[i:i+4] in doc.get('title','') for i in range(len(p)-3))}
    if len(title_matches)==1:return next(iter(title_matches)),'in_park'
    gate_parents={p for p in parents for m in refs if m.get('subtype')=='gate' and p in m['canonical_name']}
    if len(gate_parents)==1:return next(iter(gate_parents)),'in_park'
    mentioned_parents={p for p in parents if p in doc['content'] or (p[-4:] in doc['content'] and aliases.get(p[-4:],{}).get('canonical_name')==p)}
    if len(mentioned_parents)>1:return doc['scenic_code']+':scope_uncertain','unknown'
    counts=Counter(m['canonical_name'] for m in mentions if m['type']=='POI' and m['canonical_name'] in parents)
    if counts:
        ranked=counts.most_common()
        if len(ranked)==1 or ranked[0][1]>ranked[1][1]:return ranked[0][0],'in_park'
        return doc['scenic_code']+':scope_uncertain','unknown'
    return doc['scenic_code'],'in_park'


def focus_span(ev,doc,sources):
    """The first sentence of a long numbered paragraph is its heading, without truncating names."""
    if len(ev['text'])>70:
        stop=re.search(r'[。!！?？]',ev['text'])
        if stop:return trim_span(doc,ev['start'],ev['start']+stop.start(),sources)
    return ev


def chain_core(ev,doc,sources):
    text=ev['text'];start=ev['start'];end=ev['end']
    # Strip a descriptive prefix only if its colon is before the first route separator.
    sep=ARROW.search(text)
    prefix=re.search(r'(?:路线|线路|顺序|然后|总结)[^:：]{0,18}[:：]',text)
    if prefix and sep and prefix.end()<=sep.start():start+=prefix.end();text=doc['content'][start:end]
    # Sentence commentary after the node chain is not another destination.
    stop=re.search(r'[。!！?？;；]',text)
    if stop:end=start+stop.start()
    return trim_span(doc,start,end,sources)


def discover_document(doc,mentions,sources,aliases):
    candidates=[];log=[]
    state={}

    def reset(title=None):
        state.clear();state.update(title=title or doc.get('title',''),period=None,period_evidence=None,day=None,
            day_evidence=None,station=[],timed=[],chains=[],narrative=[],groups=[],list_steps=[],heading_atoms=[],
            explicit=False,cue='',recent=[],blocked=False,period_children=0,group_id=None,heading_ev=None)

    def ctx(group=False):
        return dict(period=state['period'],period_evidence=state['period_evidence'],day=state['day'],
            day_evidence=state['day_evidence'],order_group=state['group_id'] if group else None)

    def record(ev,signal,status,reason):
        row=dict(evidence=ev,signal=signal,status=status,reason=reason)
        log.append(row);return row

    def add_candidate(steps,fmt,cue,logs):
        if not steps:return
        refs=[m for s in steps for m in s['route_poi_refs']+s['p3_normalized_links']]
        attraction=[m for m in refs if m['poi_role']=='attraction' and m['scope']!='outside']
        useful={m['canonical_name'] or m['text'] for m in attraction}
        transport_only=not useful and sum(m.get('subtype')=='station' for m in refs)>=2
        accepted=len(steps)>=2 and len(useful)>=2
        if transport_only and len(steps)>=2:accepted=True
        if any(s['evidence']['origins'][0].get('kind')=='image' for s in steps) and len(steps)<3:accepted=False
        ordinals=[(s['time']['day'],s['source_ordinal']) for s in steps if s.get('source_ordinal') is not None]
        if any(day_a==day_b and b<=a for (day_a,a),(day_b,b) in zip(ordinals,ordinals[1:])):accepted=False
        scope,gran=infer_scope(doc,steps,mentions,aliases)
        if transport_only:gran='transport_subroute'
        ident=hashlib.sha256((doc['doc_id']+'|'+str(steps[0]['evidence']['start'])+'|'+fmt).encode()).hexdigest()[:10]
        route=dict(route_id=f"{doc['doc_id']}-R-{ident}",doc_id=doc['doc_id'],scenic_code=doc['scenic_code'],
            source_text_type=doc['text_type'],source_url=doc.get('source_url',''),title=state['title'] or doc.get('title',''),
            nature=route_nature(doc,cue),scope=scope,granularity=gran,steps=steps,format=fmt,
            selection_reason='程序全文扫描并由通用格式/实体阈值自动接纳',review_status='automatic_unreviewed',
            nature_evidence=cue[:500],limits=['规则输出尚无独立人工准确率；缺失信息保留未知。'],
            _log_rows=logs,_accepted=accepted)
        for row in logs:row.update(status='accepted' if accepted else 'rejected',reason='format_and_p2_support' if accepted else 'insufficient_steps_or_distinct_poi',route_id=route['route_id'])
        if accepted:candidates.append(finalize_route(route))

    def flush():
        # Prefer detailed numbered steps over the overview chain using structural overlap.
        # This is evaluated from this source/section only, never from a document ID.
        if state['station']:
            add_candidate([s for s,row in state['station']],'numbered',state['cue'],[row for s,row in state['station']])
        if state['timed']:
            add_candidate([s for s,row in state['timed']],'timed_segments',state['cue'],[row for s,row in state['timed']])
        for seq in state['chains']:
            add_candidate(seq['steps'],'arrow_chain',seq['cue'],seq['logs'])
        if state['narrative']:
            add_candidate([s for s,row in state['narrative']],'narrative_connectives',state['cue'],[row for s,row in state['narrative']])
        if state['groups']:
            add_candidate([s for s,row in state['groups']],'period_groups',state['cue'],[row for s,row in state['groups']])
        if state['list_steps']:
            add_candidate([s for s,row in state['list_steps']],'explicit_ordered_list',state['cue'],[row for s,row in state['list_steps']])
        # A period header itself is a step only when no subordinate steps were detected.
        atoms=[(s,row) for s,row in state['heading_atoms'] if s['order_group'] not in {x['order_group'] for x,row in state['groups']}]
        if len(atoms)>=2:add_candidate([s for s,row in atoms],'period_atoms',state['cue'],[row for s,row in atoms])

    reset();previous_source=None
    for p in doc['paragraphs']:
        key=origin_key(doc,p,sources)
        if previous_source is not None and key!=previous_source:
            flush();reset()
        previous_source=key
        ev=evidence(doc,p['start'],p['end'],sources);text=ev['text'].strip()
        recent=' '.join(state['recent'][-2:]);state['recent'].append(text)
        if len(state['recent'])>3:state['recent'].pop(0)
        if not text:continue
        if TOUR_TITLE.match(text) or (len(text)<=45 and not ARROW.search(text) and re.search(r'(?:路线|线路)[一二三\d]+|(?:方案|路线)[AB一二三\d]+',text)):
            flush();reset(text);state['explicit']=True;state['cue']=text
            record(ev,'route_heading','boundary','new_route_title');continue
        route_signal=bool(ROUTE_TITLE.search(text) and (ARROW.search(text) or text.endswith((':','：'))))
        if route_signal:
            state.update(blocked=False,explicit=True)
            if len(text)<=100:state['cue']=text
            if '参考' in text:state.update(period=None,period_evidence=None,group_id=None)
        if (HARD_STOP.match(text) or TIP_HEADING.match(text)) and not CLOCK.match(text) and not route_signal:
            flush();reset();state['blocked']=True
            record(ev,'topic_boundary','boundary','non_route_topic');continue
        dm=DAY.match(text)
        if dm:
            state.update(day=number(dm[1] or dm[2]),day_evidence=ev,period=None,period_evidence=None,
                         group_id=None,explicit=True,blocked=False)
            state['cue']+=' '+text
            record(ev,'day_heading','boundary','day_state_updated')
            # Day headings describe a day, not necessarily a distinct visit step.
            continue
        trailing_period=re.search(r'('+PERIOD+r')(?:的路线|.{0,12}(?:继续出发|游玩路线))[:：]$',text)
        if trailing_period:
            pe=evidence(doc,ev['start']+trailing_period.start(),ev['end'],sources)
            state.update(period=trailing_period[1],period_evidence=pe,explicit=True,blocked=False,group_id='time-'+str(pe['start']))
            state['cue']+=' '+text
            record(pe,'period_heading','boundary','trailing_period_scope');continue
        ph=PERIOD_HEADING.match(text)
        if ph:
            state.update(period={'早上':'上午','午后':'下午'}.get(ph[1],ph[1]),period_evidence=ev,
                         group_id='time-'+str(ev['start']),blocked=False,heading_ev=ev)
            row=record(ev,'period_heading','boundary','time_scope_updated')
            if ph[2] and inside(mentions,ev,'POI') and not ARROW.search(ph[2]):
                atom=make_step(ev,'period_atom',doc,mentions,sources,ctx(True),aliases)
                state['heading_atoms'].append((atom,row))
            if ph[2] and ARROW.search(ph[2]):
                ev=trim_span(doc,ev['start']+ph.start(2),ev['end'],sources);text=ev['text']
            else:continue
        # Standalone route labels change interpretation, not a manually supplied location.
        if len(text)<=80 and ROUTE_TITLE.search(text) and (text.endswith((':','：')) or (len(text)<=45 and not re.search(r'[。!！?？,，]',text))) and not ARROW.search(text) and not NUMBERED.match(text):
            if '总结' not in text and '提醒' not in text:
                if '参考' in text:
                    state.update(period=None,period_evidence=None,group_id=None)
                state['explicit']=True;state['blocked']=False;state['cue']=text
                record(ev,'route_heading','boundary','route_context_enabled')
            continue
        nm=NUMBERED.match(text)
        # Circled region outlines group the following route, not individual visits.
        if nm and re.search(r'[|｜]',text) and not CLOCK.search(text):
            record(ev,'section_outline','boundary','region_outline_not_visit');continue
        # Route-summary clauses have independent, local period headers.
        if (';' in text or '；' in text) and ARROW.search(text):
            chunks=list(re.finditer(r'[^;；]+',ev['text']))
        else:chunks=[None]
        if ARROW.search(text):
            if state['blocked']:
                record(ev,'arrow','rejected','non_route_topic_active');continue
            for chunk in chunks:
                ce=trim_span(doc,ev['start']+chunk.start(),ev['start']+chunk.end(),sources) if chunk else ev
                ce=chain_core(ce,doc,sources)
                if not ce:continue
                fragment_context=ctx();pm=re.search(r'(?:^|[:：])('+PERIOD+r')',ce['text'])
                if pm:
                    fragment_context.update(period={'早上':'上午','午后':'下午'}.get(pm[1],pm[1]),period_evidence=ce)
                nodes=split_chain(ce,doc,sources)
                # A node followed by comma commentary retains the node, not all later mentions.
                clipped=[]
                for node in nodes:
                    comma=re.search(r'[,，]',node['text'])
                    if comma and comma.start()>0:
                        node=trim_span(doc,node['start'],node['start']+comma.start(),sources)
                    if node:clipped.append(node)
                nodes=clipped
                row=record(ce,'arrow','candidate','format_detected')
                if len(nodes)<2:
                    if pm and re.search(r'吃饭|用餐|午餐|休息|休整',ce['text']) and state['chains']:
                        st=make_step(ce,'time_atom',doc,mentions,sources,fragment_context,aliases)
                        state['chains'][-1]['steps'].append(st);state['chains'][-1]['logs'].append(row)
                        continue
                    row.update(status='rejected',reason='no_complete_chain');continue
                if ce['origins'][0].get('kind')=='image' and (ce['text'].count('(')!=ce['text'].count(')') or ce['text'].count('（')!=ce['text'].count('）') or len(clean_label(nodes[-1]['text']))<2):
                    row.update(status='rejected',reason='truncated_ocr_chain');continue
                if re.search(r'不建议|强烈推荐|建议[:：]|可以走|不走|不能走',ce['text']) and len(nodes)<=3:
                    row.update(status='rejected',reason='advisory_edge_not_whole_route');continue
                refs=inside(mentions,ce,'POI')
                if re.search(r'高铁|机场|航班|[CDG]\d{3,4}',ce['text']) and sum(m['poi_role']=='attraction' and m['scope']!='outside' for m in refs)<2:
                    row.update(status='rejected',reason='external_transport');continue
                if not refs:
                    row.update(status='rejected',reason='no_p2_location_support');continue
                if not any(m['poi_role']=='attraction' and m['scope']!='outside' for m in refs) and not any(m['scope']!='outside' and m['subtype']=='station' for m in refs):
                    row.update(status='rejected',reason='outside_transport_or_no_visit_node');continue
                mode='segment' if re.search(r'[（(]\s*\d{1,2}:\d{2}\s*[-—至]\s*\d',ce['text']) or (nm and re.match(r'\s*\d{1,2}:\d{2}\s*[-—至]',NUMBERED.sub('',ce['text']))) else 'chain'
                cue=' '.join([state['cue'],recent,ev['text']])
                local_advice=bool(state['recent'][:-1] and re.search(r'推荐|建议',state['recent'][-2]) and len(state['recent'][-2])<=20)
                if local_advice:cue='推荐路线 '+cue
                if mode=='segment':
                    st=make_step(ce,'segment',doc,mentions,sources,fragment_context,aliases)
                    if nm:
                        st['source_ordinal']=number(nm[2] or nm[3] or nm[4] or str('①②③④⑤⑥⑦⑧⑨⑩'.find(nm[5])+1))
                        state['station'].append((st,row))
                    else:state['timed'].append((st,row))
                else:
                    steps=[make_step(n,'chain',doc,mentions,sources,fragment_context,aliases) for n in nodes]
                    first,last=clean_label(nodes[0]['text']),clean_label(nodes[-1]['text'])
                    category='advice' if local_advice else 'main'
                    previous=next((c for c in reversed(state['chains']) if c['category']==category),None)
                    continuous=previous and (previous['last']==first or ce['text'].lstrip().startswith(('→','➡')))
                    # Consecutive summary clauses with explicit time scopes form one route.
                    if previous and chunk and pm:continuous=True
                    if previous and state['explicit'] and ACTUAL.search(state['cue']) and not local_advice:continuous=True
                    if previous and state['period_evidence'] and previous['period']!=state['period'] and state['explicit']:continuous=True
                    if previous and state['day'] is not None and previous['day']!=state['day'] and TOUR_TITLE.match(state['title']):continuous=True
                    if continuous:
                        if previous['last']==first:
                            row['boundary_restatement']=nodes[0]
                            steps=steps[1:]
                        previous['steps'].extend(steps);previous['logs'].append(row);previous.update(last=last,day=state['day'],period=state['period'])
                    else:state['chains'].append(dict(steps=steps,logs=[row],last=last,day=state['day'],cue=cue,category=category,period=state['period']))
            continue
        if nm and not state['blocked']:
            if not text.startswith('第') and not CLOCK.search(text) and not state['explicit']:
                record(ev,'numbered','rejected','numbered_list_without_route_context');continue
            core=focus_span(ev,doc,sources)
            if not inside(mentions,core,'POI') and not re.search(r'入园|出园|小火车|缆车|午餐|巡游|表演|观看|游览|\d{1,2}:\d{2}',core['text']):
                record(core,'numbered','rejected','numbered_tip_without_route_anchor');continue
            ordinal=number(nm[2] or nm[3] or nm[4] or str('①②③④⑤⑥⑦⑧⑨⑩'.find(nm[5])+1))
            if ordinal==1 and len(state['station'])>=2 and state['station'][-1][0]['time']['day']==state['day']:
                flush();reset();state['cue']=recent
            st=make_step(core,'numbered',doc,mentions,sources,ctx(),aliases)
            st['source_ordinal']=ordinal
            if core['end']<ev['end']:st['context_evidence']=[evidence(doc,core['end'],ev['end'],sources)]
            state['station'].append((st,record(core,'numbered','candidate','numbered_route_anchor')))
            if not state['cue']:state['cue']=recent
            continue
        if state['blocked']:continue
        if CLOCK.match(text) and re.search(r'入园|出园|表演|巡游|午餐|游览|到|去|占位',text):
            st=make_step(focus_span(ev,doc,sources),'time_atom',doc,mentions,sources,ctx(),aliases)
            target='station' if state['station'] else 'list_steps'
            state[target].append((st,record(ev,'clock_step','candidate','local_clock_and_action')));continue
        # Headings such as '火箭过山车 尖叫指数' are unordered siblings within their period.
        if state['period_evidence'] and len(text)<65 and inside(mentions,ev,'POI') and re.search(r'指数|^.{1,15}(?:馆|剧场|过山车|餐厅|转马|跳跳)$',text):
            st=make_step(ev,'period_item',doc,mentions,sources,ctx(True),aliases)
            state['groups'].append((st,record(ev,'period_child','candidate','short_poi_heading')));continue
        if state['explicit'] and re.search(r'依次',state['cue']) and len(text)<95:
            if inside(mentions,ev,'POI') or re.search(r'表演|前往|午餐|休息|旋转木马|花车巡游',text) or (2<=len(text)<20 and not re.search(r'[。!?！？]',text)):
                if not re.search(r'一定带|备雨衣|提前查|隐藏玩法|怎么玩|没有走|不肯|没去',text):
                    state['list_steps'].append((make_step(ev,'ordered_list',doc,mentions,sources,ctx(),aliases),record(ev,'ordered_list','candidate','explicit_list_scope')))
                    continue
        # Only a connective plus a following P2 destination creates a narrative step.
        for sentence in re.finditer(r'[^。!！?？;；]+',ev['text']):
            clause=sentence[0];match=NARRATIVE.search(clause)
            if not match:continue
            se=evidence(doc,ev['start']+sentence.start(),ev['start']+sentence.end(),sources)
            row=record(se,'narrative','candidate','connective_found')
            if re.search(r'如果|建议|原计划|打算|没去|不去|可以|想去|不肯',clause):
                row.update(status='rejected',reason='conditional_or_unrealized_action');continue
            after=se['start']+match.end()
            dest=[m for m in inside(mentions,se,'POI') if after<=m['start']<=after+18]
            if not dest:
                row.update(status='rejected',reason='no_destination_after_connective');continue
            st=make_step(se,'narrative',doc,mentions,sources,ctx(),aliases,primary_start=after)
            st['route_poi_refs']=[r for r in st['route_poi_refs'] if r['mention_id']==dest[0]['mention_id']]
            state['narrative'].append((st,row))
            if not state['cue']:state['cue']=recent+' '+clause
    flush()
    return candidates,log


def deduplicate(routes,logs):
    """Keep distinct days/branches; suppress matching summaries and OCR copies automatically."""
    bydoc=defaultdict(list)
    for r in routes:bydoc[r['doc_id']].append(r)
    retained=[]
    def rank(r):
        kind=r['steps'][0]['evidence']['origins'][0].get('kind')
        return (kind in ('body','legacy'), r['format']=='numbered',sum(s['time']['period']!='未说明' for s in r['steps']),len(r['steps']))
    for docroutes in bydoc.values():
        kept=[]
        for r in sorted(docroutes,key=rank,reverse=True):
            seq=visit_sequence(r);duplicate=None
            for old in kept:
                if r['scope']!=old['scope'] or r['granularity']!=old['granularity']:continue
                days={s['time']['day'] for s in r['steps']};old_days={s['time']['day'] for s in old['steps']}
                if days!=old_days:continue
                other=visit_sequence(old)
                if not seq or not other:continue
                similarity=lcs(seq,other)/max(len(seq),len(other))
                overlap=len(set(seq)&set(other))/max(1,min(len(set(seq)),len(set(other))))
                # With numbered detail, suppress its broader unordered/arrow overview only when heavily overlapping.
                summary=old['format']=='numbered' and r['format']=='arrow_chain' and len(old['steps'])>=3 and overlap>=.8
                if similarity>=.9 or summary:duplicate=old;break
            if duplicate:
                for row in r['_log_rows']:row.update(status='suppressed_duplicate',reason='same_document_route_representation',retained_route_id=duplicate['route_id'])
            else:kept.append(r)
        retained.extend(kept)
    retained.sort(key=lambda r:(r['doc_id'],r['steps'][0]['evidence']['start']))
    for r in retained:r.pop('_log_rows',None);r.pop('_accepted',None)
    return retained


def automatic_pairs(routes):
    """All comparable same-scope pairs are emitted; no fixed IDs or selected paragraphs."""
    official=[r for r in routes if r['source_text_type']=='official_route' and r['granularity'] in ('in_park','park_level')]
    visitors=[r for r in routes if r['source_text_type']!='official_route' and r['granularity'] in ('in_park','park_level')]
    pairs=[]
    for a in official:
        for b in visitors:
            if (a['scenic_code'],a['scope'],a['granularity'])!=(b['scenic_code'],b['scope'],b['granularity']):continue
            if len(set(visit_sequence(a))&set(visit_sequence(b)))<2:continue
            result=compare(a,b)
            result['pairing_rule']='same_scenic_scope_granularity_and_at_least_two_shared_poi'
            result['analysis']='程序识别到共同景点%d个，官方独有%d个，游客文本独有%d个。'%(len(result['shared']),len(result['official_only']),len(result['visitor_only']))
            if result['order_status']=='applicable':result['analysis']+='共同景点顺序相似度为%.3f。'%result['common_order_similarity']
            else:result['analysis']+='存在无序组或未决分支，顺序指标不适用。'
            result['analysis']+='文本路线结构差异不能独立证明游客偏好或实际用时；原因需结合原文证据解释。'
            pairs.append(result)
    return sorted(pairs,key=lambda c:(c['official_route_id'],c['visitor_route_id']))


def discover_all(documents,entity_docs,sources):
    aliases=canonical_aliases(entity_docs);routes=[];survey=[]
    for doc in documents:
        found,log=discover_document(doc,entity_docs[doc['doc_id']]['mentions'],sources,aliases.get(doc['scenic_code'],{}))
        found=deduplicate(found,log);routes.extend(found)
        for row in log:
            if row['status']=='candidate':row.update(status='rejected',reason='no_complete_supported_route')
        survey.append(dict(doc_id=doc['doc_id'],scenic_code=doc['scenic_code'],title=doc.get('title',''),
            candidate_count=sum(x['signal'] not in ('day_heading','period_heading','topic_boundary','route_heading') for x in log),
            route_ids=[r['route_id'] for r in found],disposition='automatic_routes' if found else 'no_rule_supported_route',
            note='所有边界和状态由本次全文扫描产生；未识别不代表不存在路线。',candidates=log))
    return routes,survey
