"""Self-built ReAct-style tool loop using non-streaming Chat Completions."""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

from .budget import initial_messages
from .common import ASSETS, compact, digest, file_hash, read_json, write_json
from .host import Reject, ToolHost


class DeepSeekClient:
    def __init__(self, settings):
        from openai import OpenAI
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("缺少DEEPSEEK_API_KEY环境变量；请在本地配置，不要写入仓库。")
        self.settings = settings
        self.client = OpenAI(api_key=key, base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                             timeout=settings.timeout_seconds, max_retries=settings.api_retries)

    def complete(self, messages, tools):
        s = self.settings
        reply = self.client.chat.completions.create(
            model=s.model, messages=messages, tools=tools, stream=False,
            tool_choice="auto" if s.thinking else "required",
            max_tokens=s.output_tokens, temperature=s.temperature,
            extra_body={"thinking": {"type": "enabled" if s.thinking else "disabled"}},
        )
        choice = reply.choices[0]
        message = {"role": "assistant", "content": choice.message.content}
        if choice.message.tool_calls:
            message["tool_calls"] = [t.model_dump(exclude_none=True) for t in choice.message.tool_calls]
        # Required when the caller opts into DeepSeek thinking mode. Default off.
        reasoning = getattr(choice.message, "reasoning_content", None)
        if s.thinking and reasoning is not None:
            message["reasoning_content"] = reasoning
        return {"message": message, "finish_reason": choice.finish_reason,
                "usage": reply.usage.model_dump(exclude_none=True) if reply.usage else {},
                "response_id": reply.id, "model": reply.model}


def compact_messages(package, host):
    # This is a lossless business ledger, not a model-written summary. Raw model
    # replies/tool events remain in the checkpoint even after history compaction.
    accepted = []
    for rid, record in host.active.items():
        arguments = {k: v for k, v in record["arguments"].items()
                     if k not in {"resolves_issue_ids", "normalizations"}}
        if record.get("route_steps"):
            arguments["steps"] = record["route_steps"]
            arguments["route_id"] = record.get("route_id") or arguments["route_id"]
        item = {"record_id": rid, "tool_name": record["tool_name"], "arguments": arguments}
        if record["leg_ref"]:
            item["leg_ref"] = record["leg_ref"]
        accepted.append(item)
    ledger = {
        "instruction": "下面是全部已保存结果和待处理错误。沿用这些记录ID，只补充尚未保存的信息，不重复提交已有条目。全部处理完就调用finish_chunk。",
        "accepted": accepted,
        "retracted_record_ids": [rid for rid, r in host.records.items() if not r["active"]],
        "open_issues": {k: v for k, v in host.issues.items() if v["status"] == "open"},
        "last_feedback": host.events[-1]["result"] if host.events else None,
    }
    return initial_messages(package["context"]) + [{"role": "user", "content": compact(ledger)}]


_CODE_FILES = None
_ASSETS = None


def implementation_hashes():
    """实现指纹只算一次。它要读三十来个文件，每个包算一遍纯属浪费。"""
    global _CODE_FILES
    if _CODE_FILES is None:
        files = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
        files['artifact_integrity.py'] = file_hash(Path(__file__).resolve().parents[2] / 'artifact_integrity.py')
        _CODE_FILES = files
    return _CODE_FILES


def asset_hashes():
    global _ASSETS
    if _ASSETS is None:
        _ASSETS = {p.name: file_hash(p) for p in sorted(ASSETS.iterdir()) if p.is_file()}
    return _ASSETS


def package_fingerprint(package, settings):
    """一个包的结果只对这套（输入、配置、实现、提示词资产）成立。"""
    return digest({"package": package, "settings": settings.public_dict(),
                   "code": implementation_hashes(), "assets": asset_hashes()})


