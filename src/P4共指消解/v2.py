"""POS candidates, event channel and source-aware bounded retrieval."""
import hashlib
import re
from collections import Counter
from tokenization import Tagger
from event_candidates import spans as event_spans, ACTION, ROUTE

EVENT_HEAD = r'路线|行程|安排|情况|方式|方法|建议|攻略|做法|选择|玩法|体验|操作'
FULL = re.compile(r'(?:这|那)(?:两|几|一)?(?:个|种|条|些)?(?:'+EVENT_HEAD+r'|线路|景点|景区|园区|地方|区域|设施|项目|缆车|小火车|交通工具|工作人员|游客|动物)|这样|那样')
RESET = re.compile(r'^\s*(?:第[一二三四五六七八九十\d]+[天日站]|路线[一二三四五\d]|[一二三四五六七八九十\d]+[、.．]|[^。！？]{1,18}[：:]\s*$)')


def run(doc, p2, sources, config, core, artifacts=None):
    options = config['v2']
    tagger = Tagger(p2.get('mentions', []), config) if options['jieba'] else None
    tokens = {}
    for p in doc['paragraphs']:
        if doc['content'][p['start']:p['end']] != p['text']:
            raise ValueError('P1段落偏移不一致')
        tokens[p['i']] = tagger.cut(p['text'], p['start']) if tagger else []
    oversized=[]
    if options['events']:
        for p in doc['paragraphs']:
            for m in re.finditer(r'[^。！？!?；;\n]+',p['text']):
                if len(m.group())>options['event_max_chars'] and (ACTION.search(m.group()) or ROUTE.search(m.group())):
                    oversized.append({'start':p['start']+m.start(),'end':p['start']+m.end(),
                                      'para_id':p['i'],'reason_code':'event_structure_too_long_not_truncated'})
    if artifacts is not None:
        artifacts.update(doc_id=doc['doc_id'],metadata=tagger.metadata() if tagger else None,
                         paragraphs=[{'para_id':p['i'],'tokens':tokens[p['i']]} for p in doc['paragraphs']],
                         generation_limits=oversized)
    pool = core.build_mentions(doc, p2, config, sources)
    text = doc['content']
    paragraphs = {p['i']: p for p in doc['paragraphs']}
    units = {p['i']: core.source_for(doc['doc_id'], p['i'], sources) for p in doc['paragraphs']}

    def add(a, b, typ, method, rule, para, **extra):
        if a >= b:
            return
        existing = next((c for c in pool if (c['start'], c['end'], c['semantic_type']) == (a,b,typ)), None)
        if existing:
            existing['evidence'].append({'method': method, 'rule_id': rule})
            return
        pool.append({'candidate_id': core.stable_id('N-', doc['doc_id'], a,b,typ),
                     'start': a, 'end': b, 'text': text[a:b], 'semantic_type': typ,
                     'para_id': para['i'], 'source_unit': units[para['i']],
                     'p2_mention_id': None, 'entity_id': None, 'canonical_name': None,
                     'p2_type': None, 'poi_role': None, 'subtype': None,
                     'evidence': [{'method': method, 'rule_id': rule}], **extra})

    def semantics(word, pos):
        for typ, words in config['nouns'].items():
            if word in words:
                return typ
        if pos in {'nr','nr1','nr2','nrj','nrf'}:
            return 'person'
        if pos == 'ns' or any(word.endswith(h) for h in config['compound_location_heads']):
            return 'location'
        if any(word.endswith(h) for h in config['compound_facility_heads']):
            return 'facility'
        if any(word.endswith(h) for h in config['nouns']['animal']) and len(word) <= 8:
            return 'animal'
        return 'other'

    p2spans = [(c['start'], c['end']) for c in pool if c['p2_mention_id']]
    for p in doc['paragraphs']:
        tt = tokens[p['i']]
        for i,t in enumerate(tt):
            if t['pos'] not in options['noun_pos'] or len(t['text']) < 2:
                continue
            if any(a <= t['start'] and t['end'] <= b for a,b in p2spans):
                continue
            typ = semantics(t['text'], t['pos'])
            add(t['start'],t['end'],typ,'jieba','POS_NOUN',p,pos=t['pos'],head=t['text'])
            if i and tt[i-1]['end'] == t['start'] and tt[i-1]['pos'] in {'n','ns','nz','a','b'}:
                prev = tt[i-1]
                if len(prev['text']+t['text']) <= 12 and typ != 'other':
                    add(prev['start'],t['end'],typ,'compound','POS_MODIFIER_HEAD',p,head=t['text'])
        if options['events']:
            for e in event_spans(p,tt,options['event_max_chars']):
                add(e['start'],e['end'],'event','event',e['rule'],p,shape=e['shape'])
    if options['jieba']:
        for c in list(pool):
            if c['semantic_type']=='event':
                continue
            p=paragraphs[c['para_id']]
            before=text[max(p['start'],c['start']-14):c['start']]
            q=re.search(r'(?:[一二三四五六七八九十百两几多]+|\d+)(?:头|只|位|群|个)(?:成年|幼年|野生)?$',before)
            if q and c['semantic_type'] in {'animal','person'}:
                add(c['start']-len(q.group()),c['end'],c['semantic_type'],'compound','QUANTIFIED_NOUN',p,head=c['text'])
            if c['semantic_type'] in {'location','facility'}:
                suffix=re.match(r'[」”]?附近',text[c['end']:p['end']])
                if suffix:
                    start=c['start']-1 if c['start']>p['start'] and text[c['start']-1] in '「“' else c['start']
                    add(start,c['end']+len(suffix.group()),'location','compound','LOCATIVE_SUFFIX',p,head=c['text'])
        pool=[c for c in pool if c['p2_mention_id'] or not any(d['start']<c['start'] and d['end']==c['end'] and
              d['semantic_type']==c['semantic_type'] and any(e['rule_id']=='QUANTIFIED_NOUN' for e in d['evidence']) for d in pool)]
    pool.sort(key=lambda c:(c['start'],c['end'],c['candidate_id']))
    allsent = []
    for p in doc['paragraphs']:
        for s in core.sentences(p,units[p['i']]):
            allsent.append({**s,'para_id':p['i']})
    records=[]
    for pi,p in enumerate(doc['paragraphs']):
        ss=core.sentences(p,units[p['i']])
        refs=core.detect(doc,p,ss,units[p['i']])
        if options['expanded_references']:
            # Formal spans are demonstrative cores; retain full nominal phrase separately.
            # Classifiers 条/座/只 attach to the following noun; 一/次/段/样 belong to these trigger forms.
            for r in refs:
                ref=r['reference']; a=ref['start']-p['start']
                r['trigger']=dict(ref)
                rest=p['text'][a:]
                composite=re.match(r'(?:这|那)(?:一|次|段|样)',rest)
                if composite:
                    ref.update(end=ref['start']+len(composite.group()),text=composite.group())
                    r['detection_exclusion']=None
                elif ref['text'] in {'这条','那条','这座','那座','这只','那只'}:
                    ref.update(end=ref['start']+1,text=ref['text'][0])
                m=FULL.match(p['text'],a)
                if m:
                    r['reference_phrase']={'start':p['start']+m.start(),'end':p['start']+m.end(),'text':m.group()}
                r['reference_id']=core.stable_id('R-',doc['doc_id'],hashlib.sha256(text.encode()).hexdigest(),ref['start'],ref['end'])
            for m in re.finditer('其中',p['text']):
                a,b=p['start']+m.start(),p['start']+m.end()
                sentence=next(s for s in ss if s['start']<=a<s['end'])
                refs.append({'reference_id':core.stable_id('R-',doc['doc_id'],hashlib.sha256(text.encode()).hexdigest(),a,b),
                    'doc_id':doc['doc_id'],'scenic_code':doc['scenic_code'],'title':doc.get('title',''),
                    'para_id':p['i'],'source_unit':units[p['i']],'sentence_id':sentence['sentence_id'],'sentence_index':sentence['index'],
                    'reference':{'start':a,'end':b,'text':m.group(),'type':'group'},
                    'sentence':{k:sentence[k] for k in ['start','end','text']},'context':{k:p[k] for k in ['start','end','text']},
                    'detection_exclusion':None,'non_entity_rule':None})
        for r in refs:
            ref=r['reference']
            if options['events'] and (r['non_entity_rule']=='event_reference' or re.search(EVENT_HEAD,r.get('reference_phrase',ref)['text']) or ref['text'] in {'这样','那样','这段','那段','这一','那一','这次','那次'}):
                ref['type']='event';r['non_entity_rule']=None
                if ref['text'] in {'这样','那样','这次','那次'}:
                    r['detection_exclusion']=None
            after=text[ref['end']:min(p['end'],ref['end']+30)]
            if options['events'] and '线路' in r.get('reference_phrase',ref)['text']:
                ref['type']='event'
            if ref['type']=='unknown':
                if re.match(r'是.{0,15}(?:物种|动物)',after):
                    ref['type']='animal'
                elif re.match(r'(?:是|也是).{0,18}(?:海子|景点|景区|地方)',after):
                    ref['type']='location'
                elif re.match(r'(?:个)?项目',after):
                    ref['type']='object'
            if ref['text'] in {'这里','这儿','此处'} and re.match(r'(?:特别|再次|再)?提醒',after):
                r['non_entity_rule']='discourse_expression'
            candidates=[];blocked=Counter();blocked_details=[]
            current=next(i for i,s in enumerate(allsent) if s['start']<=ref['start']<s['end'])
            for c in pool:
                if c['end']>ref['start']:
                    continue
                distance=ref['start']-c['end']
                is_event=c['semantic_type']=='event'
                limit=options['event_max_chars']*options['event_previous_blocks'] if is_event else config['window_chars']
                if distance>limit:
                    continue
                why=None
                cp=paragraphs[c['para_id']]
                if c['source_unit']['unit_id']!=r['source_unit']['unit_id']:
                    why='source_boundary'
                elif c['para_id']!=p['i']:
                    previous=doc['paragraphs'][pi-1] if pi else None
                    if not options['cross_paragraph'] or not previous or c['para_id']!=previous['i']:
                        why='paragraph_boundary'
                    elif r['source_unit']['kind']!='body' or RESET.search(p['text']) or RESET.search(cp['text']):
                        why='topic_or_unverified_continuity'
                sentence_index=next((i for i,s in enumerate(allsent) if s['start']<=c['start']<s['end']),-1)
                gap=current-sentence_index
                if not why and c['para_id']==p['i'] and not is_event and gap>config['previous_sentences']:
                    why='window_or_sentence_boundary'
                if why:
                    blocked[why]+=1;blocked_details.append({**c,'reason_code':why});continue
                candidates.append({**c,'distance':distance,'sentence_index':sentence_index,'sentence_gap':gap,
                                   'retrieval_tier':'local' if c['para_id']==p['i'] else 'previous_continuous_body_paragraph'})
            # Bound event retrieval by complete structures, not truncated strings.
            events=sorted([c for c in candidates if c['semantic_type']=='event'],key=lambda c:(-c['end'],c['start']))
            ends=sorted({c['end'] for c in events},reverse=True)[:options['event_previous_blocks']]
            candidates=[c for c in candidates if c['semantic_type']!='event' or c['end'] in ends]
            if ref['type'] != 'event' and any(c['retrieval_tier']=='local' and core.suitability(r,c,text)[0] is not None for c in candidates):
                removed=[c for c in candidates if c['retrieval_tier']!='local']
                for c in removed:
                    blocked['local_candidate_available']+=1
                    blocked_details.append({**c,'reason_code':'local_candidate_available'})
                candidates=[c for c in candidates if c['retrieval_tier']=='local']
            nearest=max((c for c in candidates if c['p2_mention_id']),key=lambda c:(c['end'],c['start']),default=None)
            r.update(candidates=candidates,blocked_candidate_counts=dict(blocked),blocked_candidates=blocked_details,
                     baseline_result={'status':'resolved' if nearest else 'unresolved','antecedent':nearest},
                     status='unresolved',reason_code='no_candidate',antecedent=None,antecedents=[],score=None,runner_up_score=None,rule_ids=[])
            if r['detection_exclusion']:
                r.update(status='excluded',reason_code=r['detection_exclusion'])
            elif r['non_entity_rule']:
                r.update(status='non_entity_reference',reason_code=r['non_entity_rule'])
            else:
                rank(r,doc,config,core)
            r['tokenization']=tagger.metadata() if tagger else None
            r['sentence_tokens']=[t for t in tokens[p['i']] if r['sentence']['start']<=t['start']<r['sentence']['end']]
            r['candidate_generation_limits']=[o for o in oversized if o['end']<=ref['start'] and ref['start']-o['end']<=options['event_max_chars']*options['event_previous_blocks']]
            records.append(r)
    return sorted(records,key=lambda r:(r['reference']['start'],r['reference']['end']))


