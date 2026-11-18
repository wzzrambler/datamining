"""Version-scoped P6 queries. Public details expose curated summaries."""
import re

from .db import row, rows
from .errors import BusinessError

HISTORICAL = '来自 P6 历史资料，开放时间、班次和政策需以当日公告为准。'


def active_version(conn):
    found = row(conn, 'SELECT dataset_id FROM p6_kg.dataset WHERE is_active')
    if not found:
        raise BusinessError('CATALOG_UNAVAILABLE', 'P6 数据尚未导入', 503)
    return found['dataset_id']


def require_version(conn, version):
    if not row(conn, 'SELECT 1 FROM p6_kg.dataset WHERE dataset_id=:v', v=version):
        raise BusinessError('CATALOG_UNAVAILABLE', '指定 P6 发布版本不存在', 404)
    return version


def areas(conn, version):
    return rows(conn, 'SELECT scenic_code,name,aliases FROM p6_kg.scenic_area WHERE dataset_id=:v ORDER BY scenic_code', v=version)


def catalog_node(conn, version, key):
    return row(conn, 'SELECT label,properties FROM p6_kg.node WHERE dataset_id=:v AND node_id=:key', v=version, key=key)


def require_node(conn, version, key, label=None):
    result = catalog_node(conn, version, key)
    if not result or (label and result['label'] != label):
        raise BusinessError('NOT_FOUND', '所请求的 P6 对象不存在', 404, id=key)
    return result['properties']


def search_pois(conn, version, args):
    data = rows(conn, """SELECT p.poi_id,p.name,p.role,p.subtype,p.scenic_code,
           COALESCE((SELECT string_agg(t.title || ' ' || t.summary,' ')
                     FROM p6_kg.advice_theme t WHERE t.dataset_id=p.dataset_id
                       AND t.target_id=p.poi_id AND t.scenic_code=p.scenic_code),'') AS topics
         FROM p6_kg.poi p WHERE p.dataset_id=:v ORDER BY p.name,p.poi_id""", v=version)
    found = []
    for p in data:
        if args.scenic_code and p['scenic_code'] != args.scenic_code:
            continue
        topics = p.pop('topics')
        name = p['name'].casefold()
        terms = [k for k in args.keywords if k and k.casefold() in name]
        theme_hits = [k for k in args.themes if k and k.casefold() in topics.casefold()]
        if (args.keywords or args.themes) and not (terms or theme_hits):
            continue
        p['matched_terms'] = terms + theme_hits
        found.append(((-sum(k.casefold() == name for k in terms), -len(terms), -len(theme_hits), p['poi_id']), p))
    return {'items': [p for _, p in sorted(found)[:args.limit]], 'count': len(found)}


