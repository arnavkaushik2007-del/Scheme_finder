"""Fill a running Scheme Finder with demo activity (synthetic people and documents only).

    python -m scripts.make_sample_docs      # once, creates sample_documents/
    python -m scripts.demo_activity         # against http://127.0.0.1:8080 (or pass a base URL)

Creates: Sunita's profile, 3 uploaded documents (2 reviewed), a saved scheme, 2 submitted
applications + 1 draft, one assistant question, and an officer "more documents needed" decision.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
DOCS = Path(__file__).resolve().parent.parent / "sample_documents"


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=180)

    def login(login_id: str, pw: str, portal: str = "user") -> dict[str, str]:
        r = c.post("/api/auth/login", json={"login_id": login_id, "password": pw, "portal": portal})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    if not (DOCS / "aadhaar_sunita.pdf").exists():
        raise SystemExit("Run `python -m scripts.make_sample_docs` first.")
    h = login("sunita@demo.test", "Nagrik#Demo2026")
    if c.get("/api/applications", headers=h).json():
        print("demo activity already present; nothing to do")
        return
    c.put("/api/me/profile", headers=h, json={"eligibility": {
        "age": 68, "gender": "female", "state": "Bihar", "residence": "rural", "is_bpl": True,
        "marital_status": "widowed", "annual_income": 60000, "has_bank_account": True, "category": "obc",
        "occupation": "homemaker", "family_size": 3},
        "personal": {"full_name": "Sunita Devi", "date_of_birth": "1958-03-12", "district": "Gaya",
                     "aadhaar_last4": "8901"}})
    ids = []
    for name, doc_type in (("aadhaar_sunita.pdf", "aadhaar"), ("ration_card_sunita.pdf", "ration_card"),
                           ("income_certificate_sunita.pdf", "income_certificate")):
        d = c.post("/api/documents", headers=h, data={"doc_type": doc_type},
                   files={"file": (name, (DOCS / name).read_bytes(), "application/pdf")}).json()
        ids.append(d["id"])
        if doc_type != "income_certificate":
            c.post(f"/api/documents/{d['id']}/confirm", headers=h,
                   json={"fields": {k: v["value"] for k, v in d["extracted"]["fields"].items()}})
    c.post("/api/me/saved-schemes/ignwps", headers=h)
    prof = c.get("/api/me/profile", headers=h).json()
    form = {"eligibility": prof["eligibility"], "personal": prof["personal"]}
    apps = []
    for slug in ("ignwps", "ayushman-bharat-pmjay"):
        a = c.post("/api/applications", headers=h, json={"scheme": slug, "form": form, "documents": ids[:2]}).json()
        c.post(f"/api/applications/{a['id']}/submit", headers=h, json={"declaration": True})
        apps.append(a["id"])
    c.post("/api/applications", headers=h, json={"scheme": "pm-ujjwala-yojana", "form": form, "documents": ids[:1]})
    c.post("/api/ask", headers=h, json={"question": "वृद्धावस्था पेंशन के लिए कौन से दस्तावेज़ चाहिए?"})

    o = login("officer.patna@schemefinder.test", "Review#Desk2026", "officer")
    c.post(f"/api/officer/applications/{apps[0]}/start-review", headers=o)
    c.post(f"/api/officer/applications/{apps[0]}/decision", headers=o, json={
        "action": "request_docs", "reason": "Please upload your husband's death certificate.",
        "requested_documents": ["husband_death_certificate"]})
    print(f"demo activity ready ({len(apps)} submitted applications)")


if __name__ == "__main__":
    main()
