"""Adaptive interview (spec 3.3): ask the question that narrows the remaining schemes the most.

For every field that some still-possible scheme depends on, we simulate each plausible answer
(enum values, yes/no, or one representative value per interval between the rule thresholds) and
compute the expected number of schemes that would remain possible. The field with the lowest
expected count wins; that is information-gain question ordering over the rule set. Answering
"don't know" skips a field, and those rules stay "maybe". Eligibility itself still comes from the
deterministic rules engine.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services import eligibility, schemes

MAX_QUESTIONS = 20
ORDER = ["state", "age", "gender", "occupation", "annual_income", "category", "residence", "is_bpl",
         "marital_status", "has_bank_account", "is_income_tax_payer"]

QUESTIONS: dict[str, dict[str, str]] = {
    "age": {"en": "How old are you?", "hi": "आपकी उम्र कितनी है?"},
    "gender": {"en": "What is your gender?", "hi": "आपका लिंग क्या है?"},
    "state": {"en": "Which state do you live in?", "hi": "आप किस राज्य में रहते हैं?"},
    "residence": {"en": "Do you live in a village (rural) or a town/city (urban)?", "hi": "आप गाँव (ग्रामीण) में रहते हैं या शहर में?"},
    "category": {"en": "Which social category do you belong to?", "hi": "आप किस सामाजिक वर्ग से हैं?"},
    "is_minority": {"en": "Do you belong to a notified minority community?", "hi": "क्या आप अधिसूचित अल्पसंख्यक समुदाय से हैं?"},
    "annual_income": {"en": "What is your family's total income in a year (₹)?", "hi": "आपके परिवार की सालाना कुल आय कितनी है (₹)?"},
    "monthly_income": {"en": "How much do you yourself earn in a month (₹)?", "hi": "आप स्वयं महीने में कितना कमाते हैं (₹)?"},
    "occupation": {"en": "What work do you do?", "hi": "आप क्या काम करते हैं?"},
    "is_bpl": {"en": "Does your family have a BPL (below poverty line) or Antyodaya card?", "hi": "क्या आपके परिवार के पास BPL या अंत्योदय कार्ड है?"},
    "marital_status": {"en": "What is your marital status?", "hi": "आपकी वैवाहिक स्थिति क्या है?"},
    "disability_percent": {"en": "If you have a disability certificate, what percentage does it show? (0 if none)", "hi": "अगर दिव्यांगता प्रमाणपत्र है, तो उसमें कितना प्रतिशत लिखा है? (नहीं है तो 0)"},
    "land_holding_hectares": {"en": "How much farm land does your family own (hectares)?", "hi": "आपके परिवार के पास कितनी खेती की ज़मीन है (हेक्टेयर)?"},
    "is_income_tax_payer": {"en": "Do you pay income tax?", "hi": "क्या आप आयकर देते हैं?"},
    "is_govt_employee": {"en": "Are you a government employee?", "hi": "क्या आप सरकारी कर्मचारी हैं?"},
    "has_pucca_house": {"en": "Does your family own a pucca (brick/concrete) house?", "hi": "क्या आपके परिवार के पास पक्का मकान है?"},
    "has_bank_account": {"en": "Do you have a bank account?", "hi": "क्या आपका बैंक खाता है?"},
    "education_level": {"en": "What is your highest education?", "hi": "आपकी सबसे ऊँची पढ़ाई क्या है?"},
    "marks_percent": {"en": "What percentage did you get in your last exam?", "hi": "पिछली परीक्षा में आपके कितने प्रतिशत अंक आए?"},
    "is_pregnant_or_lactating": {"en": "Are you pregnant or breastfeeding?", "hi": "क्या आप गर्भवती हैं या स्तनपान कराती हैं?"},
    "has_girl_child_under_10": {"en": "Do you have a daughter younger than 10?", "hi": "क्या आपकी 10 साल से छोटी बेटी है?"},
    "family_size": {"en": "How many people are in your family?", "hi": "आपके परिवार में कितने लोग हैं?"},
    "has_lpg_connection": {"en": "Does your household already have an LPG gas connection?", "hi": "क्या आपके घर में पहले से LPG गैस कनेक्शन है?"},
    "has_epf_esic_nps": {"en": "Are you a member of EPF, ESIC or NPS?", "hi": "क्या आप EPF, ESIC या NPS के सदस्य हैं?"},
}


def _candidates(field: str, rules: list[dict[str, Any]]) -> list[Any]:
    spec = eligibility.FIELDS[field]
    if spec.kind == "bool":
        return [True, False]
    if spec.kind == "enum":
        return list(eligibility.ENUMS[field])
    used = [leaf for r in rules for leaf in eligibility.iter_leaves(r) if leaf["field"] == field]
    if spec.kind == "state":
        states = sorted({v for leaf in used for v in (leaf["value"] if isinstance(leaf["value"], list) else [leaf["value"]])})
        other = next(s for s in eligibility.INDIAN_STATES if s not in states)
        return states + [other]
    points: set[float] = set()
    for leaf in used:
        vals = leaf["value"] if isinstance(leaf.get("value"), list) else [leaf.get("value")]
        points |= {float(v) for v in vals if isinstance(v, (int, float))}
    if not points:
        return []
    b = sorted(points)
    reps = [b[0] - 1] + [(x + y) / 2 for x, y in zip(b, b[1:], strict=False)] + [b[-1] + 1] + b
    lo = spec.min if spec.min is not None else float("-inf")
    reps = [max(lo, r) for r in reps]
    return sorted({int(r) if spec.kind == "int" else r for r in reps})


def _possible(rows: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in rows:
        if s["level"] == "state" and profile.get("state") not in (None, s["state"]):
            continue
        if eligibility.evaluate(s["rules"], profile).status != "not_eligible":
            out.append(s)
    return out


def choose_next(rows: list[dict[str, Any]], answers: dict[str, Any], skipped: set[str]) -> tuple[str | None, float]:
    possible = _possible(rows, answers)
    if not possible:
        return None, 0.0
    fields = {leaf["field"] for s in possible for leaf in eligibility.iter_leaves(s["rules"])}
    fields |= {"state"} if any(s["level"] == "state" for s in possible) else set()
    best, best_score = None, float(len(possible))
    for f in sorted(fields - set(answers) - skipped, key=lambda x: (ORDER.index(x) if x in ORDER else 99, x)):
        vals = _candidates(f, [s["rules"] for s in possible])
        if not vals:
            continue
        expected = sum(len(_possible(possible, {**answers, f: v})) for v in vals) / len(vals)
        if expected < best_score - 1e-9:
            best, best_score = f, expected
    return best, best_score


def question_payload(field: str, lang: str = "en") -> dict[str, Any]:
    spec = eligibility.FIELDS[field]
    q: dict[str, Any] = {"field": field, "text": QUESTIONS.get(field, {"en": spec.en, "hi": spec.hi}),
                         "kind": spec.kind, "unit": spec.unit, "min": spec.min, "max": spec.max}
    if spec.kind == "enum":
        q["options"] = eligibility.ENUMS[field]
    elif spec.kind == "bool":
        q["options"] = {"yes": {"en": "Yes", "hi": "हाँ"}, "no": {"en": "No", "hi": "नहीं"}}
    elif spec.kind == "state":
        q["options"] = {s: {"en": s, "hi": s} for s in eligibility.INDIAN_STATES}
    return q


async def next_question(db: AsyncIOMotorDatabase, answers: dict[str, Any], skipped: list[str]) -> dict[str, Any]:
    rows = await schemes.list_schemes(db)
    clean = eligibility.clean_profile(answers)
    possible = _possible(rows, clean)
    counts = {"eligible": 0, "maybe": 0}
    for s in possible:
        counts[eligibility.evaluate(s["rules"], clean).status] += 1
    asked = len(clean) + len(set(skipped))
    field, expected = (None, 0.0) if asked >= MAX_QUESTIONS else choose_next(rows, clean, set(skipped))
    return {
        "done": field is None, "question": question_payload(field) if field else None,
        "expected_remaining": round(expected, 2) if field else None,
        "possible": len(possible), "eligible": counts["eligible"], "maybe": counts["maybe"], "total": len(rows),
        "asked": asked, "answers": clean, "skipped": sorted(set(skipped)),
    }
