"""Offline estimates, deliberately not advertised as DeepSeek tokenizer counts."""
import math
import re

from .common import compact


class TokenBudget:
    method = "offline_cjk_ascii_estimate_v1; calibrated upward by API usage"

    def __init__(self, settings):
        self.settings = settings
        self.factor = 1.0

    @staticmethod
    def estimate_text(text):
        # Approximate CJK=1 token/character, ASCII word/ID runs=1/3 chars,
        # punctuation=1 token. Includes a 15% margin. No tokenizer downloads.
        runs = re.findall(r"[A-Za-z0-9_]+|[^A-Za-z0-9_\s]", text)
        units = sum(math.ceil(len(x) / 3) if x.isascii() and re.fullmatch(r"\w+", x) else 1
                    for x in runs)
        return math.ceil(units * 1.15)

    def raw(self, messages, tools):
        # Content/arguments are tokenized as text, not as escaped JSON strings.
        total = self.estimate_text(compact(tools)) + 64 + 12 * len(messages)
        for m in messages:
            total += self.estimate_text(m.get("content") or "")
            total += self.estimate_text(m.get("reasoning_content") or "")
            for call in m.get("tool_calls", []):
                total += self.estimate_text(call["function"]["name"] + call["function"]["arguments"]) + 16
        return total

    def estimate(self, messages, tools):
        return math.ceil(self.raw(messages, tools) * self.factor)

    def observe(self, messages, tools, prompt_tokens):
        if prompt_tokens:
            self.factor = max(self.factor, prompt_tokens / max(1, self.raw(messages, tools)) * 1.15)

    def fits(self, messages, tools, initial=False):
        reserve = self.settings.history_reserve if initial else 0
        return self.estimate(messages, tools) + self.settings.output_tokens + self.settings.safety_tokens + reserve <= self.settings.context_limit


def initial_messages(context):
    from .common import ASSETS
    prompt = (ASSETS / "extraction_system_prompt.md").read_text(encoding="utf-8")
    # Stable prefix per scenic batch; only essential IDs/names/types enter it.
    def table(rows):
        if not rows:
            return []
        columns = list(rows[0])
        return {"columns": columns, "rows": [[r.get(k) for k in columns] for r in rows]}

    prefix = {"地点注册表": table([{k: r[k] for k in ("id", "name")} for r in context["scene_poi_registry"]]),
              "交通方式注册表": table([{k: r[k] for k in ("id", "name")} for r in context["scene_transport_registry"]])}
    # Recognizer output stays in host-side audit data. TIME entries are not sent.
    original = {"包类型": context["package_kind"], "标题": context["heading"],
                "文本块": [{"block_id": b["block_id"], "text": b["text"]} for b in context["blocks"]]}
    routes = []
    for route in context["p3_routes"]:
        steps = [{k: s[k] for k in ("step_id", "index", "evidence_text", "poi_ids", "transport_ids",
                                    "day", "period", "order_group")} for s in route["steps"]]
        routes.append({"route_id": route["route_id"], "steps": table(steps),
                       "order_relations": route["order_relations"]})
    content = "当前原文包：\n" + compact(original)
    if routes:
        content += "\n相关线路及步骤：\n" + compact(routes)
    return [{"role": "system", "content": prompt + "\n表格编码说明：columns给出列名，rows中的每一行按相同顺序对应这些字段。\n当前景区注册表：\n" + compact(prefix)},
            {"role": "user", "content": content}]
