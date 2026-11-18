"""Public descriptions of tool activity used by live and historical chat."""
import json

TOOL_COPY = {
    'search_routes': ('检索 P6 路线', '按景区、天数和偏好缩小候选范围。'),
    'get_route_details': ('查看路线步骤', '核对路线顺序以及步骤关联的景点和摘要。'),
    'search_pois': ('查找景点', '把地点名称或主题对应到 P6 中的标准景点。'),
    'get_poi_details': ('阅读景点摘要', '查看景点相关的时间、建议和适用条件。'),
    'get_transport_links': ('核对交通衔接', '查找两个景点之间有证据的交通连接。'),
    'lookup_sources': ('回查资料出处', '沿景点、路线或摘要查找可核对的原文出处。'),
    'view_itinerary': ('读取行程', '核对行程版本及本次要查看或修改的项目。'),
    'ask_user': ('补充行程需求', '先确认影响路线选择的条件，再继续规划。'),
    'submit_itinerary': ('保存行程', '校验景点与来源，并保存按天编排的行程。'),
    'update_itinerary': ('更新行程', '按已有版本定位修改项，并保存修改结果。'),
}

PUBLIC_CODES = {'JZG': '九寨沟', 'GZCL': '广州长隆', 'ZHCL': '珠海长隆'}


def describe(name):
    return TOOL_COPY.get(name, ('工具调用', '运行所选工具并检查返回状态。'))


def started(assistant_id, call):
    name = call['function']['name']
    title, intent = describe(name)
    try:
        args = json.loads(call['function']['arguments'])
    except (TypeError, ValueError):
        args = {}
    scope = []
    if isinstance(args, dict):
        codes = args.get('scenic_codes') or [args.get('scenic_code')]
        if isinstance(codes, list):
            scope.extend(PUBLIC_CODES[c] for c in codes if isinstance(c, str) and c in PUBLIC_CODES)
        days = args.get('days', args.get('day'))
        if type(days) is int and 1 <= days <= 30:
            scope.append(f'{days} 天' if name == 'search_routes' else f'第 {days} 天')
    return {'activity_id': f"{assistant_id}:{call['id']}", 'tool': name, 'title': title,
            'intent': intent, 'scope': ' · '.join(dict.fromkeys(scope))}


def result(assistant_id, call, response):
    payload = started(assistant_id, call)
    data = response.get('data') or {}
    status = response['status']
    count = next((len(data[key]) for key in ('items', 'steps', 'sources') if isinstance(data.get(key), list)), None)
    if type(data.get('count')) is int:
        count = data['count']
    message = (response.get('error') or {}).get('message')
    if not message and status == 'no_data':
        message = '没有找到符合本次条件的记录。'
    return {**payload, 'tool_call_id': call['id'], 'stage': status, 'label': message or payload['title'],
            'result_count': count}


def public_activity(event):
    data = event['payload']
    title, intent = describe(data.get('tool'))
    # Only known public fields leave this endpoint, including for legacy events.
    return {'turn_id': event['turn_id'], 'seq': event['seq'], 'event_type': event['event_type'],
            'payload': {'tool': data.get('tool'), 'activity_id': data.get('activity_id'),
                        'title': data.get('title') or title, 'intent': data.get('intent') or intent,
                        'scope': data.get('scope'), 'stage': data.get('stage'), 'label': data.get('label'),
                        'result_count': data.get('result_count')}}
