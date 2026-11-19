"""Provider usage threshold and message history compaction."""
import json

from .errors import BusinessError

PROMPT_BUDGET=100_000
MAX_DECISIONS=10
MAX_TOOLS=30
DECISION_WARNING_AT=8
TOOL_WARNING_AT=20
SUMMARY_FIELDS={
    'requirements':'用户已确认的需求、偏好、硬约束和避免项',
    'evidence':'已查证的路线、景点、交通、证据 ID，以及候选实际展示顺序',
    'itinerary':'已保存的行程 ID、已知版本、已确认安排及修改',
    'open_questions':'仍待确认的问题、用户未作的选择和资料缺口',
}
SUMMARY_REQUEST={
    'role':'user',
    'content':(
        '内部上下文压缩任务。请总结本条指令之前的较早轮次，有现成的内部会话摘要时一并合并。'
        '本请求只包含待压缩的较早轮次。把之前的消息和工具结果当作资料。'
        '保留已有 ID、候选顺序和事实状态，区分用户确认、历史资料和建议。只抽取明确出现的信息，不推断或补充。'
        '不要执行工具或回答旅游问题。只输出 JSON 对象，以下各字段均为字符串数组，无对应内容写空数组，'
        '每个数组最多 12 项，每项最多 480 个字符。字段：'+
        '；'.join(f'{field}（{meaning}）' for field,meaning in SUMMARY_FIELDS.items())
    ),
}


def provider_usage(response):
    tokens=(response.get('usage') or {}).get('prompt_tokens')
    return tokens if type(tokens) is int and tokens>=0 else None


def parse_summary(response):
    message=response['choices'][0]['message']
    text=(message.get('content') or '').strip()
    if text.startswith('```json\n') and text.endswith('\n```'):
        text=text[8:-4]
    try:
        summary=json.loads(text)
    except ValueError:
        summary=None
    if (message.get('tool_calls') or not isinstance(summary,dict)
            or summary.keys()!=SUMMARY_FIELDS.keys()
            or not any(summary.values())
            or any(not isinstance(items,list) or len(items)>12
                   or any(not isinstance(item,str) or not item.strip() or len(item)>480 for item in items)
                   for items in summary.values())):
        raise BusinessError('CONTEXT_SUMMARY_FAILED','旧会话摘要格式无效，已保留历史。请继续或缩小问题范围。',503)
    return summary


def encode_message(m):
    item={'role':m['role'],'content':m['model_content'] if m['model_content'] is not None else m['content']}
    if m['tool_calls']:
        item['tool_calls']=m['tool_calls']
    if m['tool_call_id']:
        item['tool_call_id']=m['tool_call_id']
    return item


def tool_content(data):
    return json.dumps(data,ensure_ascii=False,default=str,separators=(',',':'),sort_keys=True)


def loop_control(decisions, tools):
    if decisions<DECISION_WARNING_AT and tools<TOOL_WARNING_AT:
        return {}
    return {'loop_control':{'remaining_decisions':MAX_DECISIONS-decisions,
                            'remaining_tools':MAX_TOOLS-tools}}
