"""Step 3: profile, consent, encrypted document upload, extraction, review and deletion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai import extraction, llm
from app.seed import seed
from scripts.make_sample_docs import DOCS, make_pdf
from tests.conftest import ADMIN, OFFICER, USER, bearer, login

SAMPLES: dict[str, bytes] = {}


@pytest.fixture(autouse=True)
def _storage(settings, tmp_path):
    settings.file_storage_dir = str(tmp_path / "files")
    llm.set_llm(llm.MockLLM())
    yield
    llm.set_llm(None)


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> dict[str, bytes]:
    d = tmp_path_factory.mktemp("samples")
    for name, (title, lines) in DOCS.items():
        make_pdf(d / name, title, lines)
        SAMPLES[name] = (d / name).read_bytes()
    return SAMPLES


@pytest.fixture
async def user_tokens(client, db, accounts):
    await db.users.update_one({"login_id": USER["login_id"]},
                              {"$set": {"consent": {"mode": "save_profile", "accepted_at": "x", "version": "t"}}})
    return await login(client, USER, "user")


async def _upload(client, tokens, samples, name, doc_type):
    return await client.post("/api/documents", headers=bearer(tokens), data={"doc_type": doc_type},
                             files={"file": (name, samples[name], "application/pdf")})


# ----------------------------------------------------------------------------- extraction units

def test_mask_ids():
    assert extraction.mask_ids("Aadhaar 0123 4567 8901 ok") == "Aadhaar XXXX XXXX 8901 ok"
    assert extraction.mask_ids("A/c No: 001234567890") == "A/c No: XXXXXXXX7890"
    assert "1234" not in extraction.mask_ids("VID 1234 5678 9012 3456")[:8]


def test_sniff_type():
    assert extraction.sniff_type(b"%PDF-1.7 ...") == "application/pdf"
    assert extraction.sniff_type(b"\x89PNG\r\n\x1a\nxxxx") == "image/png"
    assert extraction.sniff_type(b"MZ\x90\x00") is None  # an .exe renamed to .pdf is refused


def test_parse_hindi_labels():
    text = "नाम: सुनीता देवी\nजन्म तिथि: 12/03/1958\nमहिला\nवार्षिक आय: ₹ 72,000\nअनुसूचित जाति"
    f = extraction.parse_fields(text, "other")
    assert f["date_of_birth"]["value"] == "1958-03-12"
    assert f["gender"]["value"] == "female"
    assert f["annual_income"]["value"] == 72000
    assert f["category"]["value"] == "sc"


def test_land_acres_converted_to_hectares():
    f = extraction.parse_fields("Total Area: 5 acres", "land_record")
    assert f["land_holding_hectares"]["value"] == pytest.approx(2.023, abs=0.01)


class FakeLLM:
    name = "fake"

    def __init__(self, data):
        self.data, self.prompts = data, []

    async def complete(self, system, prompt, *, max_tokens=2000):
        return llm.LLMResult(text="", model="fake", is_mock=False)

    async def complete_json(self, system, prompt, schema, *, max_tokens=2000):
        self.prompts.append(prompt)
        return llm.LLMResult(data=self.data, model="fake-model", is_mock=False)


async def test_llm_agreement_raises_confidence_and_never_sees_full_aadhaar(samples):
    fake = FakeLLM({"full_name": "Sunita Devi", "gender": "male", "state": "Bihar"})
    llm.set_llm(fake)
    ex = await extraction.extract(samples["aadhaar_sunita.pdf"], "application/pdf", "aadhaar")
    assert ex.fields["full_name"]["source"] == "pattern+llm" and ex.fields["full_name"]["confidence"] > 0.85
    assert ex.fields["gender"]["value"] == "female" and ex.fields["gender"]["alternative"] == "male"
    assert "disagreement:gender" in ex.warnings
    assert "0123 4567 8901" not in fake.prompts[0] and "XXXX XXXX 8901" in fake.prompts[0]
    assert ex.llm_model == "fake-model" and not ex.llm_is_mock

# ----------------------------------------------------------------------------- upload + storage


async def test_upload_extracts_and_encrypts(client, db, user_tokens, samples, settings):
    r = await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "aadhaar")
    assert r.status_code == 201, r.text
    doc = r.json()
    f = doc["extracted"]["fields"]
    assert f["aadhaar_last4"]["value"] == "8901" and f["age"]["value"] >= 68
    assert doc["status"] == "needs_review" and doc["extracted"]["llm_is_mock"] is True
    stored = list(Path(settings.file_storage_dir).glob("*.enc"))
    assert len(stored) == 1 and not stored[0].read_bytes().startswith(b"%PDF")  # encrypted at rest
    # the owner gets the original back
    back = await client.get(f"/api/documents/{doc['id']}/file", headers=bearer(user_tokens))
    assert back.content == samples["aadhaar_sunita.pdf"]


async def test_full_aadhaar_never_stored(client, db, user_tokens, samples):
    r = await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "aadhaar")
    await client.post(f"/api/documents/{r.json()['id']}/confirm", headers=bearer(user_tokens),
                      json={"fields": {k: v["value"] for k, v in r.json()["extracted"]["fields"].items()}})
    for coll in ("documents", "profiles", "users", "audit_log"):
        dump = json.dumps([d async for d in db[coll].find()], default=str)
        assert "012345678901" not in dump.replace(" ", "") , coll


@pytest.mark.parametrize("payload,ctype,code", [
    (b"just some text", "text/plain", 415),
    (b"MZ\x90\x00binary", "application/pdf", 415),
    (b"", "application/pdf", 422),
])
async def test_upload_rejects_bad_files(client, user_tokens, payload, ctype, code):
    r = await client.post("/api/documents", headers=bearer(user_tokens), data={"doc_type": "aadhaar"},
                          files={"file": ("x.pdf", payload, ctype)})
    assert r.status_code == code, r.text


async def test_upload_size_limit(client, user_tokens, settings, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    r = await client.post("/api/documents", headers=bearer(user_tokens), data={"doc_type": "other"},
                          files={"file": ("big.pdf", b"%PDF-" + b"0" * (1024 * 1024 + 10), "application/pdf")})
    assert r.status_code == 413


async def test_unknown_doc_type_rejected(client, user_tokens, samples):
    r = await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "passport_of_mars")
    assert r.status_code == 422


async def test_documents_are_private(client, db, accounts, user_tokens, samples):
    doc = (await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "aadhaar")).json()
    other = {"login_id": "other@test.in", "full_name": "Other", "password": "0ther!Password",
             "consent_accepted": True}
    await client.post("/api/auth/register", json=other)
    t2 = await login(client, other, "user")
    assert (await client.get(f"/api/documents/{doc['id']}", headers=bearer(t2))).status_code == 404
    assert (await client.get(f"/api/documents/{doc['id']}/file", headers=bearer(t2))).status_code == 404
    assert (await client.delete(f"/api/documents/{doc['id']}", headers=bearer(t2))).status_code == 404
    for acct, portal in ((OFFICER, "officer"), (ADMIN, "admin")):
        t = await login(client, acct, portal)
        assert (await client.get("/api/documents", headers=bearer(t))).status_code == 403
    # same file from another account is flagged for the admin's review
    d2 = await _upload(client, t2, samples, "aadhaar_sunita.pdf", "aadhaar")
    assert (await db.documents.find_one({"_id": d2.json()["id"]}))["duplicate_across_accounts"] is True


async def test_confirm_updates_profile_with_source(client, user_tokens, samples):
    doc = (await _upload(client, user_tokens, samples, "income_certificate_sunita.pdf", "income_certificate")).json()
    fields = {k: v["value"] for k, v in doc["extracted"]["fields"].items()}
    fields["annual_income"] = 58000  # the user corrects a value
    r = await client.post(f"/api/documents/{doc['id']}/confirm", headers=bearer(user_tokens), json={"fields": fields})
    assert r.status_code == 200, r.text
    prof = r.json()["profile"]
    assert prof["eligibility"]["annual_income"] == 58000 and prof["eligibility"]["state"] == "Bihar"
    assert prof["personal"]["district"] == "Gaya"
    assert prof["sources"]["annual_income"] == f"document:{doc['id']}"
    assert r.json()["document"]["status"] == "confirmed"


async def test_confirm_rejects_bad_values_and_unknown_fields(client, user_tokens, samples):
    doc = (await _upload(client, user_tokens, samples, "income_certificate_sunita.pdf", "income_certificate")).json()
    r = await client.post(f"/api/documents/{doc['id']}/confirm", headers=bearer(user_tokens),
                          json={"fields": {"annual_income": -5}})
    assert r.status_code == 422
    r = await client.post(f"/api/documents/{doc['id']}/confirm", headers=bearer(user_tokens),
                          json={"fields": {"role": "admin"}})
    assert r.status_code == 422


async def test_replace_and_delete(client, user_tokens, samples, settings):
    doc = (await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "aadhaar")).json()
    r = await client.put(f"/api/documents/{doc['id']}/file", headers=bearer(user_tokens),
                         files={"file": ("new.pdf", samples["ration_card_sunita.pdf"], "application/pdf")})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert len(list(Path(settings.file_storage_dir).glob("*.enc"))) == 1  # old blob removed
    assert (await client.delete(f"/api/documents/{doc['id']}", headers=bearer(user_tokens))).status_code == 204
    assert list(Path(settings.file_storage_dir).glob("*.enc")) == []
    assert (await client.get("/api/documents", headers=bearer(user_tokens))).json() == []

# ----------------------------------------------------------------------------- consent + profile


async def test_profile_round_trip_and_completeness(client, user_tokens):
    body = {"eligibility": {"age": 40, "state": "Bihar", "gender": "female"},
            "personal": {"full_name": "Test", "aadhaar_last4": "1234"}}
    r = await client.put("/api/me/profile", headers=bearer(user_tokens), json=body)
    assert r.status_code == 200 and r.json()["completeness"] == round(100 * 3 / 14)
    assert r.json()["sources"]["age"] == "manual"


@pytest.mark.parametrize("personal", [{"aadhaar_last4": "123456789012"}, {"address": "UID 2345 6789 0123, Patna"}])
async def test_profile_refuses_full_aadhaar(client, user_tokens, personal):
    r = await client.put("/api/me/profile", headers=bearer(user_tokens), json={"personal": personal})
    assert r.status_code == 422


async def test_use_once_stores_nothing(client, db, user_tokens, samples, settings):
    assert (await client.put("/api/me/consent", headers=bearer(user_tokens),
                             json={"mode": "use_once", "accepted": True})).status_code == 200
    assert (await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "aadhaar")).status_code == 409
    assert (await client.put("/api/me/profile", headers=bearer(user_tokens), json={})).status_code == 409
    r = await client.post("/api/extract", headers=bearer(user_tokens), data={"doc_type": "aadhaar"},
                          files={"file": ("a.pdf", samples["aadhaar_sunita.pdf"], "application/pdf")})
    assert r.status_code == 200 and r.json()["fields"]["aadhaar_last4"]["value"] == "8901"
    assert await db.documents.count_documents({}) == 0
    assert not Path(settings.file_storage_dir).exists() or not list(Path(settings.file_storage_dir).glob("*"))


async def test_withdraw_consent_purges(client, db, user_tokens, samples, settings):
    await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "aadhaar")
    await client.put("/api/me/profile", headers=bearer(user_tokens), json={"eligibility": {"age": 50}})
    r = await client.post("/api/me/consent/withdraw", headers=bearer(user_tokens))
    assert r.status_code == 200 and r.json()["mode"] == "use_once"
    assert await db.profiles.count_documents({}) == 0
    assert list(Path(settings.file_storage_dir).glob("*.enc")) == []
    assert (await client.get("/api/documents", headers=bearer(user_tokens))).json() == []


async def test_saved_schemes_and_checklist(client, db, user_tokens, samples):
    await seed(db)
    assert (await client.post("/api/me/saved-schemes/ignoaps", headers=bearer(user_tokens))).status_code == 200
    assert (await client.post("/api/me/saved-schemes/nope", headers=bearer(user_tokens))).status_code == 404
    await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "aadhaar")
    cl = (await client.get("/api/me/checklist", headers=bearer(user_tokens))).json()
    assert cl[0]["slug"] == "ignoaps"
    have = {d["id"]: d["have"] for d in cl[0]["documents"]}
    assert have.get("aadhaar") is True and cl[0]["done"] >= 1


async def test_delete_my_account(client, db, user_tokens, samples, settings):
    await _upload(client, user_tokens, samples, "aadhaar_sunita.pdf", "aadhaar")
    r = await client.post("/api/me/delete-account", headers=bearer(user_tokens),
                          json={"password": "wrong", "confirm": "DELETE"})
    assert r.status_code == 401
    r = await client.post("/api/me/delete-account", headers=bearer(user_tokens),
                          json={"password": USER["password"], "confirm": "DELETE"})
    assert r.status_code == 204
    assert (await client.post("/api/auth/login", json={"login_id": USER["login_id"], "password": USER["password"],
                                                       "portal": "user"})).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(user_tokens))).status_code == 401
    u = await db.users.find_one({"role": "user"})
    assert u["deleted_at"] is not None and u["full_name"] == "" and u["login_id"].startswith("deleted-")
    assert list(Path(settings.file_storage_dir).glob("*.enc")) == []
    assert await db.audit_log.find_one({"action": "user.self_delete"})
