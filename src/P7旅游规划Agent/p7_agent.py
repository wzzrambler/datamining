# -*- coding: utf-8 -*-
# 项目7：基于知识图谱的旅游规划 Agent（ReAct）。
#
# 一次问答跑的是一个循环，不是一条固定流水线：
#     Thought（下一步做什么） -> Action（调哪个工具、参数是什么）
#     -> Observation（工具返回什么） -> 再 Thought
# 每轮的 Action 由上一轮的 Observation 决定，所以同一个程序面对
# "缺景区""景区不在库""路线太粗"这些情况会走不同的分支。
#
# 工具五个，全部读项目3/5/6 的产物，不把图谱内容写进提示词：
#     check_area    这个景区库里有没有
#     get_route     取一条结构化路线，附带颗粒度判断
#     search_spots  按景区和人群取景点
#     get_tips      取条件建议
#     tailor_route  按人群裁剪、给步骤打标，生成最终行程
#
# LLM 是可选的：设了 OPENAI_API_KEY，由模型输出下一步的 Action；
# 没设就走规则策略。两条路共用同一个循环和同一套工具，差别只在"谁来想"。
#
#   python src/p7_agent.py                     跑全部测试场景
#   python src/p7_agent.py --ask "带老人去九寨沟一天"
#   python src/p7_agent.py --llm               强制走 LLM 决策（需 key）
#
# 输出：outputs/agent_runs.json、outputs/agent_demo.txt、outputs/figures/p7_react.png

import argparse
import inspect
import json
import os
import re
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROC = os.path.join(ROOT, "processed")
OUTPUTS = os.path.join(ROOT, "outputs", "P7")

AREAS = ["九寨沟风景名胜区", "广州长隆旅游度假区", "珠海横琴长隆国际海洋度假区"]

AREA_ALIAS = {
    "九寨沟": "九寨沟风景名胜区",
    "长隆野生动物世界": "广州长隆旅游度假区",
    "野生动物世界": "广州长隆旅游度假区",
    "广州长隆": "广州长隆旅游度假区",
    "长隆": "广州长隆旅游度假区",
    "珠海长隆": "珠海横琴长隆国际海洋度假区",
    "横琴": "珠海横琴长隆国际海洋度假区",
    "海洋王国": "珠海横琴长隆国际海洋度假区",
}

VISITOR_KW = {
    "老人": ["老人", "爸妈", "父母", "长辈", "爷爷", "奶奶", "老年", "走不动"],
    "亲子": ["孩子", "亲子", "小孩", "儿子", "女儿", "带娃", "6岁", "五岁"],
    "情侣": ["女朋友", "男朋友", "情侣", "对象", "爱人", "二人世界"],
}

SHORT = {"九寨沟风景名胜区": "九寨沟",
         "广州长隆旅游度假区": "广州长隆",
         "珠海横琴长隆国际海洋度假区": "珠海长隆"}


def load(*parts):
    with open(os.path.join(PROC, *parts), encoding="utf-8") as f:
        return json.load(f)


CODE_TO_AREA = {"GZCL": "广州长隆旅游度假区",
                "ZHCL": "珠海横琴长隆国际海洋度假区",
                "JZG": "九寨沟风景名胜区"}


