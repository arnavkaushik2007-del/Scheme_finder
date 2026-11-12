"""Deterministic eligibility rules engine.

This module is the ONLY place where eligibility is decided. It is pure Python with no I/O and
no AI: the same rules and the same profile always give the same answer. Language models may
later phrase explanations, but never change these outcomes.

Rule tree grammar (stored as JSON on each scheme):
    group := {"all": [node, ...]} | {"any": [node, ...]} | {"not": node}
    leaf  := {"id": str, "field": FIELD, "op": OP, "value": ..., "note": {"en": str, "hi": str}?}

Three-valued logic: each node is ``pass``, ``fail`` or ``unknown`` (a field the person has not
provided). A scheme is "eligible" when the root passes, "not_eligible" when it fails, and
"maybe" when the answer depends on missing information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["pass", "fail", "unknown"]
Status = Literal["eligible", "maybe", "not_eligible"]

# ----------------------------------------------------------------------------- vocabulary

ENUMS: dict[str, dict[str, dict[str, str]]] = {
    "gender": {
        "male": {"en": "Male", "hi": "पुरुष"},
        "female": {"en": "Female", "hi": "महिला"},
        "transgender": {"en": "Transgender", "hi": "ट्रांसजेंडर"},
    },
    "residence": {"rural": {"en": "Rural", "hi": "ग्रामीण"}, "urban": {"en": "Urban", "hi": "शहरी"}},
    "category": {
        "general": {"en": "General", "hi": "सामान्य"},
        "obc": {"en": "OBC", "hi": "अन्य पिछड़ा वर्ग (OBC)"},
        "sc": {"en": "SC", "hi": "अनुसूचित जाति (SC)"},
        "st": {"en": "ST", "hi": "अनुसूचित जनजाति (ST)"},
        "ews": {"en": "EWS", "hi": "आर्थिक रूप से कमजोर वर्ग (EWS)"},
    },
    "occupation": {
        "farmer": {"en": "Farmer", "hi": "किसान"},
        "agricultural_labourer": {"en": "Agricultural labourer", "hi": "खेतिहर मज़दूर"},
        "unorganised_worker": {"en": "Unorganised-sector worker", "hi": "असंगठित क्षेत्र का श्रमिक"},
        "student": {"en": "Student", "hi": "विद्यार्थी"},
        "salaried": {"en": "Salaried employee", "hi": "वेतनभोगी कर्मचारी"},
        "self_employed": {"en": "Self-employed", "hi": "स्वरोज़गार"},
        "street_vendor": {"en": "Street vendor", "hi": "रेहड़ी-पटरी विक्रेता"},
        "artisan": {"en": "Artisan / craftsperson", "hi": "कारीगर / शिल्पकार"},
        "fisher": {"en": "Fisher", "hi": "मछुआरा"},
        "unemployed": {"en": "Unemployed", "hi": "बेरोज़गार"},
        "homemaker": {"en": "Homemaker", "hi": "गृहिणी"},
        "retired": {"en": "Retired", "hi": "सेवानिवृत्त"},
    },
    "marital_status": {
        "single": {"en": "Single", "hi": "अविवाहित"},
        "married": {"en": "Married", "hi": "विवाहित"},
        "widowed": {"en": "Widowed", "hi": "विधवा / विधुर"},
        "divorced": {"en": "Divorced", "hi": "तलाक़शुदा"},
    },
    "education_level": {
        "none": {"en": "No formal schooling", "hi": "औपचारिक शिक्षा नहीं"},
        "primary": {"en": "Primary", "hi": "प्राथमिक"},
        "secondary": {"en": "Secondary (10th)", "hi": "माध्यमिक (10वीं)"},
        "higher_secondary": {"en": "Higher secondary (12th)", "hi": "उच्च माध्यमिक (12वीं)"},
        "graduate": {"en": "Graduate", "hi": "स्नातक"},
        "postgraduate": {"en": "Postgraduate", "hi": "स्नातकोत्तर"},
    },
}

INDIAN_STATES = [
    "Andaman and Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
    "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry",
    "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal",
]


@dataclass(frozen=True)
class FieldSpec:
    kind: Literal["int", "number", "bool", "enum", "state"]
    en: str
    hi: str
    unit: Literal["", "inr", "years", "percent", "hectares"] = ""
    min: float | None = None
    max: float | None = None


FIELDS: dict[str, FieldSpec] = {
    "age": FieldSpec("int", "Age", "आयु", "years", 0, 120),
    "gender": FieldSpec("enum", "Gender", "लिंग"),
    "state": FieldSpec("state", "State", "राज्य"),
    "residence": FieldSpec("enum", "Area", "क्षेत्र"),
    "category": FieldSpec("enum", "Social category", "सामाजिक वर्ग"),
    "is_minority": FieldSpec("bool", "Belongs to a notified minority community", "अधिसूचित अल्पसंख्यक समुदाय से"),
    "annual_income": FieldSpec("int", "Family annual income", "परिवार की वार्षिक आय", "inr", 0, 1_000_000_000),
    "monthly_income": FieldSpec("int", "Own monthly income", "स्वयं की मासिक आय", "inr", 0, 100_000_000),
    "occupation": FieldSpec("enum", "Occupation", "व्यवसाय"),
    "is_bpl": FieldSpec("bool", "Below poverty line (BPL) household", "गरीबी रेखा से नीचे (BPL) परिवार"),
    "marital_status": FieldSpec("enum", "Marital status", "वैवाहिक स्थिति"),
    "disability_percent": FieldSpec("int", "Disability", "दिव्यांगता", "percent", 0, 100),
    "land_holding_hectares": FieldSpec("number", "Land holding", "भूमि जोत", "hectares", 0, 10_000),
    "is_income_tax_payer": FieldSpec("bool", "Pays income tax", "आयकर दाता"),
    "is_govt_employee": FieldSpec("bool", "Government employee", "सरकारी कर्मचारी"),
    "has_pucca_house": FieldSpec("bool", "Owns a pucca house", "पक्का मकान है"),
    "has_bank_account": FieldSpec("bool", "Has a bank account", "बैंक खाता है"),
    "education_level": FieldSpec("enum", "Education", "शिक्षा"),
    "marks_percent": FieldSpec("number", "Marks in last exam", "पिछली परीक्षा के अंक", "percent", 0, 100),
    "is_pregnant_or_lactating": FieldSpec("bool", "Pregnant or breastfeeding", "गर्भवती या स्तनपान कराने वाली"),
    "has_girl_child_under_10": FieldSpec("bool", "Has a daughter under 10", "10 वर्ष से कम आयु की बेटी है"),
    "family_size": FieldSpec("int", "Family size", "परिवार के सदस्य", "", 1, 50),
    "has_lpg_connection": FieldSpec("bool", "Household has an LPG connection", "घर में LPG कनेक्शन है"),
    "has_epf_esic_nps": FieldSpec("bool", "Member of EPFO / ESIC / NPS", "EPFO / ESIC / NPS के सदस्य"),
}

OPS = ("eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "between", "is_true", "is_false")
_NUMERIC_OPS = {"lt", "lte", "gt", "gte", "between"}

# ----------------------------------------------------------------------------- validation


class RuleError(ValueError):
    pass


def _check_scalar(field_name: str, spec: FieldSpec, v: Any) -> None:
    if spec.kind in ("int", "number"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise RuleError(f"{field_name}: value {v!r} must be a number")
    elif spec.kind == "enum":
        if v not in ENUMS[field_name]:
            raise RuleError(f"{field_name}: {v!r} is not one of {sorted(ENUMS[field_name])}")
    elif spec.kind == "state":
        if v not in INDIAN_STATES:
            raise RuleError(f"{field_name}: unknown state {v!r}")
    elif spec.kind == "bool":
        raise RuleError(f"{field_name}: boolean fields only take is_true / is_false")


def validate_rules(node: Any, *, _root: bool = True, _ids: set[str] | None = None) -> None:
    """Raise RuleError if the tree is malformed. Used on every admin save and on seed load."""
    ids = _ids if _ids is not None else set()
    if not isinstance(node, dict):
        raise RuleError("each rule node must be an object")
    if _root and set(node) != {"all"}:
        raise RuleError('the top level must be {"all": [...]}')
    groups = {"all", "any", "not"} & set(node)
    if groups:
        if len(node) != 1:
            raise RuleError(f"a group node must contain only its group key, got {sorted(node)}")
        key = next(iter(groups))
        if key == "not":
            validate_rules(node["not"], _root=False, _ids=ids)
        else:
            children = node[key]
            if not isinstance(children, list) or not children:
                raise RuleError(f'"{key}" needs a non-empty list')
            for child in children:
                validate_rules(child, _root=False, _ids=ids)
        return
    allowed = {"id", "field", "op", "value", "note"}
    if extra := set(node) - allowed:
        raise RuleError(f"unknown keys in rule: {sorted(extra)}")
    rid, fname, op = node.get("id"), node.get("field"), node.get("op")
    if not isinstance(rid, str) or not rid:
        raise RuleError("every rule needs a string id")
    if rid in ids:
        raise RuleError(f"duplicate rule id {rid!r}")
    ids.add(rid)
    if fname not in FIELDS:
        raise RuleError(f"rule {rid}: unknown field {fname!r}")
    if op not in OPS:
        raise RuleError(f"rule {rid}: unknown op {op!r}")
    spec = FIELDS[fname]
    value = node.get("value")
    if op in ("is_true", "is_false"):
        if spec.kind != "bool":
            raise RuleError(f"rule {rid}: {op} only applies to yes/no fields")
        if value is not None:
            raise RuleError(f"rule {rid}: {op} takes no value")
    elif spec.kind == "bool":
        raise RuleError(f"rule {rid}: yes/no field {fname} must use is_true / is_false")
    elif op in _NUMERIC_OPS:
        if spec.kind not in ("int", "number"):
            raise RuleError(f"rule {rid}: {op} needs a numeric field")
        if op == "between":
            if not (isinstance(value, list) and len(value) == 2):
                raise RuleError(f"rule {rid}: between needs [min, max]")
            for v in value:
                _check_scalar(fname, spec, v)
            if value[0] > value[1]:
                raise RuleError(f"rule {rid}: between min > max")
        else:
            _check_scalar(fname, spec, value)
    elif op in ("in", "not_in"):
        if not isinstance(value, list) or not value:
            raise RuleError(f"rule {rid}: {op} needs a non-empty list")
        for v in value:
            _check_scalar(fname, spec, v)
    else:  # eq / ne
        _check_scalar(fname, spec, value)
    note = node.get("note")
    if note is not None and not (isinstance(note, dict) and isinstance(note.get("en"), str)
                                 and isinstance(note.get("hi"), str)):
        raise RuleError(f"rule {rid}: note must be {{'en': str, 'hi': str}}")


def iter_leaves(node: dict[str, Any]):
    if "all" in node or "any" in node:
        for child in node.get("all") or node.get("any"):
            yield from iter_leaves(child)
    elif "not" in node:
        yield from iter_leaves(node["not"])
    else:
        yield node


def clean_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only known fields with valid values; anything else is treated as unknown."""
    out: dict[str, Any] = {}
    for name, value in raw.items():
        spec = FIELDS.get(name)
        if spec is None or value is None or value == "":
            continue
        if spec.kind == "bool":
            if isinstance(value, bool):
                out[name] = value
        elif spec.kind in ("int", "number"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
                continue
            out[name] = int(value) if spec.kind == "int" else float(value)
        elif spec.kind == "enum":
            if value in ENUMS[name]:
                out[name] = value
        elif spec.kind == "state" and value in INDIAN_STATES:
            out[name] = value
    return out

# ----------------------------------------------------------------------------- text


def _inr(n: float) -> str:
    n = int(round(n))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + "₹" + s


def format_value(field_name: str, value: Any, lang: str = "en") -> str:
    spec = FIELDS[field_name]
    if value is None:
        return "—"
    if isinstance(value, list):
        sep = ", " if lang == "en" else ", "
        return sep.join(format_value(field_name, v, lang) for v in value)
    if spec.kind == "bool":
        return ({True: "Yes", False: "No"} if lang == "en" else {True: "हाँ", False: "नहीं"})[bool(value)]
    if spec.kind == "enum":
        return ENUMS[field_name].get(value, {}).get(lang, str(value))
    if spec.kind == "state":
        return str(value)
    if spec.unit == "inr":
        return _inr(value)
    num = int(value) if float(value).is_integer() else value
    unit = {"years": {"en": " years", "hi": " वर्ष"}, "percent": {"en": "%", "hi": "%"},
            "hectares": {"en": " ha", "hi": " हेक्टेयर"}}.get(spec.unit)
    return f"{num}{unit[lang]}" if unit else str(num)


def describe_rule(leaf: dict[str, Any], lang: str = "en") -> str:
    """Plain-language statement of the requirement, e.g. 'Age between 18 and 40 years'."""
    fname, op, value = leaf["field"], leaf["op"], leaf.get("value")
    spec = FIELDS[fname]
    label = spec.en if lang == "en" else spec.hi
    if op in ("is_true", "is_false"):
        return f"{label}: {format_value(fname, op == 'is_true', lang)}"
    if op == "between":
        lo, hi_ = (format_value(fname, v, lang) for v in value)
        return f"{label} between {lo} and {hi_}" if lang == "en" else f"{label} {lo} से {hi_} के बीच"
    v = format_value(fname, value, lang)
    en = {"eq": f"{label}: {v}", "ne": f"{label} is not {v}", "in": f"{label}: one of {v}",
          "not_in": f"{label}: not {v}", "lt": f"{label} below {v}", "lte": f"{label} up to {v}",
          "gt": f"{label} above {v}", "gte": f"{label} at least {v}"}
    hi = {"eq": f"{label}: {v}", "ne": f"{label}: {v} नहीं", "in": f"{label}: {v} में से कोई",
          "not_in": f"{label}: {v} नहीं", "lt": f"{label} {v} से कम", "lte": f"{label} अधिकतम {v}",
          "gt": f"{label} {v} से अधिक", "gte": f"{label} कम से कम {v}"}
    return (en if lang == "en" else hi)[op]

# ----------------------------------------------------------------------------- evaluation


def _compare(op: str, actual: Any, value: Any) -> bool:
    match op:
        case "is_true":
            return actual is True
        case "is_false":
            return actual is False
        case "eq":
            return actual == value
        case "ne":
            return actual != value
        case "in":
            return actual in value
        case "not_in":
            return actual not in value
        case "lt":
            return actual < value
        case "lte":
            return actual <= value
        case "gt":
            return actual > value
        case "gte":
            return actual >= value
        case "between":
            return value[0] <= actual <= value[1]
    raise RuleError(f"unknown op {op}")


@dataclass
class NodeResult:
    kind: Literal["all", "any", "not", "leaf"]
    outcome: Outcome
    children: list["NodeResult"] = field(default_factory=list)
    rule: dict[str, Any] | None = None
    actual: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "outcome": self.outcome}
        if self.kind == "leaf" and self.rule is not None:
            r = self.rule
            d.update({
                "id": r["id"], "field": r["field"], "op": r["op"], "value": r.get("value"),
                "actual": self.actual,
                "text": {"en": describe_rule(r, "en"), "hi": describe_rule(r, "hi")},
                "evidence": {
                    "en": "Not provided" if self.actual is None else f"You: {format_value(r['field'], self.actual, 'en')}",
                    "hi": "जानकारी नहीं दी" if self.actual is None else f"आप: {format_value(r['field'], self.actual, 'hi')}",
                },
                "note": r.get("note"),
            })
        else:
            d["children"] = [c.to_dict() for c in self.children]
        return d