def run_package(package, client, contracts, budget, checkpoint: Path, entity_scenes=None, resume=True):
    s = budget.settings
    tools = contracts.api_tools()
    host = ToolHost(package, contracts, s.max_same_error, entity_scenes)
    fingerprint = package_fingerprint(package, s)
    state = {"fingerprint": fingerprint, "rounds": 0, "tool_calls": 0,
             "messages": initial_messages(package["context"]), "responses": [],
             "pending": [], "pending_index": 0, "compactions": 0, "usage": [],
             "token_estimate_factor": budget.factor, "stop_reason": None}
    if checkpoint.exists():
        if not resume:
            raise ValueError("checkpoint已存在；请更换run-id或使用resume")
        state = read_json(checkpoint)
        if state["fingerprint"] != fingerprint:
            raise ValueError("输入、配置或实现已变化，请使用新的run-id，避免错误续跑")
        host.restore(state["host"])
        budget.factor = max(budget.factor, state.get("token_estimate_factor", 1))
        if host.finished:
            return state
        host.status = "RUNNING"
        state["stop_reason"] = None

    def save():
        state["host"] = host.snapshot()
        state["token_estimate_factor"] = budget.factor
        write_json(checkpoint, state)

    def stop(status, reason):
        host.status = status
        state["stop_reason"] = reason
        save()
        return state

    save()
    while not host.finished:
        # Complete every pending tool-call response before another API call.
        # A checkpoint is atomic after each tool, so resuming never duplicates an
        # accepted result or leaves an assistant's tool_calls unanswered.
        while state["pending_index"] < len(state["pending"]):
            call = state["pending"][state["pending_index"]]
            if state["tool_calls"] >= s.max_tool_calls:
                return stop("EXHAUSTED", "MAX_TOOL_CALLS")
            fn = call.get("function", {})
            if fn.get("name") == "finish_chunk" and state["pending_index"] != len(state["pending"])-1:
                result = host.fail("finish_chunk", fn.get("arguments", ""), Reject(
                    "INVALID_FINISH_DECLARATION", "finish_chunk必须是本轮最后一个工具；后续提交处理完后再结束。"))
                host.events.append({"tool_name": "finish_chunk", "arguments": fn.get("arguments"), "result": result})
            else:
                result = host.execute(fn.get("name", ""), fn.get("arguments", ""))
            state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": compact(result)})
            state["tool_calls"] += 1
            state["pending_index"] += 1
            save()
        state["pending"] = []
        state["pending_index"] = 0
        if host.finished:
            break
        if state["rounds"] >= s.max_rounds:
            return stop("EXHAUSTED", "MAX_ROUNDS")
        if not budget.fits(state["messages"], tools):
            state["messages"] = compact_messages(package, host)
            state["compactions"] += 1
            save()
            if not budget.fits(state["messages"], tools):
                return stop("EXHAUSTED", "CONTEXT_BUDGET")
        estimate = budget.estimate(state["messages"], tools)
        state["rounds"] += 1
        save()  # Even a failed HTTP attempt consumes one bounded round.
        try:
            response = client.complete(state["messages"], tools)
        except Exception as error:
            # Do not serialize exception strings, URLs, request headers or keys.
            return stop("FAILED", "API_" + type(error).__name__)
        usage = response.get("usage", {})
        state["usage"].append({"round": state["rounds"], "input_estimate": estimate, **usage})
        state["responses"].append(deepcopy(response))
        budget.observe(state["messages"], tools, usage.get("prompt_tokens"))
        if usage.get("total_tokens", 0) > s.context_limit:
            return stop("EXHAUSTED", "ACTUAL_USAGE_OVER_CONTEXT_LIMIT")
        message = response["message"]
        calls = message.get("tool_calls", [])
        if response["finish_reason"] == "length":
            # A truncated batch may contain valid-looking prefixes. Accept none.
            state["messages"].append({"role": "user", "content": "上轮输出被截断，工具均未执行。请每轮减少提交数量，参数完整后再提交。"})
            save()
            continue
        if not calls:
            state["messages"].append(message)
            state["messages"].append({"role": "user", "content": "必须调用工具提交；如已处理完请调用finish_chunk。"})
            save()
            continue
        ids = [c.get("id") for c in calls]
        if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
            return stop("FAILED", "INVALID_PROVIDER_TOOL_CALL_IDS")
        state["messages"].append(message)
        state["pending"] = calls
        save()
    save()
    return state
