"""Scam message checker (spec 3.9).

score = 0.5 x classifier probability + 0.5 x red-flag score
* Classifier: TF-IDF character n-grams (works for Hindi and English alike) + logistic regression,
  trained at first use on app/data/scam_messages.json (synthetic, labelled). Evaluated on a separate
  held-out set by the evaluation dashboard.
* Red flags: explicit patterns (fee or payment request, OTP/PIN/password, urgency, unofficial or
  shortened links, prize/lottery, requests for bank details, call-this-number).
* Scheme lookup: the retrieval index finds which real scheme the message claims to be about, so the
  verdict can say "this looks like Scheme X; real applications never ask for a fee".
The verdict is never "safe": the best outcome is "looks consistent with a real scheme - still verify".
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.ai.extraction import mask_ids
from app.services.schemes import DATA_DIR

log = logging.getLogger(__name__)
MODEL_VERSION = "scam-tfidf-logreg-v1"

FLAGS: list[tuple[str, float, re.Pattern[str], dict[str, str]]] = [
    ("fee_request", 0.45, re.compile(r"(?i)(registration|processing|service|activation|release|verification)\s*(fee|charge)|"
                                    r"pay\s*(rs\.?|₹|inr)?\s*\d+|send\s*(rs\.?|₹)\s*\d+|(?<!निः)(?<!नि:)शुल्क|फीस|"
                                    r"रुपये\s*(जमा करें|भेजें|भेजे)|₹\s*\d+\s*(जमा करें|भेज)"),
     {"en": "Asks you to pay money. Government schemes do not charge a fee to apply or to release benefits.",
      "hi": "पैसे माँगे जा रहे हैं। सरकारी योजनाएँ आवेदन या लाभ जारी करने के लिए शुल्क नहीं लेतीं।"}),
    ("otp_request", 0.6, re.compile(r"(?i)\botp\b|\bpin\b|password|cvv|upi\s*pin|ओटीपी|पिन|पासवर्ड|"
                                    r"\d[- ]digit code|(verification|security) code|code (we|that was) (just )?sent|"
                                    r"(share|send|tell)[^.]{0,20}\bcode\b|कोड\s*(बताएँ|बताएं|भेजें|शेयर)"),
     {"en": "Asks for an OTP, PIN or password. No official scheme ever asks for these.",
      "hi": "OTP, PIN या पासवर्ड माँगा गया है। कोई सरकारी योजना ये कभी नहीं माँगती।"}),
    ("urgency", 0.2, re.compile(r"(?i)today only|last (date|chance) (is )?today|within \d+ (hours|hrs)|immediately|urgent|"
                                r"hurry|expires? (today|soon)|आज ही|तुरंत|जल्दी|अंतिम मौका|\d+\s*घंटे"),
     {"en": "Creates urgency to make you act without checking.", "hi": "बिना जाँचे जल्दी करने का दबाव बनाया गया है।"}),
    ("unofficial_link", 0.35, re.compile(r"(?i)\b(bit\.ly|tinyurl|t\.co|wa\.me|goo\.gl|cutt\.ly|is\.gd)\b|"
                                         r"https?://(?![\w.-]*\.(gov\.in|nic\.in)\b)[\w.-]+"),
     {"en": "Contains a link that is not an official .gov.in / .nic.in site.",
      "hi": "इसमें ऐसा लिंक है जो आधिकारिक .gov.in / .nic.in साइट नहीं है।"}),
    ("prize", 0.35, re.compile(r"(?i)lottery|lucky draw|you (have )?won|prize|winner|congratulations.*(selected|won)|"
                               r"लॉटरी|इनाम|जीत(ा|े) है|बधाई"),
     {"en": "Talks about a prize or lucky draw. Schemes are not lotteries.", "hi": "इनाम या लकी ड्रॉ की बात है। योजनाएँ लॉटरी नहीं होतीं।"}),
    ("bank_details", 0.35, re.compile(r"(?i)(send|share|give|update).{0,20}(account number|bank details|atm|debit card|card number)|"
                                      r"खाता संख्या.{0,15}(भेजें|बताएँ)|बैंक.{0,10}(विवरण|डिटेल).{0,15}(भेजें|दें)"),
     {"en": "Asks you to share bank or card details.", "hi": "बैंक या कार्ड का विवरण माँगा गया है।"}),
    ("call_number", 0.15, re.compile(r"(?i)(call|whatsapp|contact|कॉल करें|संपर्क करें)\D{0,15}(\+?91[\s-]?)?[6-9]\d{9}"),
     {"en": "Asks you to call or WhatsApp a private mobile number.", "hi": "किसी निजी मोबाइल नंबर पर कॉल या व्हाट्सऐप करने को कहा गया है।"}),
    ("free_for_all", 0.2, re.compile(r"(?i)(all|every|सभी|हर)\s+(women|girls|students|farmers|महिलाओं|छात्रों|किसानों)[^.]{0,40}"
                                     r"(free|₹|rs\.?|मुफ्त|मुफ़्त)"),
     {"en": "Promises money or free items to everyone, which real schemes don't do.",
      "hi": "सबको पैसे या मुफ़्त सामान का वादा है, असली योजनाएँ ऐसा नहीं करतीं।"}),
]

NO_FEE = re.compile(r"(?i)\bno\b[^.]{0,25}\b(fee|charge)s?\b|free of (cost|charge)|without any (fee|charge)|"
                    r"निःशुल्क|नि:शुल्क|कोई शुल्क नहीं|कोई फीस नहीं|मुफ़्त जाँच")

TIPS = {
    "en": ["Do not pay any fee and never share an OTP, PIN or password.",
           "Check the scheme on its official portal (ending in .gov.in or .nic.in) or at your Gram Panchayat / CSC.",
           "Report fraud at cybercrime.gov.in or call the national cyber-crime helpline 1930.",
           "Forwarding the message spreads the scam; delete it instead."],
    "hi": ["कोई शुल्क न दें और OTP, PIN या पासवर्ड कभी साझा न करें।",
           "योजना को उसके आधिकारिक पोर्टल (.gov.in या .nic.in) या ग्राम पंचायत / CSC पर जाँचें।",
           "धोखाधड़ी की शिकायत cybercrime.gov.in पर करें या राष्ट्रीय साइबर अपराध हेल्पलाइन 1930 पर कॉल करें।",
           "संदेश आगे भेजने से धोखा फैलता है; इसे मिटा दें।"],
}

_model: Any = None
_lock = threading.Lock()


def _load(name: str) -> list[dict[str, Any]]:
    path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def model() -> Any:
    global _model
    with _lock:
        if _model is None:
            rows = _load("scam_messages.json")
            if len(rows) < 10:
                _model = False
                log.warning("scam classifier: training data missing; using red-flag rules only")
            else:
                from sklearn.feature_extraction.text import TfidfVectorizer
                from sklearn.linear_model import LogisticRegression
                from sklearn.pipeline import make_pipeline

                _model = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1, sublinear_tf=True),
                                       LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced"))
                _model.fit([r["text"] for r in rows], [1 if r["label"] == "scam" else 0 for r in rows])
                log.info("scam classifier trained on %d messages", len(rows))
        return _model


def classify(text: str) -> dict[str, Any]:
    flags = [{"code": c, "weight": w, **msg} for c, w, rx, msg in FLAGS if rx.search(text)]
    if NO_FEE.search(text) and not re.search(r"(?i)pay\s*(rs\.?|₹)|₹\s*\d+\s*(जमा करें|भेज)", text):
        flags = [f for f in flags if f["code"] != "fee_request"]  # "no fee is charged" is not a fee request
    flag_score = min(1.0, sum(f["weight"] for f in flags))
    m = model()
    prob = float(m.predict_proba([text])[0][1]) if m else None
    score = 0.5 * prob + 0.5 * flag_score if prob is not None else flag_score
    # A request for OTP/PIN or payment is decisive on its own.
    if any(f["code"] in ("otp_request", "fee_request") for f in flags):
        score = max(score, 0.75)
    verdict = "likely_scam" if score >= 0.7 else "suspicious" if score >= 0.4 else "consistent"
    return {"verdict": verdict, "score": round(score, 3), "classifier_probability": None if prob is None else round(prob, 3),
            "flag_score": round(flag_score, 3), "flags": flags, "model": MODEL_VERSION if m else "rules-only"}


async def check(db: AsyncIOMotorDatabase, text: str) -> dict[str, Any]:
    clean = mask_ids(text.strip())[:3000]
    out = classify(clean)
    match = None
    try:
        from app.ai import index
        from app.ai.embeddings import get_embedder

        hits = await index.search(db, clean, k=3)
        if hits and hits[0].meta.get("vector_score", 0) >= get_embedder().abstain_below and hits[0].meta.get("slug"):
            h = hits[0]
            match = {"slug": h.meta["slug"], "name": {"en": h.meta["title_en"], "hi": h.meta["title_hi"]},
                     "official_url": h.meta["url"], "similarity": round(h.meta["vector_score"], 3)}
    except Exception:  # noqa: BLE001 - index not ready; the checker still works without it
        log.warning("scam check: scheme lookup unavailable")
    if match:
        out["match"] = match
        out["match_note"] = {
            "en": f"This message seems to be about {match['name']['en']}. Apply only on the official portal "
                  f"({match['official_url']}); real applications never ask for a fee, OTP or PIN.",
            "hi": f"यह संदेश {match['name']['hi']} के बारे में लगता है। केवल आधिकारिक पोर्टल ({match['official_url']}) "
                  f"पर आवेदन करें; असली आवेदन में कभी शुल्क, OTP या PIN नहीं माँगा जाता।"}
    out["tips"] = TIPS
    out["report"] = {"portal": "https://cybercrime.gov.in", "helpline": "1930"}
    return out
