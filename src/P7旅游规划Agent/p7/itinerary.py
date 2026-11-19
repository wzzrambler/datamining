"""Validated immutable itinerary versions."""
import hashlib
import json
from datetime import date
from uuid import uuid4

from .db import execute, row, rows
from .errors import BusinessError
from .knowledge import catalog_node


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def view(conn,iid,version=None,conversation_id=None,day_id=None,type=None,route_id=None,entity_id=None):
    root=row(conn,'SELECT * FROM p7_app.itinerary WHERE id=:id',id=iid)
    if not root or (conversation_id and root['conversation_id']!=conversation_id):
        raise BusinessError('NOT_FOUND','找不到该行程',404)
    version=version or root['current_version']
    record=row(conn,'SELECT * FROM p7_app.itinerary_version WHERE itinerary_id=:id AND version=:v',id=iid,v=version)
    if not record:
        raise BusinessError('NOT_FOUND','找不到该行程版本',404)
    result={**record,'conversation_id':root['conversation_id'],'current_version':root['current_version']}
    days=rows(conn,'''SELECT day_id,day_no,date,label,schedule_basis FROM p7_app.itinerary_day
                      WHERE itinerary_id=:id AND version=:v ORDER BY day_no''',id=iid,v=version)
    items=rows(conn,'''SELECT item_id,day_id,position AS "order",type,i.poi_id,i.leg_id,route_id,step_id,
                             recommendation_reason,fields,evidence_refs,
                             COALESCE(p.name,l.service_name,i.fields->>'text') AS name
                      FROM p7_app.itinerary_item i LEFT JOIN p6_kg.poi p
                      ON (p.dataset_id,p.poi_id)=(i.data_version,i.poi_id)
                      LEFT JOIN p6_kg.transport_leg l ON (l.dataset_id,l.leg_id)=(i.data_version,i.leg_id)
                      WHERE i.itinerary_id=:id AND i.version=:v ORDER BY i.position''',id=iid,v=version)
    for day in days:
        day['items']=[i for i in items if i['day_id']==day['day_id'] and (not type or i['type']==type)
                      and (not route_id or i['route_id']==route_id) and (not entity_id or entity_id in (i['poi_id'],i['leg_id']))]
    result['days']=[d for d in days if not day_id or d['day_id']==day_id]
    return result


def receipt(conn,cid,key,body):
    existing=row(conn,'''SELECT request_hash,itinerary_id,result_version
                          FROM p7_app.itinerary_write_receipt WHERE conversation_id=:c AND operation_key=:k''',c=cid,k=key)
    if existing:
        if existing['request_hash']!=digest(body):
            raise BusinessError('REQUEST_ID_REUSED','编辑标识已用于不同内容',409)
        return view(conn, existing['itinerary_id'],existing['result_version'],cid)