def _mm(v):
    return "%02d:%02d" % (v // 60, v % 60) if isinstance(v, int) else ""


def _norm_route(r):
    """项目3 的路线结构以证据为中心（每步挂 poi_refs/transport_refs/time），
    这里摊平成 Agent 用的线性步骤。摊平只降维度，不再做判断：
    主景点取 attraction 角色，交通方式取 transport_refs 的第一条原文。"""
    steps = []
    for s in r.get("steps") or []:
        refs = s.get("poi_refs") or []
        main = [p for p in refs if p.get("poi_role") == "attraction"] or refs
        name = (main[0].get("canonical_name") or main[0].get("text")) if main else ""
        tr = s.get("transport_refs") or []
        t = s.get("time") or {}
        raw = ((s.get("evidence") or {}).get("text") or "").strip()
        if not name:      # 这一步没解析出主景点，退到原文片段，总比空着强
            name = raw[:18]
        steps.append({"to": name,
                      "text": raw or name,     # 展示用原文"南门进园"，不是光秃秃的景点名
                      "support": [p.get("text", "") for p in refs if p.get("poi_role") == "support"],
                      "transport": (tr[0].get("text") or "").rstrip("。") if tr else "",
                      "period": t.get("period") or "",
                      "time_start": _mm(t.get("start_minute")),
                      "time_end": _mm(t.get("end_minute"))})
    starts = [x["time_start"] for x in steps if x["time_start"]]
    ends = [x["time_end"] or x["time_start"] for x in steps if x["time_end"] or x["time_start"]]
    # 项目6 的路线没有标题字段，摘要第一条就是这条路线的走法
    sums = r.get("summaries") or []
    title = (sums[0].get("text") if sums else "") or r["route_id"]
    # 一日/二日只在摘要里写过，没有独立字段
    sub = next((w for w in ("一日", "二日", "三日") if w in title), "")
    return {"route_id": r["route_id"], "scenic_area": CODE_TO_AREA[r["scenic_code"]],
            "title": title, "sub_route": sub, "granularity": r.get("granularity", ""),
            "steps": steps, "step_count": len(steps),
            "time_start": starts[0] if starts else "", "time_end": ends[-1] if ends else ""}


# 读项目6 的正式线路：项目3 的 42 条里有 13 条判成碎片被图谱丢掉，
# 那些没有完整走向，不能拿来排行程
ROUTES = [_norm_route(r) for r in load("P6知识图谱", "cleaned-v1", "routes.json")]

# 项目2 按文档存 mentions，这里按景区摊平；景点名直接取 POI 类型的 text
ENTS = [{"scenic_area": d["scenic_area"],
         "entities": [m for m in d["mentions"] if m.get("type") == "POI"]}
        for d in load("P2实体识别", "entities.json")["documents"]]


def _clip(text, n=48):
    """按标点截断，别把一句话砍在半截。"""
    if len(text) <= n:
        return text
    cut = max(text.rfind(p, 0, n) for p in "，。；、")
    return text[:cut + 1] if cut > n * 0.5 else text[:n] + "…"


def _tips(rows):
    """项目6 的清洗版建议字段名和早期版本不同，先统一成 get_tips 认的形状。"""
    out = []
    for r in rows:
        advice = (r.get("consequence_text") or "").strip()
        if not advice:
            continue
        cond = r.get("condition") or {}
        clauses = cond.get("clauses") or []
        head = (cond.get("text") or "").strip()
        # 只给结论的话，"直接放弃"这种片段读不出是什么情况下放弃，
        # 把条件接回前面才对得上原文
        display = "%s，%s" % (head, advice) if head and head not in advice else advice
        out.append({"scenic_area": CODE_TO_AREA.get(r.get("scenic_code"), ""),
                    "rule": "if_pattern" if clauses else "plain",
                    "cond_type": clauses[0].get("category", "") if clauses else "",
                    "sentence": head or advice,
                    "advice": advice,
                    "display": _clip(display),
                    "kind": r.get("statement_kind", "")})
    return out


CONDS = _tips(load("P6知识图谱", "cleaned-v1", "conditions.json"))


# 参数解析
def mentioned_areas(text):
    """一句话里点到哪几个景区。

    长名字优先占位，"珠海长隆海洋王国"里的"长隆"就不再算广州，
    否则一句正常的话会被判成"提到了两个景区"。
    """
    spans, hit = [], []
    for k, v in sorted(AREA_ALIAS.items(), key=lambda kv: -len(kv[0])):
        start = 0
        while True:
            i = text.find(k, start)
            if i < 0:
                break
            if not any(not (i + len(k) <= a or i >= b) for a, b in spans):
                spans.append((i, i + len(k)))
                if v not in hit:
                    hit.append(v)
            start = i + 1
    order = {}
    for k, v in AREA_ALIAS.items():
        i = text.find(k)
        if i >= 0 and v in hit:
            order[v] = min(order.get(v, 10 ** 6), i)
    return sorted(hit, key=lambda a: order.get(a, 10 ** 6))


def guess_place(text):
    """用户没说库里的景区，但提到了别的地方（"去三亚玩三天"）。

    "没说去哪"和"说了但库里没有"是两件事，处理方式不一样：前者要反问，
    后者要如实说没有。这里从"去X玩/在X旅游"这类句式里把地名抠出来。
    """
    for pat in (r"去([\u4e00-\u9fa5]{2,8}?)(?:玩|旅游|旅行|自由行|逛逛|呆|待|的)",
                r"在([\u4e00-\u9fa5]{2,8}?)(?:玩|旅游|旅行)"):
        m = re.search(pat, text)
        if m:
            name = m.group(1)
            if name not in AREA_ALIAS and name not in SHORT.values():
                return name
    return ""


def parse_slots(text):
    """把用户这句话里的约束抠出来，抠不到的留空，由策略决定是问还是估。"""
    areas = mentioned_areas(text)
    slots = {
        "area": areas[0] if len(areas) == 1 else "",
        "areas": areas,
        "other_place": guess_place(text) if not areas else "",
        "visitor": "",
        "days": 1,
    }
    for k, kws in VISITOR_KW.items():
        if any(w in text for w in kws):
            slots["visitor"] = k
            break
    if re.search(r"两天|2天|两日|2日|过夜|住一晚", text):
        slots["days"] = 2
    elif re.search(r"三天|3天|三日", text):
        slots["days"] = 3
    return slots


# 工具
def resolve_area(area):
    """把用户/模型嘴里的景区名收敛到库里的正式名。

    parse_slots 一直有别名表，工具却只认全名，于是 LLM 说"九寨沟"就查不到，
    还会以为是数据没有。别名查不到时退一步做包含匹配，仍不中再原样返回。
    """
    if area in AREAS:
        return area
    text = (area or "").strip()
    if text in AREA_ALIAS:
        return AREA_ALIAS[text]
    for alias, full in sorted(AREA_ALIAS.items(), key=lambda kv: -len(kv[0])):
        if alias in text:
            return full
    for full in AREAS:
        if full in text or text in full:
            return full
    return area


def check_area(area):
    area = resolve_area(area)
    rows = [r for r in ROUTES if r["scenic_area"] == area]
    return {"exists": bool(rows), "routes": len(rows),
            "available": [SHORT[a] for a in AREAS]}


def get_route(area, days=1):
    area = resolve_area(area)
    cands = [r for r in ROUTES if r["scenic_area"] == area]
    picked = None
    if days and days >= 2:
        picked = next((r for r in cands if "二日" in r.get("sub_route", "")), None)
    if picked is None:
        picked = next((r for r in cands if "一日" in r.get("sub_route", "")),
                      cands[0] if cands else None)
    if picked is None:
        return {"ok": False, "reason": "没有解析出结构化路线"}
    span = ""
    if picked["time_start"] and picked["time_end"]:
        span = "%s—%s" % (picked["time_start"], picked["time_end"])
    elif picked["time_start"]:
        span = "%s 起" % picked["time_start"]
    # 步数太少说明只到园区级，带天数的需求靠它排不出来
    coarse = picked["step_count"] < 5
    return {"ok": True, "route_id": picked["route_id"], "steps": picked["steps"],
            "step_count": picked["step_count"], "span": span,
            "sub_route": picked.get("sub_route", ""), "coarse": coarse,
            "title": picked["title"][:24]}


def search_spots(area, visitor="", top_k=8):
    # 只认景区内的景点：项目2 里标了 poi_role 和 scope，
    # 不加这两条会把成都的餐厅、路过的服务区也算成九寨沟的景点
    bag = {}
    area = resolve_area(area)
    for r in ENTS:
        if r["scenic_area"] != area:
            continue
        for e in r["entities"]:
            if e.get("poi_role") != "attraction" or e.get("scope") != "in_area":
                continue
            name = e.get("canonical_name") or e.get("text")
            if name:
                bag[name] = bag.get(name, 0) + 1
    pref = {"亲子": ["熊猫", "考拉", "动物", "剧场", "表演"],
            "老人": ["缆车", "观景", "餐厅", "休息"],
            "情侣": ["缆车", "天鹅", "花", "观景"]}.get(visitor, [])
    items = sorted(bag.items(), key=lambda kv: (-kv[1], kv[0]))
    if pref:
        items.sort(key=lambda kv: (0 if any(p in kv[0] for p in pref) else 1, -kv[1]))
    return {"hit": len(bag), "spots": [k for k, _ in items[:top_k]]}


TIP_RANK = {"conditional_advice": 0, "conditional_restriction": 1,
            "general_advice": 2, "general_restriction": 3}


def get_tips(area, keyword="", limit=3):
    """取注意事项。带条件的排前面——"夏天去要带防晒"比"早些准备总是好的"更能用。"""
    rows = []
    area = resolve_area(area)
    for c in CONDS:
        if c["scenic_area"] != area:
            continue
        rank = TIP_RANK.get(c["kind"])
        if rank is None:          # conditional_fact 陈述的是事实，不是建议
            continue
        if keyword and keyword not in c["sentence"] and keyword not in c["advice"]:
            continue
        rows.append((rank, c["display"]))
    rows.sort(key=lambda x: x[0])
    return {"count": len(rows), "tips": [a for _, a in rows[:limit]]}


def tailor_route(route, visitor):
    """按人群处理步骤：不删步骤，只打标，删了路线就断了。"""
    steps = []
    for s in route["steps"]:
        if not s["to"]:
            continue
        tag = ""
        if visitor == "老人" and s["transport"] in ("步行", "徒步"):
            tag = "可跳过"
        elif visitor == "亲子" and any(w in s["to"] for w in ["熊猫", "考拉", "剧场", "表演"]):
            tag = "适合孩子"
        elif visitor == "情侣" and any(w in s["to"] for w in ["缆车", "天鹅", "花"]):
            tag = "适合拍照"
        steps.append({"step": len(steps) + 1, "period": s["period"],
                      "time": s["time_start"], "spot": s["text"],
                      "transport": s["transport"], "tag": tag})
    tagged = sum(1 for x in steps if x["tag"])
    skipped = sum(1 for x in steps if x["tag"] == "可跳过")
    return {"steps": steps, "count": len(steps), "tagged": tagged, "skipped": skipped}


TOOLS = {
    "check_area": check_area,
    "get_route": get_route,
    "search_spots": search_spots,
    "get_tips": get_tips,
    "tailor_route": tailor_route,
}
# LLM 会自作主张多塞参数（check_area 带个 keyword），直接把多余键丢掉，
# 别让它因为一个用不上的字段空转到轮次上限。
TOOL_PARAMS = {name: set(inspect.signature(fn).parameters) for name, fn in TOOLS.items()}


# LLM（可选）
def call_llm(prompt, timeout=25):
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return None
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    try:
        r = requests.post(base.rstrip("/") + "/chat/completions",
                          headers={"Authorization": "Bearer " + key,
                                   "Content-Type": "application/json"},
                          json={"model": model, "temperature": 0.2,
                                "messages": [{"role": "user", "content": prompt}]},
                          timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


REACT_PROMPT = """你在扮演一个旅游规划 Agent，按 ReAct 的方式决定下一步。

可用工具：
- check_area(area)                     查景区库里有没有
- get_route(area, days)                取结构化路线
- search_spots(area, visitor, top_k)   取景点
- get_tips(area, keyword)              取注意事项
- tailor_route(route, visitor)         按人群定制行程（需要先拿到 route）

只允许两种输出格式，不要多余解释：
Thought: 一句话说明你现在要做什么
Action: 工具名
Action Input: {{"参数": 值}}

或者：
Thought: 信息已经够了
Action: final

用户请求：{query}
已经拿到的观察：{memory}
"""


# Agent
class Agent:
    def __init__(self, text, use_llm=False, max_turns=6):
        self.text = text
        self.use_llm = use_llm and bool(os.environ.get("OPENAI_API_KEY"))
        self.max_turns = max_turns
        self.slots = {}
        self.obs = {}
        self.turns = []
        self.note = []
        self.status = ""
        self.plan = None
        self.answer = ""
        self.route = None
        self.fail_count = {}
        self.trace = []

    def diag(self, message):
        """引擎层的诊断信息，只进记录不进答案：参数被丢掉、调用被拦下这类事对用户没有意义。"""
        if message not in self.trace:
            self.trace.append(message)

    def say(self, message):
        """备注只留一条。同一句话每轮都追加，答案末尾会叠出四条一模一样的提醒。"""
        if message not in self.note:
            self.note.append(message)

    def log(self, i, thought, action, args, obs):
        self.turns.append({"turn": i, "thought": thought, "action": action,
                           "args": args, "observation": obs})

    def run(self):
        self.slots = parse_slots(self.text)
        self.log(0, "先把这句话里的景区、人群、天数拆出来",
                 "parse_slots", {"text": self.text[:20]},
                 "景区 %s／人群 %s／%d 天" % (
                     "、".join(SHORT[a] for a in self.slots["areas"]) if self.slots["areas"]
                     else (self.slots.get("other_place") or "未提到"),
                     self.slots["visitor"] or "未提到", self.slots["days"]))

        for i in range(1, self.max_turns + 1):
            thought, action, args = self.think()
            if action in ("final", "ask"):
                self.log(i, thought, action, args, "结束")
                self.finish(action, args)
                return self.result()
            fn = TOOLS.get(action)
            if fn is None:
                self.log(i, thought, action, args, "工具不存在")
                self.diag("工具 %s 没实现" % action)
                break
            extra = [k for k in args if k not in TOOL_PARAMS[action]]
            if extra:
                self.diag("工具 %s 忽略了多余参数：%s" % (action, "、".join(sorted(extra))))
                args = {k: v for k, v in args.items() if k in TOOL_PARAMS[action]}
            try:
                obs = fn(**args)
            except Exception as e:
                obs = {"ok": False, "error": str(e)[:40]}
            self.obs[action] = obs
            self.log(i, thought, action, args, self._brief(action, obs))
            # 同一个调用连错两次就别再试了：模型往往只会换说法重复同一个错误
            sig = (action, json.dumps(args, ensure_ascii=False, sort_keys=True))
            if obs.get("ok") is False:
                self.fail_count[sig] = self.fail_count.get(sig, 0) + 1
                if self.fail_count[sig] >= 2:
                    self.say("同一调用连续失败两次，提前收尾：%s" % action)
                    break
            if action == "get_route" and obs.get("ok"):
                self.route = obs
        self.finish("final", {"reason": "turns_exhausted"})
        return self.result()

    def _brief(self, action, obs):
        # 工具抛异常时 run() 会把 {"ok": False, "error": ...} 塞进 obs，
        # 早先这里直接按正常字段取值，于是"工具失败不中断会话"的承诺在记录环节就断了
        if obs.get("ok") is False:
            return "工具失败：%s" % (obs.get("error") or obs.get("reason") or "未给出原因")
        if action == "check_area":
            return "库里有 %d 条路线" % obs.get("routes", 0) if obs.get("exists") else "库里没有这个景区"
        if action == "get_route":
            if not obs.get("ok"):
                return obs.get("reason", "取路线失败")
            return "%s，%d 步，%s%s" % (obs["route_id"], obs["step_count"],
                                       obs["span"] or "无时刻",
                                       "（只到园区级）" if obs["coarse"] else "")
        if action == "search_spots":
            return "命中 %d 个景点，取前 %d 个" % (obs["hit"], len(obs["spots"]))
        if action == "get_tips":
            return "候选中 %d 条，取前 %d 条" % (obs["count"], len(obs["tips"]))
        if action == "tailor_route":
            return "成行 %d 步，标注 %d 处" % (obs["count"], obs["tagged"])
        return str(obs)[:40]

    def think(self):
        if self.use_llm:
            out = self._llm_think()
            if out:
                return out
        return self._rule_think()

    def _rule_think(self):
        s = self.slots

        if len(s["areas"]) >= 2 and s["days"] <= 1:
            return ("用户点了多个景区但只给一天，先说明排不开", "final",
                    {"reason": "conflict",
                     "message": "一次只能排一个景区。%s 不在同一片区域，一天之内走不完，"
                                "建议先选一个，另一个放到第二天。" % "、".join(SHORT[a] for a in s["areas"])})

        if not s["area"]:
            other = self.slots.get("other_place", "")
            if other:
                return ("用户说的是 %s，库里没有这个地方，直接说明" % other, "final",
                        {"reason": "no_data",
                         "message": "库里只有九寨沟、广州长隆、珠海长隆三个景区的路线，"
                                    "没有 %s 的数据，换个景区我可以直接排。" % other})
            return ("没说去哪个景区，先反问，不猜", "ask",
                    {"message": "想去哪个景区？库里现成的有九寨沟、广州长隆、珠海长隆。"})

        if "check_area" not in self.obs:
            return ("先确认这个景区库里有没有路线", "check_area", {"area": s["area"]})

        if not self.obs["check_area"]["exists"]:
            return ("库里没有这个景区，只能说明情况", "final",
                    {"reason": "no_data",
                     "message": "库里只有九寨沟、广州长隆、珠海长隆三个景区的路线，"
                                "你说的这个地方暂时没有数据。"})

        if "get_route" not in self.obs:
            return ("景区在库里，先取一条符合天数的路线", "get_route",
                    {"area": s["area"], "days": s["days"]})

        route = self.obs["get_route"]
        if not route.get("ok"):
            return ("路线取不到，直接说明，不硬编", "final",
                    {"reason": "no_route",
                     "message": "这个景区还没解析出结构化路线，暂时排不出行程。"})

        if route["coarse"] and s["days"] >= 2:
            self.say("路线只到园区级（%d 步），撑不起 %d 天" % (route["step_count"], s["days"]))
        if not route["span"]:
            self.say("这条路线原文没写时刻，只能给顺序")
        if s["days"] >= 2 and "二日" not in route.get("sub_route", ""):
            self.say("库里这条景区没有两日路线，先用一日路线排，第二天建议自由活动")

        if "search_spots" not in self.obs:
            return ("再看这个景区有哪些景点能排进去", "search_spots",
                    {"area": s["area"], "visitor": s["visitor"] or "", "top_k": 8})

        if "get_tips" not in self.obs:
            return ("顺手取这个景区的注意事项，最后一起给", "get_tips",
                    {"area": s["area"], "keyword": ""})

        if "tailor_route" not in self.obs:
            return ("材料齐了，按人群把路线裁一版", "tailor_route",
                    {"route": self.route, "visitor": s["visitor"] or ""})

        return ("信息够了，可以出方案", "final", {"reason": "done"})

    def _llm_think(self):
        mem = json.dumps({k: (v if not isinstance(v, dict) else
                              {kk: vv for kk, vv in v.items() if kk != "steps"})
                          for k, v in self.obs.items()}, ensure_ascii=False)[:800]
        out = call_llm(REACT_PROMPT.format(query=self.text, memory=mem or "（还没有）"))
        if not out:
            return None
        m_a = re.search(r"Action:\s*(\w+)", out)
        m_t = re.search(r"Thought:\s*(.+)", out)
        if not m_a:
            return None
        action = m_a.group(1).strip()
        thought = (m_t.group(1).strip() if m_t else "")[:60]
        if action in ("final", "ask"):
            return thought or "模型判断可以结束", action, {"reason": "llm", "message": ""}
        m_i = re.search(r"Action Input:\s*(\{.*?\})", out, re.S)
        try:
            args = json.loads(m_i.group(1)) if m_i else {}
        except Exception:
            args = {}
        if action == "tailor_route":
            args = {"route": self.route, "visitor": args.get("visitor", self.slots.get("visitor", ""))}
        elif action == "search_spots":
            args.setdefault("area", self.slots.get("area", ""))
            args.setdefault("visitor", self.slots.get("visitor", ""))
            args.setdefault("top_k", 8)
        elif action == "get_route":
            args.setdefault("area", self.slots.get("area", ""))
            args.setdefault("days", self.slots.get("days", 1))
        elif action in ("check_area", "get_tips"):
            args.setdefault("area", self.slots.get("area", ""))
            args.setdefault("keyword", "")
        return thought or "模型决定调用 %s" % action, action, args

    def finish(self, kind, payload):
        if kind == "ask":
            self.status = "need_info"
            self.answer = payload["message"]
            return
        reason = payload.get("reason", "done")
        if reason in ("no_data", "conflict", "no_route"):
            self.status = "conflict" if reason == "conflict" else "no_data"
            self.answer = payload["message"]
            return

        route = self.obs.get("get_route", {})
        steps = self.obs.get("tailor_route", {}).get("steps", [])
        tips = self.obs.get("get_tips", {}).get("tips", [])
        if not steps and route.get("steps"):
            # 轮次用完了但路线已经拿到，用原始步骤兜底，别让用户白等
            steps = [{"step": i, "period": x["period"], "time": x["time_start"],
                      "spot": x["text"], "transport": x["transport"], "tag": ""}
                     for i, x in enumerate([y for y in route["steps"] if y["to"]], 1)]
            self.say("轮次上限到了，直接按原路线输出，没做人群裁剪")
        if not steps:
            self.status = "no_data"
            failed = [k for k, v in self.obs.items() if v.get("ok") is False]
            if failed:
                self.answer = ("这次没排出方案：%s 工具调用没成功，问题在调用参数不在数据，"
                               "重跑一次或换成规则策略再看。" % "、".join(failed))
            elif route.get("ok") is False:
                self.answer = route.get("reason") or "这个景区没有能用的路线。"
            else:
                self.answer = "路线取到了但没排出步骤，先看项目 3 的解析结果。"
            return

        area = self.slots["area"]
        # 天数用用户说的，别去摘要文本里猜——摘要里"一日"经常是形容某一段而不是整条路线
        span = {1: "一日", 2: "两日", 3: "三日"}.get(self.slots["days"], "")
        lines = ["按%s的%s给你排了 %d 步：" % (SHORT[area], span or "现有路线", len(steps))]
        cur = ""
        for s in steps:
            # "未说明"占了两百多步，一律当成没写，别在答案里摆一排【未说明】
            per = s["period"] if s["period"] and s["period"] not in ("未说明", "unspecified") else ""
            if per and per != cur:
                cur = per
                lines.append("【%s】" % cur)
            spot = s["spot"]
            if per and spot.startswith(per):   # 前面已经有【上午】，"上午长海"就只写长海
                spot = spot[len(per):]
            seg = "  %s" % spot
            if s["time"]:
                seg += "（%s）" % s["time"]
            if s["transport"]:
                seg += "，%s" % s["transport"]
            if s["tag"]:
                seg += "（%s）" % s["tag"]
            lines.append(seg)
        if tips:
            lines.append("注意：" + "；".join(tips[:2]))
        if self.note:
            lines.append("提醒：" + "；".join(self.note))

        self.plan = {"area": area, "visitor": self.slots["visitor"] or "未指定",
                     "days": self.slots["days"], "route_id": route.get("route_id", ""),
                     "steps": steps, "tips": tips, "notes": self.note}
        self.answer = "\n".join(lines)
        self.status = "partial" if self.note else "ok"

    def result(self):
        return {"input": self.text, "status": self.status,
                "slots": {k: v for k, v in self.slots.items() if k != "areas"},
                "route_id": (self.route or {}).get("route_id", ""),
                "plan": self.plan, "answer": self.answer,
                "turns": self.turns, "mode": "llm" if self.use_llm else "rule",
                "diagnostics": self.trace}


# 测试场景
SCENES = [
    "带爸妈去九寨沟玩一天，他们走不动太久",
    "带6岁孩子去广州长隆，一天怎么安排",
    "和女朋友去珠海长隆海洋王国玩两天",
    "去三亚玩三天",
    "帮我规划一下",
    "一天想把广州长隆和珠海长隆都玩了",
    "九寨沟玩两天，怎么安排",
]


def draw(result, runs):
    """左图画循环结构，右图画七个场景的结果。

    真实轮次不放图里：九寨沟那条有 6 轮、每轮三个字段，
    缩到 15 厘米宽之后字全糊在一起，轮次改用表格说明。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

    for name in ["Microsoft YaHei", "SimHei"]:
        try:
            font_manager.findfont(name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [name]
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.9, 3.0), dpi=300,
                                   gridspec_kw={"width_ratios": [1.05, 1.2]})

    def box(ax, x, y, w, h, title, sub, fc):
        ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                    boxstyle="round,pad=0.012,rounding_size=0.03",
                                    facecolor=fc, edgecolor="none"))
        ax.text(x, y + 0.032, title, ha="center", va="center", fontsize=7.2, color="white")
        ax.text(x, y - 0.042, sub, ha="center", va="center", fontsize=5.6, color="white")

    def arrow(ax, p1, p2, style="-|>", ls="-", color="#9AA5B1", rad=0.0):
        ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, linestyle=ls,
                                     color=color, lw=1.0, mutation_scale=7,
                                     connectionstyle="arc3,rad=%s" % rad))

    box(ax1, 0.28, 0.72, 0.40, 0.22, "思考 Thought", "下一步查什么", "#6B7A90")
    box(ax1, 0.76, 0.72, 0.40, 0.22, "动作 Action", "调一个图谱工具", "#E8833A")
    box(ax1, 0.76, 0.28, 0.40, 0.22, "观察 Observation", "工具返回写进记忆", "#5B8C5A")
    box(ax1, 0.28, 0.28, 0.40, 0.22, "判断", "信息够不够", "#7A6BA8")

    arrow(ax1, (0.48, 0.72), (0.56, 0.72))
    arrow(ax1, (0.76, 0.61), (0.76, 0.39))
    arrow(ax1, (0.56, 0.28), (0.48, 0.28))
    arrow(ax1, (0.28, 0.39), (0.28, 0.61), ls="--")

    ax1.text(0.315, 0.50, "不够就继续", ha="left", va="center", fontsize=5.4, color="#777777")
    ax1.text(0.52, 0.10, "够 → 按记忆里的观察出方案    缺景区 / 排不开 → 反问",
             ha="center", fontsize=5.6, color="#555555")

    ax1.set_xlim(0.0, 1.0)
    ax1.set_ylim(0.04, 0.94)
    ax1.axis("off")
    ax1.set_title("ReAct 循环：每轮的观察决定下一轮的动作", fontsize=7.4, pad=4)

    labels, vals, cols = [], [], []
    tone = {"ok": "#2F6DB5", "partial": "#E8833A", "need_info": "#7A6BA8",
            "no_data": "#9AA5B1", "conflict": "#B5735B"}
    tag_cn = {"ok": "成行", "partial": "成行·有提醒", "need_info": "反问",
              "no_data": "说明无数据", "conflict": "说明冲突"}
    for r in runs:
        q = r["input"]
        labels.append(q[:10] + ("…" if len(q) > 10 else ""))
        vals.append(len(r["plan"]["steps"]) if r["plan"] else 0)
        cols.append(tone.get(r["status"], "#9AA5B1"))

    ys = list(range(len(runs)))[::-1]
    ax2.barh(ys, vals, height=0.52, color=cols)
    for y, v, r in zip(ys, vals, runs):
        tag = tag_cn.get(r["status"], r["status"])
        ax2.text(v + 0.4, y, ("%d 步  %s" % (v, tag)) if v else tag, va="center",
                 fontsize=5.8, color="#333333")
    ax2.set_yticks(ys)
    ax2.set_yticklabels(labels, fontsize=5.8)
    ax2.set_xlim(0, 33)
    ax2.set_xlabel("输出步数", fontsize=6.4)
    ax2.set_title("七个测试场景的结果", fontsize=7.4, pad=4)
    ax2.spines[["top", "right"]].set_visible(False)
    ax2.tick_params(labelsize=5.8, length=2.2, pad=2)

    fig.tight_layout(pad=0.4)
    os.makedirs(os.path.join(OUTPUTS, "figures"), exist_ok=True)
    fig.savefig(os.path.join(OUTPUTS, "figures", "p7_react.png"))
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", default="")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--max_turns", type=int, default=6)
    args = ap.parse_args()

    cases = [args.ask] if args.ask else SCENES
    runs = []
    for q in cases:
        t0 = time.time()
        r = Agent(q, use_llm=args.llm, max_turns=args.max_turns).run()
        r["elapsed"] = round(time.time() - t0, 2)
        runs.append(r)
        print("\n【问】%s" % q)
        for t in r["turns"]:
            print("  %d 想法 %s" % (t["turn"], t["thought"]))
            print("    动作 %s %s" % (t["action"], json.dumps(t["args"], ensure_ascii=False)[:70]))
            print("    观察 %s" % t["observation"])
        print("  【状态】%s" % r["status"])
        print("  【答】%s" % r["answer"].replace("\n", " / ")[:120])

    os.makedirs(OUTPUTS, exist_ok=True)
    if args.ask:
        # 单条查询只打印，不覆盖批量测试的记录
        draw(runs[0], runs)
        return
    with open(os.path.join(OUTPUTS, "agent_runs.json"), "w", encoding="utf-8") as f:
        json.dump(runs, f, ensure_ascii=False, indent=1)

    with open(os.path.join(OUTPUTS, "agent_demo.txt"), "w", encoding="utf-8") as f:
        f.write("ReAct Agent 运行记录（模式：%s）\n" % ("LLM 决策" if args.llm else "规则策略"))
        for r in runs:
            f.write("\n" + "=" * 56 + "\n【问】%s\n" % r["input"])
            for t in r["turns"]:
                f.write("  第%d轮 想法：%s\n" % (t["turn"], t["thought"]))
                f.write("        动作：%s  %s\n" % (t["action"], json.dumps(t["args"], ensure_ascii=False)[:90]))
                f.write("        观察：%s\n" % t["observation"])
            f.write("  状态：%s\n  答：\n%s\n" % (r["status"], r["answer"]))

    draw(runs[0], runs)
    ok = sum(1 for r in runs if r["status"] in ("ok", "partial"))
    print("\n%d 个场景跑完，成行 %d 个；记录在 outputs/agent_runs.json" % (len(runs), ok))


if __name__ == "__main__":
    main()