def rank(r,doc,config,core):
    event=r['reference']['type']=='event'
    ranked=[]
    for c in r['candidates']:
        if event:
            fit=1 if c['semantic_type']=='event' else None
            rule='EVENT_STRUCTURE_MATCH'
        else:
            fit,rule=core.suitability(r,c,doc['content'])
            if r['reference']['type']=='group':
                fit=1 if c['semantic_type'] in {'animal','person'} and re.search(r'[二三四五六七八九十两几多群]|[2-9]',c['text']) else None
                rule='EXPLICIT_GROUP_MATCH'
        c.update(eligible=fit is not None,type_rule=rule)
        if fit is None:
            continue
        if not event and r['reference']['text'] in {'他们','她们','它们'}:
            before=doc['content'][max(0,c['start']-5):c['start']]
            if not (rule=='ANIMAL_PERSONIFICATION' or re.search(r'动物|游客|工作人员|朋友|孩子|大家|鸟类|人群|群|们',c['text']) or
                    (c['semantic_type']=='animal' and not re.search(r'一头|一只',before))):
                c.update(eligible=False,type_rule='collective_unsupported');continue
        locality=1 if c['sentence_gap']==0 else .5 if c['sentence_gap']==1 else 0
        advanced=config['v2'].get('advanced_ranking',False)
        if advanced:
            dist=1/(1+c['distance']/config['v2']['distance_scale_chars'])
            if not event and c['semantic_type']=='animal' and not any(e['rule_id']=='QUANTIFIED_NOUN' for e in c['evidence']):
                fit=.75
            if not event and c['text'] in sum(config['nouns'].values(),[]) and c['semantic_type'] in {'location','facility'}:
                fit=min(fit,.5)
        else:
            dist=max(0,1-c['distance']/(config['v2']['event_max_chars'] if event else config['window_chars']))
        parts={'type':fit,'distance':dist,'sentence':locality}
        c['score_parts']=parts
        c['score']=sum(config['weights'][k]*v for k,v in parts.items())
        if event:
            shape=c.get('shape')
            head=r.get('reference_phrase',r['reference'])['text']
            preferred=('route' if '路线' in head or '行程' in head else 'sequence' if '安排' in head else 'route' if r['reference']['text'] in {'这段','那段','这一','那一'} else None)
            if advanced and preferred and shape != preferred:
                c.update(eligible=False,type_rule='event_focus_mismatch')
                continue
            # Structural quality replaces locality for events; no arbitrary longest-string tie-break.
            quality=1 if shape==preferred or (not preferred and shape in {'clause','action','sequence'}) else .4
            c['score_parts']={'structure':quality,'distance':dist,'continuity':1 if c['retrieval_tier']=='local' else .6}
            c['score']=.5*quality+.3*dist+.2*c['score_parts']['continuity']
        c['object_key']='entity:'+str(c['entity_id']) if c['entity_id'] else c['candidate_id']
        ranked.append(c)
    objects={}
    for c in ranked:
        if c['object_key'] not in objects or c['end']>objects[c['object_key']]['end']:
            objects[c['object_key']]=c
    ranked=sorted(objects.values(),key=lambda c:(-c['score'],c['candidate_id']))
    if event:
        # A sequence/route supersedes its contained actions when the reference names that whole structure.
        ranked=[c for c in ranked if not any(d['start']<=c['start'] and c['end']<=d['end'] and
                 (d['start'],d['end'])!=(c['start'],c['end']) and d['score']>c['score'] and
                 d.get('shape') in {'route','sequence'} for d in ranked)]
    r['ranked_candidate_ids']=[c['candidate_id'] for c in ranked]
    if not ranked:
        r['reason_code']='event_candidate_missing' if event else 'type_mismatch' if r['candidates'] else 'no_candidate'
        return
    first=ranked[0];second=ranked[1] if len(ranked)>1 else None
    r.update(score=first['score'],runner_up_score=second['score'] if second else None)
    phrase=r.get('reference_phrase',r['reference'])['text']
    if event and re.search(r'两|几|这些|那些',phrase):
        routes=[c for c in ranked if c.get('shape')=='route']
        disjoint=[]
        for c in routes:
            if all(c['end']<=d['start'] or d['end']<=c['start'] for d in disjoint):
                disjoint.append(c)
        if '两' in phrase and len(disjoint)==2:
            r.update(status='resolved',reason_code='explicit_two_routes',antecedents=sorted(disjoint,key=lambda c:c['start']),rule_ids=['TWO_DISJOINT_ROUTE_BLOCKS'])
        else:
            r['reason_code']='collective_unsupported'
    elif first['score']<config['accept_threshold']:
        r['reason_code']='low_score'
    elif second and first['score']-second['score']<config['min_margin']:
        r['reason_code']='ambiguous'
    else:
        r.update(status='resolved',reason_code='event_structure_rule' if event else 'type_distance_rule',
                 antecedent=dict(first),antecedents=[dict(first)],rule_ids=[first['type_rule'],*[e['rule_id'] for e in first['evidence']]])
