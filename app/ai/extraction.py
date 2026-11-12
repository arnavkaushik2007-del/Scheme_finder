"""Document -> text -> structured fields, all in memory.

1. Text: PDFs with a text layer are read with PyMuPDF. Scanned PDFs and images go through
   Tesseract OCR (English + Hindi). If Tesseract is not installed, the result says so
   (``ocr_available=False``) instead of pretending.
2. Fields: deterministic patterns for each document type (English and Hindi labels) give a
   value plus a confidence. If a real LLM is configured, it reads the *masked* text too; a value
   both agree on gets higher confidence, an LLM-only value gets low confidence.
3. ID masking: any 12-digit Aadhaar-like number is reduced to its last four digits before the
   text goes anywhere (LLM, logs, database). Raw text is never stored.

The user reviews and corrects every field before it is used.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.ai.llm import get_llm
from app.config import get_settings

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- masking

AADHAAR_RE = re.compile(r"(?<!\d)(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})(?!\d)")
VID_RE = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")  # 16-digit Aadhaar VID
ACCOUNT_RE = re.compile(r"(?i)(a/?c(?:count)?\s*(?:no\.?|number)?\s*[:\-]?\s*)(\d{9,18})")


def mask_ids(text: str) -> str:
    """Replace Aadhaar numbers / VIDs / bank account numbers with masked forms."""
    text = ACCOUNT_RE.sub(lambda m: m.group(1) + "X" * (len(m.group(2)) - 4) + m.group(2)[-4:], text)
    text = VID_RE.sub(lambda m: "XXXX XXXX XXXX " + re.sub(r"\D", "", m.group(0))[-4:], text)
    return AADHAAR_RE.sub(lambda m: f"XXXX XXXX {m.group(3)}", text)


def contains_full_aadhaar(text: str) -> bool:
    return bool(AADHAAR_RE.search(text))

# ----------------------------------------------------------------------------- file sniffing


ALLOWED = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def sniff_type(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None

# ----------------------------------------------------------------------------- text extraction


def ocr_available() -> bool:
    try:
        import pytesseract

        if get_settings().tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = get_settings().tesseract_cmd
        pytesseract.get_tesseract_version()
        return True
    except Exception:  # noqa: BLE001 - binary missing or broken
        return False


def _ocr_image_bytes(data: bytes) -> str:
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.load()
    langs = pytesseract.get_languages(config="")
    lang = "+".join(x for x in ("eng", "hin") if x in langs) or "eng"
    return pytesseract.image_to_string(img, lang=lang)


@dataclass
class TextResult:
    text: str
    method: str  # pdf_text | ocr | none
    pages: int = 1
    ocr_available: bool = True
    warnings: list[str] = field(default_factory=list)


def extract_text(data: bytes, content_type: str) -> TextResult:
    settings = get_settings()
    has_ocr = ocr_available()
    if content_type == "application/pdf":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.page_count > settings.max_pdf_pages:
                raise ValueError("too_many_pages")
            text = "\n".join(page.get_text() for page in doc)
            if len(text.strip()) >= 20:
                return TextResult(text, "pdf_text", doc.page_count, has_ocr)
            if not has_ocr:
                return TextResult("", "none", doc.page_count, False, ["ocr_unavailable"])
            parts = [_ocr_image_bytes(page.get_pixmap(dpi=200).tobytes("png")) for page in doc]
            return TextResult("\n".join(parts), "ocr", doc.page_count, True)
    if not has_ocr:
        return TextResult("", "none", 1, False, ["ocr_unavailable"])
    return TextResult(_ocr_image_bytes(data), "ocr", 1, True)

# ----------------------------------------------------------------------------- field parsers

# Fields each document type can provide, and where each lands in the profile.
DOC_FIELDS: dict[str, list[str]] = {
    "aadhaar": ["full_name", "date_of_birth", "gender", "aadhaar_last4", "state"],
    "income_certificate": ["full_name", "annual_income", "state", "district", "issue_date"],
    "caste_certificate": ["full_name", "category", "state"],
    "ration_card": ["full_name", "is_bpl", "family_size", "state"],
    "bpl_card": ["full_name", "is_bpl"],
    "disability_certificate": ["full_name", "disability_percent"],
    "marksheet": ["full_name", "marks_percent", "education_level"],
    "bank_passbook": ["full_name", "has_bank_account", "bank_account_last4"],
    "land_record": ["full_name", "land_holding_hectares", "state", "occupation"],
    "birth_certificate": ["full_name", "date_of_birth", "gender"],
    "age_proof": ["full_name", "date_of_birth"],
    "domicile_certificate": ["full_name", "state", "district"],
}
GENERIC_FIELDS = sorted({f for fs in DOC_FIELDS.values() for f in fs})

# profile section each field belongs to
PERSONAL_FIELDS = {"full_name", "date_of_birth", "aadhaar_last4", "district", "bank_account_last4", "issue_date"}

STATES = None  # loaded lazily from the rules vocabulary


def _states() -> list[str]:
    global STATES
    if STATES is None:
        from app.services.eligibility import INDIAN_STATES

        STATES = INDIAN_STATES
    return STATES


def _num(s: str) -> float | None:
    s = s.replace(",", "").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def _label(text: str, labels: str, value: str = r"([^\n]{2,60})") -> str | None:
    m = re.search(rf"(?im)(?:{labels})\s*[:\-–]?\s*{value}", text)
    return m.group(1).strip() if m else None


_DOB_FORMATS = (r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", r"(\d{4})-(\d{2})-(\d{2})")


def _parse_date(s: str) -> str | None:
    m = re.search(_DOB_FORMATS[1], s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(_DOB_FORMATS[0], s)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def age_from_dob(dob: str, today: date | None = None) -> int | None:
    try:
        b = date.fromisoformat(dob)
    except ValueError:
        return None
    t = today or date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def parse_fields(text: str, doc_type: str) -> dict[str, dict[str, Any]]:
    """Deterministic extraction. Returns {field: {"value", "confidence", "source": "pattern"}}."""
    out: dict[str, dict[str, Any]] = {}
    wanted = set(DOC_FIELDS.get(doc_type, GENERIC_FIELDS))

    def put(name: str, value: Any, conf: float) -> None:
        if name in wanted and value not in (None, "") and name not in out:
            out[name] = {"value": value, "confidence": round(conf, 2), "source": "pattern"}

    # name
    name = _label(text, r"name of (?:the )?(?:applicant|candidate|holder|student)|applicant(?:'s)? name|"
                        r"candidate(?:'s)? name|holder(?:'s)? name|\bname\b|नाम", r"([A-Za-zऀ-ॿ .']{3,60})")
    if name:
        put("full_name", re.sub(r"\s+", " ", name).strip(" ."), 0.85)

    # date of birth
    dob_raw = _label(text, r"date of birth|d\.?o\.?b\.?|जन्म तिथि|जन्मतिथि", r"([0-9/\-. ]{8,12})")
    if dob_raw and (dob := _parse_date(dob_raw)):
        put("date_of_birth", dob, 0.9)
    else:
        yob = _label(text, r"year of birth|जन्म वर्ष", r"(\d{4})")
        if yob:
            put("date_of_birth", f"{yob}-07-01", 0.5)  # only the year is known

    # gender
    g = re.search(r"(?i)\b(female|male|transgender)\b|(महिला|पुरुष|ट्रांसजेंडर)", text)
    if g:
        word = (g.group(1) or g.group(2)).lower()
        put("gender", {"महिला": "female", "पुरुष": "male", "ट्रांसजेंडर": "transgender"}.get(word, word), 0.85)

    # Aadhaar: only ever the last four digits
    a = AADHAAR_RE.search(text) or re.search(r"XXXX\s?XXXX\s?(\d{4})", text)
    if a:
        put("aadhaar_last4", a.group(a.lastindex or 1)[-4:], 0.9)

    # state / district
    for st in sorted(_states(), key=len, reverse=True):
        if re.search(rf"(?i)\b{re.escape(st)}\b", text):
            put("state", st, 0.75)
            break
    dist = _label(text, r"district|जिला|ज़िला", r"([A-Za-zऀ-ॿ ]{3,40})")
    if dist:
        put("district", dist.strip(), 0.7)

    # income
    inc = _label(text, r"(?:total )?(?:annual|yearly) (?:family )?income|family income|वार्षिक आय|कुल आय",
                 r"(?:rs\.?|inr|₹|रु\.?)?\s*([\d,]{3,12})")
    if inc and (v := _num(inc)) is not None:
        put("annual_income", int(v), 0.9)
    else:
        monthly = _label(text, r"monthly income|मासिक आय", r"(?:rs\.?|inr|₹)?\s*([\d,]{3,10})")
        if monthly and (v := _num(monthly)) is not None:
            put("annual_income", int(v * 12), 0.6)

    # category
    low = text.lower()
    cat = None
    if re.search(r"scheduled tribe|\bst\b category|अनुसूचित जनजाति", text, re.I):
        cat = "st"
    elif re.search(r"scheduled caste|\bsc\b category|अनुसूचित जाति", text, re.I):
        cat = "sc"
    elif re.search(r"other backward class|\bobc\b|अन्य पिछड़ा वर्ग", text, re.I):
        cat = "obc"
    elif re.search(r"economically weaker section|\bews\b|आर्थिक रूप से कमजोर", text, re.I):
        cat = "ews"
    if cat:
        put("category", cat, 0.85)

    # BPL / ration card type
    if re.search(r"antyodaya|\baay\b|below poverty line|\bbpl\b|अंत्योदय|गरीबी रेखा से नीचे", text, re.I):
        put("is_bpl", True, 0.85)
    elif re.search(r"\bapl\b|above poverty line", text, re.I):
        put("is_bpl", False, 0.8)
    members = _label(text, r"(?:number of|no\.? of|total) (?:family )?members|सदस्यों की संख्या", r"(\d{1,2})")
    if members:
        put("family_size", int(members), 0.8)

    # disability
    dis = _label(text, r"(?:percentage of|degree of)? ?(?:permanent )?disability(?: percentage)?|दिव्यांगता",
                 r"(\d{1,3})\s*%")
    if dis and 0 <= int(dis) <= 100:
        put("disability_percent", int(dis), 0.85)

    # marks
    pct = _label(text, r"percentage|aggregate|overall|प्रतिशत", r"(\d{1,3}(?:\.\d{1,2})?)\s*%?")
    if pct and (v := _num(pct)) is not None and 0 <= v <= 100:
        put("marks_percent", v, 0.8)
    else:
        mo = re.search(r"(?i)marks obtained\s*[:\-]?\s*(\d{2,4})\s*(?:/|out of)\s*(\d{2,4})", text)
        if mo and int(mo.group(2)) > 0:
            put("marks_percent", round(100 * int(mo.group(1)) / int(mo.group(2)), 2), 0.75)
    if re.search(r"(?i)class\s*(?:xii|12)|higher secondary|senior secondary|intermediate|कक्षा\s*12", text):
        put("education_level", "higher_secondary", 0.75)
    elif re.search(r"(?i)class\s*(?:x|10)\b|secondary school|high school|matriculation|कक्षा\s*10", text):
        put("education_level", "secondary", 0.75)
    elif re.search(r"(?i)bachelor|b\.?a\.?|b\.?sc|b\.?com|graduat", text):
        put("education_level", "graduate", 0.6)

    # bank
    if re.search(r"(?i)passbook|savings account|ifsc|बचत खाता", text):
        put("has_bank_account", True, 0.9)
        acct = ACCOUNT_RE.search(text) or re.search(r"(?i)a/?c\s*(?:no\.?)?\s*[:\-]?\s*X+(\d{4})", text)
        if acct:
            put("bank_account_last4", re.sub(r"\D", "", acct.group(acct.lastindex or 1))[-4:], 0.85)

    # land
    land = re.search(r"(?i)(?:total )?(?:area|land holding|रकबा|क्षेत्रफल)\s*[:\-]?\s*([\d.]+)\s*(hectares?|ha\b|acres?|हेक्टेयर|एकड़)",
                     text)
    if land and (v := _num(land.group(1))) is not None:
        unit = land.group(2).lower()
        ha = v * 0.404686 if unit.startswith(("acre", "एकड़")) else v
        put("land_holding_hectares", round(ha, 3), 0.8)
    if doc_type == "land_record" and re.search(r"(?i)khatauni|khasra|cultivat|किसान|खतौनी", text):
        put("occupation", "farmer", 0.5)

    if "issue_date" in wanted:
        issued = _label(text, r"date of issue|issue date|issued on|जारी करने की तिथि", r"([0-9/\-. ]{8,12})")
        if issued and (d := _parse_date(issued)):
            put("issue_date", d, 0.85)
    _ = low
    return out


LLM_SYSTEM = (
    "You extract fields from Indian government documents. The text came from OCR and may contain errors. "
    "Only report a field if it is clearly present in the text; never guess. ID numbers are already masked. "
    "Return JSON matching the schema; omit fields you cannot find."
)


def _llm_schema(fields: list[str]) -> dict[str, Any]:
    types = {
        "annual_income": {"type": "integer"}, "family_size": {"type": "integer"},
        "disability_percent": {"type": "integer"}, "marks_percent": {"type": "number"},
        "land_holding_hectares": {"type": "number"}, "is_bpl": {"type": "boolean"},
        "has_bank_account": {"type": "boolean"},
        "gender": {"type": "string", "enum": ["male", "female", "transgender"]},
        "category": {"type": "string", "enum": ["general", "obc", "sc", "st", "ews"]},
        "education_level": {"type": "string", "enum": ["none", "primary", "secondary", "higher_secondary",
                                                       "graduate", "postgraduate"]},
        "date_of_birth": {"type": "string", "description": "YYYY-MM-DD"},
        "issue_date": {"type": "string", "description": "YYYY-MM-DD"},
    }
    props = {f: types.get(f, {"type": "string"}) for f in fields if f not in ("aadhaar_last4", "bank_account_last4")}
    return {"type": "object", "properties": props, "additionalProperties": False}


async def llm_fields(masked_text: str, doc_type: str) -> tuple[dict[str, Any], str, bool]:
    fields = DOC_FIELDS.get(doc_type, GENERIC_FIELDS)
    res = await get_llm().complete_json(
        LLM_SYSTEM, f"Document type: {doc_type}\n\n<document_text>\n{masked_text[:12000]}\n</document_text>",
        _llm_schema(fields), max_tokens=1500)
    return ({k: v for k, v in res.data.items() if k in fields and v not in (None, "")} if not res.error else {},
            res.model, res.is_mock)


@dataclass
class Extraction:
    doc_type: str
    fields: dict[str, dict[str, Any]]
    method: str
    ocr_available: bool
    llm_model: str
    llm_is_mock: bool
    warnings: list[str]
    pages: int

    def to_dict(self) -> dict[str, Any]:
        return {"doc_type": self.doc_type, "fields": self.fields, "method": self.method,
                "ocr_available": self.ocr_available, "llm_model": self.llm_model, "llm_is_mock": self.llm_is_mock,
                "warnings": self.warnings, "pages": self.pages}


async def extract(data: bytes, content_type: str, doc_type: str) -> Extraction:
    tr = extract_text(data, content_type)
    masked = mask_ids(tr.text)
    found = parse_fields(tr.text, doc_type)  # pattern parser sees the raw text but only keeps last-4 of IDs
    warnings = list(tr.warnings)
    model, is_mock = "none", True
    if masked.strip():
        llm_data, model, is_mock = await llm_fields(masked, doc_type)
        for name, value in llm_data.items():
            if name in found:
                if str(found[name]["value"]).lower() == str(value).lower():
                    found[name]["confidence"] = min(0.98, found[name]["confidence"] + 0.08)
                    found[name]["source"] = "pattern+llm"
                else:
                    found[name]["alternative"] = value
                    found[name]["confidence"] = round(found[name]["confidence"] * 0.7, 2)
                    warnings.append(f"disagreement:{name}")
            else:
                found[name] = {"value": value, "confidence": 0.5, "source": "llm"}
    elif tr.method != "none":
        warnings.append("no_text_found")
    if "date_of_birth" in found and (age := age_from_dob(str(found["date_of_birth"]["value"]))) is not None:
        found["age"] = {"value": age, "confidence": found["date_of_birth"]["confidence"], "source": "derived"}
    if contains_full_aadhaar(tr.text):
        warnings.append("aadhaar_masked")
    return Extraction(doc_type, found, tr.method, tr.ocr_available, model, is_mock, warnings, tr.pages)