def validate(conn,v,plan,constraints=None):
    constraints=constraints or {}
    days=plan['days']
    if constraints.get('days') and len(days)!=constraints['days']:
        raise BusinessError('UNCONFIRMED_CONSTRAINT','行程天数与已确认需求不一致',days=constraints['days'])
    all_pois=set()
    for day in days:
        if day['date']:
            try:
                day['date']=date.fromisoformat(str(day['date']))
            except ValueError:
                raise BusinessError('INVALID_ARGUMENT','日程日期无效')
        last_poi=None
        between=[]
        for item in day['items']:
            poi,leg,route,step=(item.get(k) for k in ('poi_id','leg_id','route_id','step_id'))
            if poi:
                info=catalog_node(conn,v,poi)
                if not info or info['label']!='POI':
                    raise BusinessError('INVALID_PLAN_REFERENCE','行程含未知景点',poi_id=poi)
                all_pois.add(poi)
                if constraints.get('scenic_codes') and info['properties']['scenic_code'] not in constraints['scenic_codes']:
                    raise BusinessError('UNCONFIRMED_CONSTRAINT','景点不属于已确认的景区',poi_id=poi)
                if route:
                    route_info=catalog_node(conn,v,route)
                    match=row(conn,'''SELECT 1 FROM p6_kg.route_step s JOIN p6_kg.step_poi p USING(dataset_id,step_id)
                                      WHERE s.dataset_id=:v AND s.route_id=:r AND p.poi_id=:p
                                        AND (CAST(:s AS text) IS NULL OR s.step_id=:s)''',v=v,r=route,p=poi,s=step)
                    if not route_info or not match:
                        raise BusinessError('INVALID_PLAN_REFERENCE','景点与来源路线或步骤不匹配',poi_id=poi)
                if not (item.get('evidence_refs') or route):
                    raise BusinessError('INVALID_PLAN_REFERENCE','景点推荐缺少证据引用',poi_id=poi)
                if last_poi and last_poi!=poi and not row(conn,'''SELECT 1 FROM p6_kg.transport_leg
                         WHERE dataset_id=:v AND from_poi_id=:f AND to_poi_id=:t''', v=v,f=last_poi,t=poi):
                    if not between:
                        plan['warnings'].append(f'第 {day["day_no"]} 天有景点间交通衔接需确认。')
                if between and (not last_poi or between[0]['from_poi_id'] != last_poi or between[-1]['to_poi_id'] != poi
                                or any(a['to_poi_id']!=b['from_poi_id'] for a,b in zip(between,between[1:]))):
                    raise BusinessError('INVALID_PLAN_REFERENCE','交通段与前后到访的景点不匹配')
                between=[]
                last_poi=poi
            if leg:
                segment=row(conn,'SELECT * FROM p6_kg.transport_leg WHERE dataset_id=:v AND leg_id=:k',v=v,k=leg)
                if not segment:
                    raise BusinessError('INVALID_PLAN_REFERENCE','行程含未知交通段',leg_id=leg)
                between.append(segment)
            for ref in item.get('evidence_refs',[]):
                table,column={'node':('node','node_id'),'theme':('advice_theme','theme_id'),'document':('source_document','doc_id')}[ref['kind']]
                if not row(conn,f'SELECT 1 FROM p6_kg.{table} WHERE dataset_id=:v AND {column}=:k',v=v,k=ref['id']):
                    raise BusinessError('INVALID_PLAN_REFERENCE','证据引用不存在',id=ref['id'])
    missing=set(constraints.get('must_visit_poi_ids',[]))-all_pois
    forbidden=set(constraints.get('avoid_poi_ids',[]))&all_pois
    if missing or forbidden:
        raise BusinessError('UNCONFIRMED_CONSTRAINT','行程与已确认的必去或避开项冲突',
                            missing=sorted(missing),forbidden=sorted(forbidden))
    if not all_pois:
        raise BusinessError('INVALID_ARGUMENT','行程至少包含一次景点到访')
    plan['warnings']=list(dict.fromkeys(plan['warnings']))
    if plan['warnings'] or plan['unresolved_items']:
        plan['status']='partial'


def save_version(conn,cid,iid,v,version,plan,key,body,turn_id=None):
    execute(conn,'''INSERT INTO p7_app.itinerary_version
        (itinerary_id,version,data_version,parent_version,created_by_turn_id,title,status,assumptions,warnings,unresolved_items,change_summary)
        VALUES (:id,:version,:v,:parent,:turn,:title,:status,:assumptions,:warnings,:unresolved_items,:changes)''',
        dict(plan,id=iid,version=version,v=v,parent=version-1 if version>1 else None,turn=turn_id),
        'assumptions','warnings','unresolved_items','changes')
    for day in plan['days']:
        execute(conn,'''INSERT INTO p7_app.itinerary_day(itinerary_id,version,day_id,day_no,date,label,schedule_basis)
                        VALUES(:id,:version,:day_id,:day_no,:date,:label,:schedule_basis)''',dict(day,id=iid,version=version))
        for position,item in enumerate(day['items'],1):
            execute(conn,'''INSERT INTO p7_app.itinerary_item
                (itinerary_id,version,item_id,day_id,position,type,data_version,poi_id,leg_id,route_id,step_id,
                 recommendation_reason,fields,evidence_refs)
                VALUES(:id,:version,:item_id,:day,:pos,:type,:v,:poi_id,:leg_id,:route_id,:step_id,
                       :recommendation_reason,:fields,:evidence_refs)''',
                dict(item,id=iid,version=version,day=day['day_id'],pos=position,v=v),'fields','evidence_refs')
    execute(conn,'UPDATE p7_app.itinerary SET current_version=:n,updated_at=now() WHERE id=:id',{'n':version,'id':iid})
    execute(conn,'UPDATE p7_app.conversation SET active_itinerary_id=:id,updated_at=now() WHERE id=:c',{'id':iid,'c':cid})
    execute(conn,'''INSERT INTO p7_app.itinerary_write_receipt VALUES(:c,:k,:h,:id,:n,now())''',
            {'c':cid,'k':key,'h':digest(body),'id':iid,'n':version})
    return view(conn,iid,version,cid)


def lock_conversation(conn,cid):
    conv=row(conn,'SELECT id,data_version,state FROM p7_app.conversation WHERE id=:id FOR UPDATE',id=cid)
    if not conv:
        raise BusinessError('NOT_FOUND','会话不存在',404)
    return conv