def search_routes(conn, version, args):
    data = rows(conn, """SELECT r.*,n.properties,
          ARRAY(SELECT DISTINCT p.poi_id FROM p6_kg.route_step s JOIN p6_kg.step_poi p USING(dataset_id,step_id)
             WHERE s.dataset_id=r.dataset_id AND s.route_id=r.route_id ORDER BY p.poi_id) AS poi_ids,
          ARRAY(SELECT DISTINCT p2.name FROM p6_kg.route_step s2 JOIN p6_kg.step_poi m USING(dataset_id,step_id)
             JOIN p6_kg.poi p2 USING(dataset_id,poi_id) WHERE s2.dataset_id=r.dataset_id AND s2.route_id=r.route_id
             ORDER BY p2.name) AS poi_names
          FROM p6_kg.route r JOIN p6_kg.node n ON (n.dataset_id,n.node_id)=(r.dataset_id,r.route_id)
          WHERE r.dataset_id=:v""", v=version)
    matched, near = [], []
    for r in data:
        if args.scenic_codes and r['scenic_code'] not in args.scenic_codes:
            continue
        poi_ids = set(r['poi_ids'])
        if poi_ids.intersection(args.avoid_poi_ids):
            continue
        missing = sorted(set(args.must_visit_poi_ids) - poi_ids)
        src_days = [g['day'] for g in r['hierarchy'] if isinstance(g.get('day'), int)]
        known = max(src_days, default=0)
        summary = r['summary_text'] or ''
        audiences = ' '.join(map(str, r['properties'].get('recommended_audiences') or []))
        declared = bool(known and (known > 1 or r['itinerary_scope'] == 'explicit_day'))
        weak = args.days and any(re.search(pattern, summary) for pattern in (
            f'{args.days}\\s*(天|日)', {1:'[一单](天|日)',2:'[二两](天|日)',3:'三(天|日)'}.get(args.days, r'(?!)')
        ))
        if not args.days:
            day_match = 'unspecified'
        elif declared:
            day_match = 'exact' if known == args.days else ('partial' if known < args.days else 'conflict')
        elif weak:
            day_match = 'weak'
        else:
            day_match = 'unknown'
        if day_match == 'conflict':
            continue
        haystack = (summary + ' ' + ' '.join(r['poi_names'])).casefold()
        terms = [k for k in args.keywords if k and k.casefold() in haystack]
        item = {key: r[key] for key in ('route_id','scenic_code','route_class','itinerary_scope','step_count','mapped_stage_count')}
        item.update(summary=summary or ('、'.join(r['poi_names'][:6]) if r['poi_names'] else None),
                    summary_is_overview=not bool(summary), day_match=day_match, matched_terms=terms,
                    must_visit_missing=missing, mapped_poi_count=len(poi_ids),
                    evidence_refs=[{'kind':'node','id':r['route_id']}])
        classes = {'long_touring_sequence':0,'timed_thematic_itinerary':1,'explicit_day_itinerary':1,'multi_park_overview':2}
        ranks = {'exact':0,'weak':1,'partial':2,'unknown':3,'unspecified':0}
        key = (len(missing), ranks[day_match], -len(terms), -int(bool(args.audience and args.audience in audiences)),
               classes.get(r['route_class'],3), -r['mapped_stage_count'], r['route_id'])
        (near if missing else matched).append((key, item))
    return {'items': [r for _,r in sorted(matched)[:args.limit]], 'count':len(matched),
            'near_matches': [r for _,r in sorted(near)[:min(3,args.limit)]] if not matched else []}


def require_scope(conn, v, route_id, step_id):
    if route_id:
        require_node(conn, v, route_id, 'ROUTE')
    if step_id:
        step = row(conn, 'SELECT route_id FROM p6_kg.route_step WHERE dataset_id=:v AND step_id=:s',
                   v=v,s=step_id)
        if not step or (route_id and step['route_id'] != route_id):
            raise BusinessError('INVALID_PLAN_REFERENCE', '步骤与路线不匹配')
        return step['route_id']
    return route_id


def allowed_scope(scope, route_id, step_id, scenic):
    if not scope:
        return True
    if scope.get('scope_status'):
        return False
    if scope.get('scenic_code') and scope['scenic_code'] != scenic:
        return False
    if scope.get('route_id') and scope['route_id'] != route_id:
        return False
    if scope.get('step_id') and scope['step_id'] != step_id:
        return False
    return not any(scope.get(k) and scope[k] != step_id for k in ('from_step_id','to_step_id'))


def linked_summaries(conn, v, key, scenic, route_id=None, step_id=None, topics=()):
    parts = rows(conn, """SELECT n.node_id,n.label,n.properties FROM p6_kg.edge e
          JOIN p6_kg.node n ON (n.dataset_id,n.node_id)=(e.dataset_id,e.target_id)
          WHERE e.dataset_id=:v AND e.source_id=:key AND e.type IN ('HAS_TIME_SUMMARY','HAS_ADVICE_SUMMARY')
          ORDER BY n.node_id""", v=v,key=key)
    time, advice, themes = [], [], []
    for part in parts:
        p = part['properties']
        ref = {'kind':'node','id':part['node_id']}
        if part['label'] == 'TIME_SUMMARY':
            observations = [{k:o.get(k) for k in ('time_text','condition','modality')}
                            for o in p.get('observations',[]) if allowed_scope(o.get('scope'),route_id,step_id,scenic)]
            if observations:
                time.append(dict(attribute=p['attribute'],category=p['category'],historical=True,
                                 values=observations[:5],evidence_refs=[ref]))
        elif not p.get('scope_status') and (not p.get('scenic_code') or p['scenic_code']==scenic):
            items = [i for i in p.get('items',[]) if allowed_scope(i.get('scope'),route_id,step_id,scenic)]
            allowed_ids = {i['item_id'] for i in items}
            for t in (p.get('llm_theme_result') or {}).get('topics',[]):
                if set(t.get('source_item_ids',[])) and set(t['source_item_ids']) <= allowed_ids:
                    themes.append(dict(title=t['label'],summary=t['summary'],current_validity_verified=False,
                                       evidence_refs=[{'kind':'theme','id':t['topic_id']}]))
            for i in items:
                advice.append(dict(category=i.get('category'),topic=i.get('topic'),summary=i.get('text'),
                                   condition=i.get('condition'),evidence_refs=[ref]))
    def choose(records):
        return sorted(records,key=lambda r:-sum(t.casefold() in str(r).casefold() for t in topics))[:5]
    return {'time_summaries':choose(time),'advice_summaries':choose(advice),'advice_themes':choose(themes)}


