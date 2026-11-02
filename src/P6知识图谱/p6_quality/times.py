"""Historical time observations: lossless text union, conservative clock union."""
import re
from collections import defaultdict

from .common import compact, digest

TIME_ATTRIBUTES = {"opening_time", "operating_time", "arrival_time", "departure_time",
                   "visit_time", "recommended_stay", "ride_duration", "frequency"}
AVAILABILITY = {"opening_time", "operating_time"}
# Only parse an entire, unambiguous 24-hour clock range. Natural-language
# qualifiers remain intact; never strip weekday/season/holiday restrictions.
RANGE = re.compile(r"^(?:每天|每日)?\s*(\d{1,2})[:：](\d{2})\s*[-—–~～至到]\s*(\d{1,2})[:：](\d{2})$")


def clock_intervals(text):
    match = RANGE.fullmatch(text.strip())
    if not match:
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    if h1 > 23 or m1 > 59 or h2 > 24 or m2 > 59 or (h2 == 24 and m2):
        return None
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start == end:
        return None  # Ambiguous zero length versus all day.
    return [(start, end)] if start < end else [(start, 1440)] + ([(0, end)] if end else [])


def summarize_times(facts, records, rules=()):
    observations = list(facts)
    # Rules may contain availability even when the model did not repeat it as
    # a POI fact. Preserve it as rule evidence, without inventing a fact or
    # converting an advisory sentence into a declared opening schedule.
    for rule in rules:
        text = rule.get("consequence_text") or ""
        if rule["statement_kind"] not in {"conditional_fact", "conditional_restriction", "general_restriction"}:
            continue
        if not re.search(r"开放|营业|运行时间|运营时间", text):
            continue
        for target in rule["targets"]:
            if target["kind"] not in {"POI", "LEG", "TRANSPORT"}:
                continue
            attribute = "opening_time" if target["kind"] == "POI" else "operating_time"
            observations.append({"entity_id" if target["kind"] != "LEG" else "leg_id": target["id"],
                "attribute": attribute, "value_text": text, "condition": rule["condition"],
                "modality": "prohibited" if rule["force"] == "prohibited" else "unclear",
                "observation_ids": rule["observation_ids"], "origin_kind": "conditional_rule",
                "rule_ids": [rule["rule_id"]], "route_id": None, "step_id": None,
                "scope_status": rule.get("scope_status"), "scenic_code": rule.get("scenic_code")})
    groups = {}
    for fact in observations:
        if fact["attribute"] not in TIME_ATTRIBUTES:
            continue
        target = fact.get("entity_id") or fact.get("leg_id")
        key = (target, fact["attribute"])
        group = groups.setdefault(key, {"time_summary_id": "TIME-" + digest(key)[:20],
            "target_id": target, "attribute": fact["attribute"],
            "category": "availability" if fact["attribute"] in AVAILABILITY else "itinerary_or_duration",
            "observations": [], "text_union": [], "interval_unions": [],
            "historical_only": True, "unobserved_times_are_not_closed": True})
        scope = {k: fact.get(k) for k in ("route_id", "step_id", "from_step_id", "to_step_id")}
        # Keep migrated fragment bindings and scene restrictions inside every
        # observation, so a cleared route ID never becomes a global schedule.
        for field in ("scope_status", "upstream_route_id", "upstream_step_id", "upstream_from_step_id", "upstream_to_step_id", "scenic_code"):
            if fact.get(field) is not None:
                scope[field] = fact[field]
        group["observations"].append({"time_text": fact["value_text"], "condition": fact["condition"],
            "origin_kind": fact.get("origin_kind", "extracted_attribute"), "rule_ids": fact.get("rule_ids", []),
            "scope_basis": "not_specified_in_rule" if fact.get("origin_kind") == "conditional_rule" else "attribute_fields",
            "modality": fact["modality"], "scope": scope, "observation_ids": fact["observation_ids"],
            "source_fact_id": fact.get("source_fact_id"),
            "sources": [{"observation_id": rid, "doc_id": records[rid]["provenance"]["doc_id"],
                         "package_key": records[rid]["package_key"]} for rid in fact["observation_ids"]]})
        item = {"time_text": fact["value_text"], "condition": fact["condition"], "modality": fact["modality"]}
        if item not in group["text_union"]:
            group["text_union"].append(item)
    for group in groups.values():
        buckets = defaultdict(list)
        for obs in group["observations"]:
            intervals = clock_intervals(obs["time_text"]) if group["category"] == "availability" and obs["modality"] not in {"prohibited", "unclear"} and not obs["scope"].get("scope_status") else None
            obs["normalization"] = "clock_range" if intervals is not None else "text_preserved"
            if intervals is None:
                continue
            # Different applicability, modality, or route scope never collapse.
            key = compact([obs["condition"], obs["modality"], obs["scope"]])
            for start, end in intervals:
                buckets[key].append((start, end, obs["observation_ids"]))
        for key, intervals in buckets.items():
            merged = []
            for start, end, ids in sorted(intervals):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(end, merged[-1][1])
                    merged[-1][2].update(ids)
                else:
                    merged.append([start, end, set(ids)])
            import json
            condition, modality, scope = json.loads(key)
            group["interval_unions"].append({"condition": condition, "modality": modality, "scope": scope,
                "intervals": [{"start": f"{a//60:02d}:{a%60:02d}", "end": f"{b//60:02d}:{b%60:02d}",
                               "observation_ids": sorted(ids)} for a, b, ids in merged]})
    return sorted(groups.values(), key=lambda g: (g["target_id"], g["attribute"]))