def submit(conn,cid,args,key,turn_id=None):
    conv=lock_conversation(conn,cid)
    body=args.model_dump(mode='json')
    if result:=receipt(conn,cid,key,body):
        return result
    plan=args.model_dump()
    for n,day in enumerate(plan['days'],1):
        day.update(day_id=uuid4(),day_no=n)
        for item in day['items']:
            item['item_id']=uuid4()
    plan['changes']=[{'op':'create','message':'已创建行程'}]
    v=conv['data_version']
    validate(conn,v,plan,conv['state'].get('constraints'))
    iid=uuid4()
    execute(conn,'''INSERT INTO p7_app.itinerary(id,conversation_id,data_version,current_version)
                    VALUES(:id,:c,:v,1)''',{'id':iid,'c':cid,'v':v})
    return save_version(conn,cid,iid,v,1,plan,key,body,turn_id)


def locate(plan,target):
    matches=[]
    for day in plan['days']:
        if target.day_id and target.day_id!=day['day_id']:
            continue
        for index,item in enumerate(day['items']):
            if target.item_id and item['item_id']!=target.item_id:
                continue
            if target.route_id and item['route_id']!=target.route_id:
                continue
            if target.entity_id and target.entity_id not in (item['poi_id'],item['leg_id']):
                continue
            matches.append((day,index,item))
    if len(matches)!=1:
        code='AMBIGUOUS_TARGET' if matches else 'NOT_FOUND'
        raise BusinessError(code,'请选择唯一的行程项目' if matches else '找不到项目',409 if matches else 404,
                            candidates=[{'day_no':d['day_no'],'position':i+1,'item_id':str(x['item_id']),'name':x.get('name')} for d,i,x in matches])
    return matches[0]


def apply(plan,op):
    if op.op=='insert':
        day=next((d for d in plan['days'] if d['day_id']==op.day_id),None)
        if not day:
            raise BusinessError('NOT_FOUND','找不到目标日程',404)
        items=day['items']
        anchor=op.anchor
        ref=anchor.before_item_id or anchor.after_item_id
        i=next((n for n,x in enumerate(items) if x['item_id']==ref),None)
        if not (anchor.day_start and not items) and i is None:
            raise BusinessError('INVALID_ARGUMENT','插入锚点不在目标日程')
        item=dict(op.item.model_dump(),item_id=uuid4())
        items.insert(0 if anchor.day_start else i+int(bool(anchor.after_item_id)),item)
    else:
        day,index,item=locate(plan,op.target)
        if op.op=='delete':
            day['items'].pop(index)
        else:
            allowed={'poi_visit':{'recommendation_reason','optional','planned_time'},
                     'transport_leg':{'optional','planned_time'},
                     'note':{'text'}}[item['type']]
            if set(op.fields)-allowed:
                raise BusinessError('INVALID_ARGUMENT','存在不允许修改的字段',fields=sorted(set(op.fields)-allowed))
            for name,value in op.fields.items():
                if (name=='optional' and not isinstance(value,bool)) or (name!='optional' and not isinstance(value,str)):
                    raise BusinessError('INVALID_ARGUMENT','字段值类型错误',field=name)
                if name=='recommendation_reason':
                    if not value.strip():
                        raise BusinessError('INVALID_ARGUMENT','推荐理由不能为空')
                    item[name]=value
                else:
                    item['fields'][name]=value
    return {'op':op.op,'item_id':str(item['item_id']),'day_no':day['day_no'],
            'name':item.get('name') or item.get('poi_id') or item.get('leg_id') or item['type']}


def update(conn,cid,iid,args,key,turn_id=None):
    conv=lock_conversation(conn,cid)
    body=args.model_dump(mode='json',exclude={'itinerary_id','client_edit_id'})
    body['itinerary_id']=str(iid)
    if result:=receipt(conn,cid,key,body):
        return result
    root=row(conn,'SELECT current_version FROM p7_app.itinerary WHERE id=:id AND conversation_id=:c FOR UPDATE',id=iid,c=cid)
    if not root:
        raise BusinessError('NOT_FOUND','找不到该行程',404)
    current=root['current_version']
    if args.base_version!=current:
        raise BusinessError('VERSION_CONFLICT','行程已更新，请先读取最新版本',409,current_version=current)
    plan=view(conn,iid,current,cid)
    plan['changes']=[apply(plan,op) for op in args.operations]
    plan['warnings']=[w for w in plan['warnings'] if not w.endswith('有景点间交通衔接需确认。')]
    validate(conn,conv['data_version'],plan,conv['state'].get('constraints'))
    return save_version(conn,cid,iid,conv['data_version'],current+1,plan,key,body,turn_id)
