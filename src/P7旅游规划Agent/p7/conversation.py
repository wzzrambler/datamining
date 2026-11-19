import json
import re
from contextvars import ContextVar
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from .db import execute, row, rows
from .trace import public_activity
from .errors import BusinessError
from .itinerary import digest
from .knowledge import active_version, catalog_node
from .usage import tool_content

ACTIVE = ('queued','running')
owner_key = ContextVar('owner_key', default='local')


def get(conn,cid,lock=False):
    conv=row(conn, 'SELECT * FROM p7_app.conversation WHERE id=:id AND owner_key=:o' +
             (' FOR UPDATE' if lock else ''),id=cid,o=owner_key.get())
    if not conv:
        raise BusinessError('NOT_FOUND','会话不存在',404)
    return conv


def create(conn,title):
    value={'id':uuid4(),'owner':owner_key.get(),'title':title or '新会话','v':active_version(conn)}
    return dict(execute(conn,'''INSERT INTO p7_app.conversation(id,owner_key,title,data_version)
                                 VALUES(:id,:owner,:title,:v) RETURNING id,title,data_version,created_at,updated_at''',value).mappings().one())


def listing(conn,cursor=None,limit=20):
    cursor_from = get(conn,cursor) if cursor else None
    sql = '''SELECT id,title,data_version,created_at,updated_at,active_itinerary_id
             FROM p7_app.conversation WHERE owner_key=:o'''
    data=rows(conn, sql+(' AND (updated_at,id)<(:t,:id)' if cursor_from else '')+
              ' ORDER BY updated_at DESC,id DESC LIMIT :n', o=owner_key.get(),n=limit+1,
              t=(cursor_from or {}).get('updated_at'),id=cursor)
    return {'items':data[:limit], 'next_cursor':data[limit-1]['id'] if len(data)>limit else None}


def public_conv(conn,cid):
    conv=get(conn,cid)
    hold=conv['pending_question_message_id']
    return {k:conv[k] for k in ('id','title','data_version','created_at','updated_at')} | {
        'pending_question': row(conn,'SELECT id,content,attachments FROM p7_app.message WHERE id=:id',id=hold) if hold else None,
        'active_turn': row(conn,"SELECT id,status FROM p7_app.turn WHERE conversation_id=:c AND status IN ('queued','running')",c=cid),
        'active_itinerary':row(conn,'SELECT id AS itinerary_id,current_version AS version FROM p7_app.itinerary WHERE id=:id',
                               id=conv['active_itinerary_id']) if conv['active_itinerary_id'] else None
    }


def message_list(conn,cid,before=None,limit=30):
    conv=get(conn,cid)
    items=rows(conn,'''SELECT id AS message_id,turn_id,seq,role,content AS markdown,attachments FROM p7_app.message
          WHERE conversation_id=:c AND is_public'''+(' AND seq<:b' if before is not None else '')+
          ' ORDER BY seq DESC LIMIT :n',c=cid,b=before,n=limit+1)
    page=items[:limit]
    turn_ids=list(dict.fromkeys(item['turn_id'] for item in page))
    activity=rows(conn,'''SELECT turn_id,seq,event_type,payload FROM p7_app.turn_event
                          WHERE turn_id=ANY(:ids) AND event_type IN ('tool.started','tool.progress')
                          ORDER BY turn_id,seq''',ids=turn_ids) if turn_ids else []
    return {'items':[dict(x,data_version=conv['data_version']) for x in reversed(page)],
            'activities':[public_activity(e) for e in activity],
            'next_before_seq':items[limit-1]['seq'] if len(items)>limit else None}


def add_message(conn,cid,tid,role,content=None,tool_calls=None,tool_call_id=None,parent=None,
                public=False,attachments=None,message_id=None,model_content=None):
    # All message writers hold the conversation row lock.
    return dict(execute(conn,'''INSERT INTO p7_app.message
        (id,conversation_id,turn_id,seq,role,content,tool_calls,tool_call_id,parent_assistant_message_id,is_public,attachments,model_content)
        SELECT :id,:c,:t,COALESCE(MAX(seq),0)+1,:r,:content,:calls,:call,:parent,:public,:attachments,:model_content
          FROM p7_app.message WHERE conversation_id=:c RETURNING id,seq''',
        dict(id=message_id or uuid4(),c=cid,t=tid,r=role,content=content,calls=tool_calls,
             call=tool_call_id,parent=parent,public=public,attachments=jsonable_encoder(attachments or []),
             model_content=model_content),
        'calls','attachments').mappings().one())


def event(conn,tid,kind,payload):
    return execute(conn,'''INSERT INTO p7_app.turn_event(turn_id,seq,event_type,payload)
            SELECT :id,COALESCE(MAX(seq),0)+1,:kind,:data FROM p7_app.turn_event WHERE turn_id=:id''',
            dict(id=tid,kind=kind,data=jsonable_encoder(payload)),'data')


def get_turn(conn,tid,lock=False):
    found=row(conn,'''SELECT t.* FROM p7_app.turn t JOIN p7_app.conversation c ON c.id=t.conversation_id
                      WHERE t.id=:id AND c.owner_key=:o'''+(' FOR UPDATE OF t' if lock else ''),id=tid,o=owner_key.get())
    if not found:
        raise BusinessError('NOT_FOUND','回合不存在',404)
    return found


