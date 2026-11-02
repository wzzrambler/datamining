"""Conservative structural event spans, never annotation-derived."""
import re

ACTION = re.compile(r'乘坐|游览|参观|购买|购票|坐|去|走|看|玩|排队|预约|换乘|入园|出发|到达|进入|选择|建议|推荐|需要|可以|不用|不能|不要|必须|带|喂|拍|吃|住|绕|省|租|接驳')
ROUTE = re.compile(r'→|—>|->|➜|➡|(?:^|\s)[①②③④⑤⑥]|(?:^|\s)[1-9][、.)．]')
SEQUENCE = re.compile(r'先.{1,80}(?:再|然后)|(?:之后|接着|最后)')


def spans(paragraph, tokens, max_chars=600):
    text, offset = paragraph['text'], paragraph['start']
    result = {}

    def add(a, b, shape, evidence):
        while a < b and text[a].isspace():
            a += 1
        while b > a and (text[b-1].isspace() or text[b-1] in '。！？!?；;，,'):
            b -= 1
        if b-a < 2 or b-a > max_chars:
            return
        key = a+offset, b+offset
        if key not in result or shape in {'sequence', 'route'}:
            result[key] = {'start': key[0], 'end': key[1], 'shape': shape, 'rule': evidence}

    for sent in re.finditer(r'[^。！？!?；;\n]+', text):
        value = sent.group()
        predicates = [t for t in tokens if offset+sent.start() <= t['start'] < offset+sent.end()
                      and t['pos'] in {'v', 'vn', 'vd', 'vg'}]
        eventful = ACTION.search(value) or predicates
        if not eventful and not ROUTE.search(value):
            continue
        shape = 'route' if ROUTE.search(value) else 'sequence' if SEQUENCE.search(value) else 'clause'
        add(sent.start(), sent.end(), shape, 'STRUCTURAL_'+shape.upper())
        for clause in re.finditer(r'[^，,：:]+', value):
            body = clause.group()
            if ACTION.search(body) and len(body) >= 4:
                add(sent.start()+clause.start(), sent.start()+clause.end(), 'action', 'ACTION_CLAUSE')
        # Explicit source-complement after 说/建议/推荐 is an event in its own right.
        for complement in re.finditer(r'(?:说|建议|推荐)([^，,。！？]{3,})', value):
            if ACTION.search(complement.group(1)):
                add(sent.start()+complement.start(1), sent.start()+complement.end(1),
                    'route' if re.search(r'从.+进.+出',complement.group(1)) else 'action', 'VERBAL_COMPLEMENT')
        # A comma-linked plan can start after an introduction; keep its contiguous suffix intact.
        parts=list(re.finditer(r'[^，,]+',value))
        if len(parts)>1:
            for index,part in enumerate(parts[:-1]):
                if ACTION.search(part.group()) and ACTION.search(parts[index+1].group()):
                    add(sent.start()+part.start(),sent.end(),'sequence','CONNECTED_ACTION_SEQUENCE')
        # Standalone action nouns are useful, but remain a separate granularity.
        for action in re.finditer(r'停车|购票|排队|预约|换乘',value):
            add(sent.start()+action.start(),sent.start()+action.end(),'nominal','ACTION_NOMINAL')
        for chain in re.finditer(r'[^，,：:。！？\s]+(?:\s*(?:→|->|—>|➜|➡)\s*[^，,：:。！？\s]+)+',value):
            add(sent.start()+chain.start(),sent.start()+chain.end(),'route','ARROW_CHAIN')
    if ROUTE.search(text) and len(text) <= max_chars:
        add(0, len(text), 'route', 'ROUTE_BLOCK')
    for match in re.finditer(r'[\u4e00-\u9fff]{0,6}(?:购票方式|游览安排|游览路线|游玩路线|行程安排)', text):
        add(*match.span(), 'nominal', 'EVENT_NOMINAL')
    return sorted(result.values(), key=lambda x: (x['start'], x['end']))