def entity(conn, v, key, route_id=None, step_id=None, topics=()):
    route_id = require_scope(conn,v,route_id,step_id)
    found = catalog_node(conn,v,key)
    if not found or found['label'] not in {'POI','TRANSPORT','TRANSPORT_LEG'}:
        raise BusinessError('NOT_FOUND','景点或交通实体不存在',404,id=key)
    p = found['properties']
    scenic = p.get('scenic_code')
    return dict(entity_id=key,poi_id=key if found['label']=='POI' else None,name=p.get('name') or p.get('service_name_text') or key,
                scenic_code=scenic,role=p.get('role'),type=found['label'],data_version=v,
                historical_notice=HISTORICAL,evidence_refs=[{'kind':'node','id':key}],
                **linked_summaries(conn,v,key,scenic,route_id,step_id,topics))


def route_details(conn,v,args):
    route = require_node(conn,v,args.route_id,'ROUTE')
    if args.step_ids and not set(args.step_ids) <= {s['step_id'] for s in route['steps']}:
        raise BusinessError('INVALID_PLAN_REFERENCE','存在不属于该路线的步骤')
    steps = [s for s in route['steps'] if (not args.step_ids or s['step_id'] in args.step_ids)
             and (not args.day or (s.get('time') or {}).get('day')==args.day)]
    items = []
    for s in steps[args.cursor:args.cursor+12]:
        pois = [entity(conn,v,k,args.route_id,s['step_id'],args.topics) for k in s.get('poi_ids',[])[:6]]
        time = s.get('time') or {}
        items.append(dict(step_id=s['step_id'],order=s.get('index'),order_group=s.get('order_group'),
                          source_day_no=time.get('day'),period=time.get('period'),historical_time=time.get('clock_texts',[]),
                          pois=pois, unmapped=not bool(pois),evidence_refs=[{'kind':'node','id':s['step_id']}]))
    return dict(route_id=args.route_id,data_version=v,scenic_code=route['scenic_code'],
                summaries=[x['text'] for x in route.get('summaries',[])],hierarchy=route.get('hierarchy',[]),
                itinerary_scope=route['p6_curation']['itinerary_scope'],steps=items, historical_notice=HISTORICAL,
                next_cursor=args.cursor+12 if args.cursor+12<len(steps) else None)


def transport_links(conn,v,args):
    for p in (args.from_poi_id,args.to_poi_id):
        require_node(conn,v,p,'POI')
    items = rows(conn, """SELECT t.leg_id,t.service_name,t.scenic_code,m.properties->>'name' AS mode
        FROM p6_kg.transport_leg t LEFT JOIN p6_kg.node m
          ON (m.dataset_id,m.node_id)=(t.dataset_id,t.mode_id)
        WHERE t.dataset_id=:v AND from_poi_id=:f AND to_poi_id=:t ORDER BY t.leg_id""",
        v=v,f=args.from_poi_id,t=args.to_poi_id)
    for item in items:
        item.update(linked_summaries(conn,v,item['leg_id'],item['scenic_code']))
        item['evidence_refs']=[{'kind':'node','id':item['leg_id']}]
    items.sort(key=lambda x: (x.get('mode') not in args.preferred_modes,x['leg_id']))
    return {'items':items[:8],'historical_notice':HISTORICAL,
            'warnings':[] if items else ['P6 暂无该方向的路段记录，需确认交通衔接。']}