def update_constraints(conn,v,old,text):
    explicit=re.findall(r'((?:[1-9]|[12]\d|30)|[一二两三四五六七八九十])\s*(?:天|日)(?!前|后|的)',text)
    if explicit:
        n=explicit[-1]
        if not re.search(r'第\s*'+re.escape(n)+r'\s*(?:天|日)',text):
            old['days']=int(n) if n.isdigit() else {'两':2}.get(n,'一二三四五六七八九十'.find(n)+1)
    locations=[]
    for a in rows(conn,'SELECT scenic_code,name,aliases FROM p6_kg.scenic_area WHERE dataset_id=:v',v=v):
        if any(x in text for x in [a['name'],*a['aliases']]):
            locations.append(a['scenic_code'])
    if locations and not re.search(r'对比|还是|不去|不要去|不想去|哪些|分别',text):
        if set(locations)!=set(old.get('scenic_codes',[])):
            old.pop('must_visit_poi_ids',None)
            old.pop('avoid_poi_ids',None)
        old['scenic_codes']=locations
    for name,key in [('必去','must_visit_poi_ids'),('一定要去','must_visit_poi_ids'),('不要去','avoid_poi_ids'),('避开','avoid_poi_ids')]:
        for piece in text.split(name)[1:]:
            part=re.split(r'[，。；！？,]',piece,1)[0].strip()
            match=rows(conn,'SELECT poi_id FROM p6_kg.poi WHERE dataset_id=:v AND name=:n',v=v,n=part)
            if len(match)==1:
                poi=match[0]['poi_id']
                if poi not in old.setdefault(key,[]):
                    old[key].append(poi)
                opposite='avoid_poi_ids' if key=='must_visit_poi_ids' else 'must_visit_poi_ids'
                old[opposite]=[p for p in old.get(opposite,[]) if p!=poi]
    return old


def admit(conn,cid,request):
    conv=get(conn,cid,True)
    body=request.model_dump(mode='json')
    fingerprint=digest(body)
    prior=row(conn,'''SELECT id,request_hash FROM p7_app.turn
               WHERE conversation_id=:c AND client_message_id=:m''',c=cid,m=request.client_message_id)
    if prior:
        if fingerprint!=prior['request_hash']:
            raise BusinessError('REQUEST_ID_REUSED','消息 ID 已用于不同内容',409)
        return prior['id']
    if row(conn,"SELECT 1 FROM p7_app.turn WHERE conversation_id=:c AND status IN ('queued','running')",c=cid):
        raise BusinessError('TURN_ACTIVE','该会话已有执行中的回合',409)
    if not request.text.strip():
        raise BusinessError('INVALID_ARGUMENT','请输入问题')
    if request.reply_to and request.reply_to!=conv['pending_question_message_id']:
        raise BusinessError('STALE_QUESTION','该问题已过期，请刷新会话',409)
    ref=request.target_ref
    if ref:
        check = catalog_node(conn,conv['data_version'],ref.id) if ref.type!='item' else row(
            conn,'''SELECT 1 FROM p7_app.itinerary_item x JOIN p7_app.itinerary i ON i.id=x.itinerary_id
             WHERE i.conversation_id=:c AND x.item_id::text=:id AND x.version=i.current_version''',c=cid,id=ref.id)
        if not check or (ref.type=='route' and check.get('label')!='ROUTE') or (
            ref.type=='entity' and check.get('label') not in ('POI','TRANSPORT','TRANSPORT_LEG')
        ):
            raise BusinessError('NOT_FOUND','引用对象不存在',404)
    tid,mid=uuid4(),uuid4()
    execute(conn,'''INSERT INTO p7_app.turn(id,conversation_id,client_message_id,request_hash,input_message_id,status)
                   VALUES(:id,:c,:client,:h,:m,'queued')''',dict(id=tid,c=cid,client=request.client_message_id,h=fingerprint,m=mid))
    state=conv['state']
    state['constraints']=update_constraints(conn,conv['data_version'],state.get('constraints',{}),request.text)
    if ref:
        state['target_ref']=ref.model_dump()
    else:
        state.pop('target_ref',None)
    current=public_conv(conn,cid)
    snapshot={'constraints':state['constraints'],'displayed_routes':state.get('displayed_routes'),
              'target_ref':state.get('target_ref'),'reply_to':request.reply_to,
              'active_itinerary':current['active_itinerary'],'pending_question':current['pending_question']}
    snapshot={key:value for key,value in snapshot.items() if value}
    text=request.text.strip()
    model_content=text+'\n\n本轮会话资料：'+tool_content(snapshot) if snapshot else None
    add_message(conn,cid,tid,'user',text,public=True,message_id=mid,model_content=model_content,
                attachments=[{'type':'target','target':ref.model_dump()}] if ref else [])
    execute(conn,'''UPDATE p7_app.conversation SET updated_at=now(),state=:s,
             title=CASE WHEN title='新会话' THEN :title ELSE title END WHERE id=:id''',
            dict(s=state,title=request.text.strip()[:36],id=cid),'s')
    event(conn,tid,'turn.started',{'turn_id':tid,'user_message_id':mid,'data_version':conv['data_version']})
    return tid
