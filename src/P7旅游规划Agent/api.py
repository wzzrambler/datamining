# -*- coding: utf-8 -*-
"""项目 7 的 Web 接口：把 ReAct Agent 包成 HTTP 服务，前端在同目录的 web/ 下。

设计上只做三件事：把请求转成一次 Agent 运行、把运行过程按轮次推给前端、
把结果整理成前端能直接渲染的结构。规划逻辑全部留在 p7_agent.py，
这里不复制任何业务规则——否则改一处要改两份。

    python src/P7旅游规划Agent/serve.py           # 推荐：本机模式，自动选端口并开浏览器
    python src/P7旅游规划Agent/serve.py --public  # 公网模式，强制口令
    python src/P7旅游规划Agent/api.py             # 直接起 FastAPI：默认 127.0.0.1:8000
    python src/P7旅游规划Agent/api.py --port 9000

接口：
    GET  /api/health          服务状态、数据规模、是否配置了模型
    GET  /api/scenes          可选景区与各自的路线数
    GET  /api/examples        示例问句（前端做成快捷按钮）
    POST /api/plan            同步规划，返回完整结果
    GET  /api/plan/stream     SSE，逐轮推送思考与动作
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p7_agent as engine  # noqa: E402

VERSION = 'p7-web-1'
WEB_DIR = HERE / 'web'
LLM_READY = bool(os.environ.get('OPENAI_API_KEY'))
ACCESS_TOKEN = os.environ.get('P7_ACCESS_TOKEN', '').strip()
MAX_CONCURRENCY = int(os.environ.get('P7_MAX_CONCURRENCY', '4'))
RATE_LIMIT = int(os.environ.get('P7_RATE_LIMIT', '60'))
RATE_WINDOW = float(os.environ.get('P7_RATE_WINDOW', '60'))
REQUEST_TIMEOUT = float(os.environ.get('P7_REQUEST_TIMEOUT', '150'))

app = FastAPI(title='旅游规划 Agent', version=VERSION,
              description='基于项目 2/3/5/6 产物的 ReAct 规划服务')
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get('P7_CORS_ORIGINS', '*').split(',') if o.strip()],
    allow_methods=['GET', 'POST', 'OPTIONS'],
    allow_headers=['Content-Type'],
)

_slots = threading.BoundedSemaphore(MAX_CONCURRENCY)
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


class PlanRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    mode: Literal['rule', 'llm'] = 'rule'
    max_turns: int = Field(6, ge=1, le=10)


def _auth(request: Request):
    """可选口令：设了 P7_ACCESS_TOKEN 就必须带上。浏览器 EventSource 发不了
    自定义头，所以同时接受 ?token=。公网部署时务必设置。"""
    if not ACCESS_TOKEN:
        return
    supplied = (request.headers.get('authorization') or '').removeprefix('Bearer ').strip()
    if not supplied:
        supplied = (request.query_params.get('token') or '').strip()
    if supplied != ACCESS_TOKEN:
        raise HTTPException(401, '需要访问口令', headers={'WWW-Authenticate': 'Bearer'})


def _client_ip(request: Request) -> str:
    # 只信任一层反代：deploy/nginx-p7.conf 用 $remote_addr 覆盖该头，
    # 客户端自己伪造的值到不了这里。
    forwarded = request.headers.get('x-forwarded-for', '')
    return (forwarded.split(',')[0].strip() if forwarded else
            (request.client.host if request.client else 'unknown'))


def _check_rate(ip: str):
    now = time.monotonic()
    with _hits_lock:
        bucket = _hits[ip]
        while bucket and now - bucket[0] > RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            raise HTTPException(429, '请求过于频繁，请稍后再试',
                                headers={'Retry-After': str(int(RATE_WINDOW))})
        bucket.append(now)
        if len(_hits) > 4096:      # 防止被大量 IP 撑爆内存
            for key in [k for k, v in _hits.items() if not v][:1024]:
                _hits.pop(key, None)


def _clean_query(text: str) -> str:
    return ' '.join(text.split()).strip()


def _args_preview(action: str, args: dict) -> str:
    """轮次列表只展示参数要点。tailor_route 的参数里塞着整条路线，原样回传会有几十 KB。"""
    if action == 'tailor_route':
        return json.dumps({'visitor': args.get('visitor', ''),
                           'route': (args.get('route') or {}).get('route_id', '')},
                          ensure_ascii=False)
    text = json.dumps(args, ensure_ascii=False)
    return text if len(text) <= 120 else text[:117] + '...'


def _public_turn(turn: dict) -> dict:
    return {'turn': turn.get('turn'), 'thought': turn.get('thought', ''),
            'action': turn.get('action', ''),
            'args': _args_preview(turn.get('action', ''), turn.get('args') or {}),
            'observation': turn.get('observation', '')}


_KEYCAP = re.compile(r'^[0-9]\ufe0f?\u20e3\s*')


def _clean_steps(steps):
    """路线原文带 1⃣ 这类序号，前端自己会编号，展示时去掉，免得一行两个号。"""
    cleaned = []
    for step in steps:
        item = dict(step)
        spot = _KEYCAP.sub('', item.get('spot') or '').strip()
        # 原文常把时刻写进景点名（"09:30 开园直冲小火车"），卡片上还有一列时刻，重复
        if item.get('time') and spot.startswith(item['time']):
            spot = spot[len(item['time']):].lstrip(' :：-·')
        cleaned.append(dict(item, spot=spot))
    return cleaned


def _public_result(result: dict, elapsed_ms: int) -> dict:
    plan = result.get('plan') or {}
    steps = _clean_steps(plan.get('steps') or [])
    days = engine.split_days(steps, plan.get('days') or 1,
                             plan.get('span') or '') if steps else []
    return {
        'status': result.get('status', ''),
        'mode': result.get('mode', 'rule'),
        'answer': result.get('answer', ''),
        'elapsed_ms': elapsed_ms,
        'route_id': result.get('route_id', ''),
        'slots': result.get('slots') or {},
        'plan': {
            'area': plan.get('area', ''),
            'area_short': engine.SHORT.get(plan.get('area', ''), plan.get('area', '')),
            'visitor': plan.get('visitor', ''),
            'days': plan.get('days'),
            'days_actual': plan.get('days_actual'),
            'route_id': plan.get('route_id', ''),
            'span': plan.get('span', ''),
            'steps': steps,
            'day_groups': days,
            'tips': plan.get('tips') or [],
            'notes': plan.get('notes') or [],
        } if plan else None,
        'turns': [_public_turn(t) for t in result.get('turns') or []],
        'diagnostics': result.get('diagnostics') or [],
    }


def _run(query: str, mode: str, max_turns: int, on_event=None, should_stop=None) -> dict:
    agent = engine.Agent(query, use_llm=(mode == 'llm'), max_turns=max_turns,
                         on_event=on_event, should_stop=should_stop)
    started = time.perf_counter()
    result = agent.run()
    return _public_result(result, max(1, int((time.perf_counter() - started) * 1000)))


@app.get('/api/health')
def health(request: Request):
    _auth(request)
    return {'ok': True, 'version': VERSION, 'llm_configured': LLM_READY,
            'max_concurrency': MAX_CONCURRENCY,
            'counts': {'routes': len(engine.ROUTES), 'documents': len(engine.ENTS),
                       'entities': sum(len(d.get('entities') or []) for d in engine.ENTS),
                       'rules': len(engine.CONDS)}}


@app.get('/api/scenes')
def scenes(request: Request):
    _auth(request)
    rows = []
    for name in engine.AREAS:
        rows.append({'name': name, 'short': engine.SHORT.get(name, name),
                     'routes': sum(1 for r in engine.ROUTES if r['scenic_area'] == name)})
    return {'areas': rows}


@app.get('/api/examples')
def examples(request: Request):
    _auth(request)
    return {'examples': engine.SCENES}


@app.post('/api/plan')
def plan(payload: PlanRequest, request: Request):
    _auth(request)
    ip = _client_ip(request)
    _check_rate(ip)
    query = _clean_query(payload.query)
    if len(query) < 2:
        raise HTTPException(422, '请把问题写清楚一点')
    if payload.mode == 'llm' and not LLM_READY:
        raise HTTPException(409, '服务端没有配置模型密钥，请改用规则模式')
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '当前并发已满，请稍后重试')
    try:
        return _run(query, payload.mode, payload.max_turns)
    except Exception:                       # 不把内部堆栈和路径抛给浏览器
        raise HTTPException(500, '规划过程出错，请稍后重试或换规则模式')
    finally:
        _slots.release()


@app.get('/api/plan/stream')
async def plan_stream(request: Request, query: str, mode: Literal['rule', 'llm'] = 'rule',
                      max_turns: int = 6):
    """SSE：先推 meta，再逐轮推 turn，最后推 result。

    浏览器只能用 EventSource 发 GET，所以参数走 query string。客户端断开时
    置取消标记，Agent 在下一轮开当前收尾，不再为已离开的用户烧模型调用。
    """
    _auth(request)
    ip = _client_ip(request)
    _check_rate(ip)
    text = _clean_query(query)
    if len(text) < 2:
        raise HTTPException(422, '请把问题写清楚一点')
    if mode == 'llm' and not LLM_READY:
        raise HTTPException(409, '服务端没有配置模型密钥，请改用规则模式')
    if max_turns < 1 or max_turns > 10:
        raise HTTPException(422, 'max_turns 取值 1 到 10')
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, '当前并发已满，请稍后重试')

    async def generate():
        events: queue.Queue = queue.Queue()
        cancelled = threading.Event()

        def emit(event):
            events.put(event)

        def work():
            try:
                result = _run(text, mode, max_turns, on_event=emit,
                              should_stop=cancelled.is_set)
                events.put({'kind': 'result', 'data': result})
            except Exception:
                events.put({'kind': 'failure', 'data': {'message': '规划过程出错，请稍后重试'}})
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()
        started = time.monotonic()
        try:
            yield 'event: meta\ndata: %s\n\n' % json.dumps(
                {'mode': mode, 'query': text, 'llm_configured': LLM_READY}, ensure_ascii=False)
            while True:
                if await request.is_disconnected():
                    cancelled.set()
                try:
                    event = events.get(timeout=1.0)
                except queue.Empty:
                    if time.monotonic() - started > REQUEST_TIMEOUT:
                        cancelled.set()
                        yield 'event: failure\ndata: {"message":"规划超时"}\n\n'
                        return
                    continue
                if event is None:
                    return
                yield 'event: %s\ndata: %s\n\n' % (
                    event['kind'], json.dumps(event['data'], ensure_ascii=False))
        finally:
            _slots.release()

    return StreamingResponse(generate(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


if WEB_DIR.is_dir():
    app.mount('/', StaticFiles(directory=str(WEB_DIR), html=True), name='web')


def main():
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=os.environ.get('P7_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('P7_PORT', '8000')))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
