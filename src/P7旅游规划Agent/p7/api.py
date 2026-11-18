import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import copy_context
from secrets import token_hex
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import agent, conversation as chat, itinerary, knowledge, models, sources
from .db import engine, execute, row, rows, setting
from .errors import BusinessError


@asynccontextmanager
async def lifespan(app):
    await run_in_threadpool(agent.recover)
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='p7-turn') as executor:
        app.state.executor=executor
        try:
            yield
        finally:
            executor.shutdown(wait=False,cancel_futures=True)


app=FastAPI(title='行迹 P7_1',lifespan=lifespan)
V='/api/v1'
SESSION_COOKIE='p7_browser_session'


@app.middleware('http')
async def browser_session(request,call_next):
    path=request.url.path
    if setting('P7_1_BROWSER_SESSIONS')!='1' or not path.startswith(
            (V+'/conversations',V+'/turns/',V+'/itineraries/')):
        return await call_next(request)

    session=request.cookies.get(SESSION_COOKIE,'')
    is_new=not re.fullmatch(r'[0-9a-f]{64}',session)
    if is_new:
        session=token_hex(32)
    token=chat.owner_key.set(session)
    try:
        response=await call_next(request)
    finally:
        chat.owner_key.reset(token)
    response.headers['Cache-Control']='no-store'
    if is_new:
        response.set_cookie(SESSION_COOKIE,session,max_age=365*24*60*60,path=V,
                            httponly=True,samesite='strict',secure=request.url.scheme=='https')
    return response


@app.exception_handler(BusinessError)
async def business_error(request,exc):
    return JSONResponse({'error':exc.payload()},status_code=exc.http_status)


@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    issues=[{'path':e['loc'],'message':e['msg']} for e in exc.errors()]
    return JSONResponse({'error':{'code':'INVALID_ARGUMENT','message':'请求参数无效','details':issues}},status_code=422)


@app.get(V+'/status')
def status():
    with engine().connect() as conn:
        try:
            v=knowledge.active_version(conn)
        except BusinessError:
            v=None
        key=bool(setting('DEEPSEEK_API_KEY'))
        return {'service_ready':bool(v and key),'model_configured':key,'model':agent.MODEL if key else None,
                'data_version':v,'catalog_ready':bool(v),'scenic_areas':knowledge.areas(conn,v) if v else []}


@app.post(V+'/conversations',status_code=201)
def new_conversation(body: models.NewConversation):
    with engine().begin() as conn:
        return chat.create(conn,body.title)


@app.get(V+'/conversations')
def conversations(cursor: UUID | None=None,limit: int=Query(20,ge=1,le=50)):
    with engine().connect() as conn:
        return chat.listing(conn,cursor,limit)


@app.get(V+'/conversations/{cid}')
def conversation(cid: UUID):
    with engine().connect() as conn:
        return chat.public_conv(conn,cid)


@app.get(V+'/conversations/{cid}/messages')
def messages(cid: UUID,before_seq: int | None=Query(None,ge=1),limit: int=Query(30,ge=1,le=100)):
    with engine().connect() as conn:
        return chat.message_list(conn,cid,before_seq,limit)


def poll(tid,after):
    with engine().connect() as conn:
        turn=chat.get_turn(conn,tid)
        data=rows(conn,'''SELECT seq,event_type,payload FROM p7_app.turn_event
                         WHERE turn_id=:id AND seq>:n ORDER BY seq LIMIT 100''',id=tid,n=after)
        return turn['status'],data