def _eval(node: dict[str, Any], profile: dict[str, Any]) -> NodeResult:
    if "all" in node:
        kids = [_eval(c, profile) for c in node["all"]]
        outs = {k.outcome for k in kids}
        outcome: Outcome = "fail" if "fail" in outs else ("unknown" if "unknown" in outs else "pass")
        return NodeResult("all", outcome, kids)
    if "any" in node:
        kids = [_eval(c, profile) for c in node["any"]]
        outs = {k.outcome for k in kids}
        outcome = "pass" if "pass" in outs else ("unknown" if "unknown" in outs else "fail")
        return NodeResult("any", outcome, kids)
    if "not" in node:
        kid = _eval(node["not"], profile)
        flipped: Outcome = {"pass": "fail", "fail": "pass", "unknown": "unknown"}[kid.outcome]  # type: ignore[assignment]
        return NodeResult("not", flipped, [kid])
    actual = profile.get(node["field"])
    if actual is None:
        return NodeResult("leaf", "unknown", rule=node, actual=None)
    return NodeResult("leaf", "pass" if _compare(node["op"], actual, node.get("value")) else "fail",
                      rule=node, actual=actual)


@dataclass
class Evaluation:
    status: Status
    tree: NodeResult
    passed: list[str]
    failed: list[str]
    unknown: list[str]

    @property
    def score(self) -> float:
        total = len(self.passed) + len(self.failed) + len(self.unknown)
        return len(self.passed) / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "passed": self.passed, "failed": self.failed, "unknown": self.unknown,
                "match_score": round(self.score, 3), "tree": self.tree.to_dict()}


def evaluate(rules: dict[str, Any], profile: dict[str, Any]) -> Evaluation:
    tree = _eval(rules, clean_profile(profile))
    passed, failed, unknown = [], [], []

    def walk(n: NodeResult) -> None:
        if n.kind == "leaf":
            {"pass": passed, "fail": failed, "unknown": unknown}[n.outcome].append(n.rule["id"])  # type: ignore[index]
        for c in n.children:
            walk(c)

    walk(tree)
    status: Status = {"pass": "eligible", "fail": "not_eligible", "unknown": "maybe"}[tree.outcome]  # type: ignore[assignment]
    return Evaluation(status, tree, passed, failed, unknown)


_STATUS_WEIGHT = {"eligible": 2, "maybe": 1, "not_eligible": 0}


def rank_key(evaluation: Evaluation, annual_benefit_inr: int) -> tuple[int, float, int]:
    """Sort key (use with reverse=True): status first, then share of rules met, then benefit size."""
    return (_STATUS_WEIGHT[evaluation.status], evaluation.score, annual_benefit_inr)
