"""Canonical JSON Schema stays in the host; API uses a portable subset."""
from copy import deepcopy

from jsonschema import Draft202012Validator

from .common import ASSETS, read_json


class Contracts:
    def __init__(self):
        self.schema = read_json(ASSETS / "extraction_calls.schema.json")
        self.tools = read_json(ASSETS / "extraction_tools.json")["tools"]
        self.names = {x["name"] for x in self.tools}
        self.validators = {
            name: Draft202012Validator({"$defs": self.schema["$defs"], "$ref": "#/$defs/" + name})
            for name in self.names
        }

    def errors(self, name, args):
        if name not in self.validators:
            return [("/tool_name", "未知工具；只能调用当前提供的六个工具。")]
        return [("/arguments/" + "/".join(map(str, e.absolute_path)), e.message[:700])
                for e in self.validators[name].iter_errors(args)][:10]

    def api_tools(self):
        # allOf/if/then business constraints are checked locally. Avoid sending
        # strict-mode-specific features to an OpenAI-compatible endpoint.
        def portable(node):
            if isinstance(node, list):
                return [portable(x) for x in node]
            if not isinstance(node, dict):
                return node
            result = {}
            for k, v in node.items():
                if k in {"allOf", "if", "then", "else", "$schema", "uniqueItems", "minLength",
                         "maxLength", "minItems", "maxItems", "pattern"}:
                    continue
                if k == "const":
                    result["enum"] = [v]
                else:
                    result["anyOf" if k == "oneOf" else k] = portable(v)
            return result
        return [{"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": portable(deepcopy(t["parameters"]))}}
            for t in self.tools]