def stream(tid,after=0):
    async def events():
        wait=0
        cursor=after
        while True:
            state,items=await run_in_threadpool(poll,tid,cursor)
            for item in items:
                cursor=item['seq']
                data=json.dumps(item['payload'],ensure_ascii=False,separators=(',',':'))
                yield f"id: {cursor}\nevent: {item['event_type']}\ndata: {data}\n\n"
                if item['event_type']=='turn.ended':
                    return
            if state not in chat.ACTIVE and len(items)<100:
                return
            if items:
                wait=0
            else:
                wait+=1
                if wait%50==0:
                    yield ': keep-alive\n\n'
            await asyncio.sleep(0.3)
    return StreamingResponse(events(),media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@app.post(V+'/conversations/{cid}/turns')
def new_turn(cid: UUID,body: models.NewTurn,request: Request):
    with engine().begin() as conn:
        if not setting('DEEPSEEK_API_KEY'):
            raise BusinessError('MODEL_UNAVAILABLE','缺少 DEEPSEEK_API_KEY',503)
        tid=chat.admit(conn,cid,body)
        queued=chat.get_turn(conn,tid)['status']=='queued'
    if queued:
        request.app.state.executor.submit(copy_context().run,agent.run_turn,tid)
    return stream(tid)


@app.get(V+'/turns/{tid}/events')
def events(tid: UUID,after: int=Query(0,ge=0)):
    with engine().connect() as conn:
        chat.get_turn(conn,tid)
    return stream(tid,after)


@app.post(V+'/turns/{tid}/cancel')
def cancel(tid: UUID):
    with engine().begin() as conn:
        turn=chat.get_turn(conn,tid,True)
        if turn['status'] not in chat.ACTIVE:
            return {'status':turn['status']}
        execute(conn,'UPDATE p7_app.turn SET cancel_requested_at=COALESCE(cancel_requested_at,now()) WHERE id=:id',{'id':tid})
    return JSONResponse({'status':'cancel_requested'},status_code=202)


@app.get(V+'/knowledge/pois')
def pois(q: str='',data_version: str | None=None,scenic_code: models.Code | None=None,limit: int=Query(8,ge=1,le=20)):
    with engine().connect() as conn:
        v=knowledge.require_version(conn,data_version) if data_version else knowledge.active_version(conn)
        return knowledge.search_pois(conn,v,models.PoiSearch(scenic_code=scenic_code,keywords=[q] if q else [],limit=limit))


@app.get(V+'/knowledge/routes/{route_id}')
def route(route_id: str,data_version: str | None=None,day: int | None=Query(None,ge=1,le=30),cursor: int=Query(0,ge=0)):
    with engine().connect() as conn:
        v=knowledge.require_version(conn,data_version) if data_version else knowledge.active_version(conn)
        return knowledge.route_details(conn,v,models.RouteDetails(route_id=route_id,day=day,cursor=cursor))


@app.get(V+'/knowledge/entities/{entity_id}')
def entity(entity_id: str,data_version: str | None=None,route_id: str | None=None,step_id: str | None=None):
    with engine().connect() as conn:
        v=knowledge.require_version(conn,data_version) if data_version else knowledge.active_version(conn)
        return knowledge.entity(conn,v,entity_id,route_id,step_id)


@app.get(V+'/knowledge/sources')
def origin(data_version: str | None=None,ref_id: str | None=None,entity_id: str | None=None,
           route_id: str | None=None,step_id: str | None=None,cursor: int=Query(0,ge=0)):
    if not any((ref_id,entity_id,route_id,step_id)):
        raise BusinessError('INVALID_ARGUMENT','请提供来源引用')
    with engine().connect() as conn:
        v=knowledge.require_version(conn,data_version) if data_version else knowledge.active_version(conn)
        return sources.excerpts(conn,v,models.Sources(ref_id=ref_id,entity_id=entity_id,route_id=route_id,step_id=step_id,cursor=cursor))


def owner_of_itinerary(conn,iid):
    result=row(conn,'SELECT conversation_id FROM p7_app.itinerary WHERE id=:id',id=iid)
    if not result:
        raise BusinessError('NOT_FOUND','找不到行程',404)
    chat.get(conn,result['conversation_id'])
    return result['conversation_id']


@app.get(V+'/itineraries/{iid}')
def get_itinerary(iid: UUID,version: int | None=Query(None,ge=1)):
    with engine().connect() as conn:
        return itinerary.view(conn,iid,version,owner_of_itinerary(conn,iid))


@app.patch(V+'/itineraries/{iid}')
def edit_itinerary(iid: UUID,body: models.ManualEdit):
    with engine().begin() as conn:
        result=itinerary.update(conn,owner_of_itinerary(conn,iid),iid,body,f'http:{body.client_edit_id}')
        return {'itinerary':result,'changes':result['change_summary']}
