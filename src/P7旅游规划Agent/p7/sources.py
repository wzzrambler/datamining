from collections import deque

from .db import row, rows
from .errors import BusinessError
from .knowledge import catalog_node, require_scope


def excerpts(conn, version, args):
    require_scope(conn, version, args.route_id, args.step_id)
    queue = deque()
    if args.step_id:
        queue.append(args.step_id)
    if args.ref_id:
        queue.append(args.ref_id)
    elif args.entity_id:
        queue.append(args.entity_id)
    elif args.route_id and not args.step_id:
        queue.append(args.route_id)
    visited, result, seen = set(), [], set()

    def add(doc_id, fragment, precision, observation_id=None):
        if not doc_id:
            return
        doc = row(conn, """SELECT doc_id,title,source_url,content FROM p6_kg.source_document
                         WHERE dataset_id=:v AND doc_id=:d""", v=version,d=doc_id)
        start,end = fragment.get('start'),fragment.get('end')
        quote = fragment.get('text') or fragment.get('quote')
        if doc and isinstance(start,int) and isinstance(end,int) and 0<=start<end<=len(doc['content']):
            quote = doc['content'][start:end]
        else:
            start = end = None
        marker = (doc_id,start,end,quote)
        if marker in seen:
            return
        seen.add(marker)
        result.append(dict(doc_id=doc_id,title=doc['title'] if doc else doc_id,
                           source_url=(doc or {}).get('source_url'),start=start,end=end,
                           excerpt=quote[:1000] if isinstance(quote,str) else None,
                           precision=precision,observation_id=observation_id))

    max_sources = min(args.cursor+16,400)
    while queue and len(visited)<600 and len(result)<max_sources:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        theme = row(conn,'SELECT source_record_ids FROM p6_kg.advice_theme WHERE dataset_id=:v AND theme_id=:id',
                    v=version,id=key)
        if theme:
            queue.extend(theme['source_record_ids'])
            continue
        found = catalog_node(conn,version,key)
        if not found:
            doc = row(conn,'SELECT 1 FROM p6_kg.source_document WHERE dataset_id=:v AND doc_id=:id',v=version,id=key)
            if doc:
                add(key,{},'document')
            elif key in (args.ref_id,args.entity_id):
                raise BusinessError('NOT_FOUND','找不到指定的来源引用',404,id=key)
            continue
        p = found['properties']
        if found['label']=='OBSERVATION':
            provenance=p.get('provenance') or {}
            fragments=provenance.get('quotes') or provenance.get('blocks') or [{}]
            for fragment in fragments:
                if isinstance(fragment,dict):
                    add(fragment.get('doc_id') or provenance.get('doc_id'),fragment,
                        provenance.get('evidence_precision') or 'document',key)
            continue
        evidence = p.get('evidence') or {}
        if isinstance(evidence,dict):
            add(evidence.get('doc_id'),evidence,'step')
        for evidence in p.get('source_evidence') or []:
            add(evidence.get('doc_id'),evidence,'route')
        queue.extend(x['target_id'] for x in rows(conn, """SELECT target_id FROM p6_kg.edge
                WHERE dataset_id=:v AND source_id=:id AND type IN ('SUPPORTED_BY','DERIVED_FROM')
                ORDER BY edge_no""", v=version,id=key))
        queue.extend(p.get('observation_ids') or [])
        for observation in p.get('observations') or []:
            queue.extend(observation.get('observation_ids') or [])
    found=result[args.cursor:args.cursor+15]
    return {'data_version':version,'sources':found,
            'next_cursor':args.cursor+15 if len(result)>args.cursor+15 and args.cursor+15<400 else None}
