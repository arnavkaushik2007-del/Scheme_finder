# Scheme Finder (योजना खोजक)

> **Prototype, not an official government service.** Always verify on the official portal.

A Hindi/English assistant that tells people which government schemes they likely qualify for, why,
what is missing, and which documents to bring. It has three roles with separate logins: **citizen**,
**officer** and **admin**.

The source of truth is *Scheme Finder — Website Structure and Feature Map* (section 0 overrides the
rest). The app is built in the order given in its section 0.10.

| Build step | Scope | Status |
|---|---|---|
| 1 | Authentication, three roles, role checks, seeded admin | ✅ done |
| 2 | Scheme data, structured rules, eligibility engine | ✅ done |
| 3 | User profile, document upload, field extraction | ✅ done |
| 4 | Recommendations + assistant chat with hallucination guard | ✅ done |
| 5 | Application form, submission, status tracking | ✅ done |
| 6 | Officer dashboard, AI verification report, decision panel | ✅ done |
| 7 | Admin console (officers, schemes & rules, users, database, audit) | ✅ done |
| 8 | Remaining AI features and the evaluation dashboard | ✅ done |

---

## Quick start

### Option A: Docker (MongoDB included)

```bash
cp .env.example .env        # then set JWT_SECRET and STORAGE_SECRET
docker compose up --build
```

Open <http://localhost:8080>. With `SEED_ON_STARTUP=true` (the default in `.env.example`), the demo
accounts and the 30 schemes are created the first time the app starts on an empty database. You can
also seed by hand:

```bash
docker compose exec app python -m scripts.seed
```

### Option B: Local Python, no MongoDB needed

```bash
python -m pip install -r requirements-dev.txt
# .env:  APP_ENV=dev  MONGO_URL=mongomock://  SEED_ON_STARTUP=true
python -m uvicorn app.server:app --port 8080
```

`mongomock://` is an **in-memory** database, so everything resets when the app stops. Use it only to
try the app or run tests. For a real MongoDB, set `MONGO_URL=mongodb://localhost:27017` and run
`python -m scripts.seed`.

### Tests

```bash
python -m pytest -q
```

The tests run against an in-memory MongoDB. There are 192 of them. They use the built-in hash
embedder and an in-memory vector store so they don't download a model. By build step they cover:

- **Steps 1–2**: the rules engine, auth, lockout, rate limits, token rotation, role checks on every
  admin endpoint, scheme versioning and the audit hash-chain.
- **Step 3**: encrypted uploads, extraction, Aadhaar masking, consent modes, document privacy and
  "delete my data".
- **Step 4**: retrieval, citations, the hallucination guard, abstention, rules-engine answers,
  feedback and "what's missing".
- **Step 5**: the full application lifecycle, refused transitions, withdrawal, resubmission, version
  pinning, assignment and notifications.
- **Step 6**: the officer queue, access to assigned cases only, rule evidence, contradiction
  handling, decisions with AI agreement tracking, history and stats.
- **Step 7**: 2FA (including the RFC 6238 test vector), officer management, fraud detection, the
  masked database browser with logged reveals, rule ingestion and review, and audit filters.
- **Step 8**: the interview, what-if, the planner and PDF, the scam checker, the change tracker and
  affected-user notices, the voice flag, a full evaluation run, and a check for duplicate
  translation keys.

---

## Demo credentials (synthetic, demo only)

| Role | Login page | Login ID | Password |
|---|---|---|---|
| Admin | `/admin/login` | `admin@schemefinder.test` | `Yojana#Root2026` |
| Officer (Uttar Pradesh) | `/officer/login` | `officer.lucknow@schemefinder.test` | `Review#Desk2026` |
| Officer (Bihar) | `/officer/login` | `officer.patna@schemefinder.test` | `Review#Desk2026` |
| Citizen (Hindi UI) | `/login` | `sunita@demo.test` | `Nagrik#Demo2026` |
| Citizen (Hindi UI) | `/login` | `ramesh@demo.test` | `Nagrik#Demo2026` |
| Citizen (English UI) | `/login` | `priya@demo.test` | `Nagrik#Demo2026` |

All people are fictional. Change or remove these accounts before showing the app to anyone outside
your machine.

---

## What works now (all 8 steps)

### Pages

| Page | Who | What it does |
|---|---|---|
| `/` | everyone | Landing page: hero, live scheme count, last-reviewed date, "How it works", scheme grid |
| `/about` | everyone | Disclaimer, plain-language privacy, how decisions are made, help, data sources |
| `/login`, `/register` | citizens | Login; registration with a plain-language consent notice ("save my profile" or "use once") |
| `/forgot-password`, `/reset-password` | citizens | Single-use reset code (see Assumptions) |
| `/officer/login` | officers | Separate officer portal (not linked from the public site) |
| `/admin/login` | admin | Separate admin portal (not linked from the public site) |
| `/dashboard` | citizen | Shortcuts, account details, consent choice, change password |
| `/check` | citizen | Fixed-form eligibility check that calls `/api/match`. Results are ranked and show the rule-by-rule reasons (matched / failed / unknown, with your value as evidence). Filter by category, central/state and benefit type, or hide schemes you're not eligible for |
| `/explore` | any signed-in role | Keyword search in Hindi or English, with filters for category, level, benefit type and beneficiary group |
| `/scheme/{slug}` | any signed-in role | Summary, benefit, rule checklist (checked against your `/check` answers if you have any), documents with where to get them, how to apply, official portal, last-reviewed date, rules version, "cannot combine with" |
| `/start` | citizen | Consent screen: what is stored, why, for how long; "save my profile" or "use once"; withdraw consent (deletes stored data). Shown after the first login if no choice was made |
| `/me` | citizen | Saved profile (all 24 eligibility answers + name, date of birth, Aadhaar last 4, district, address), completeness meter, "from document" markers, saved schemes with a document checklist, **delete my account and data** |
| `/documents` | citizen | Upload (PDF/JPG/PNG/WEBP, 5 MB), fields found with a confidence bar each, **review & correct → copy into profile**, view, replace, delete. In "use once" mode files are read in memory and nothing is stored |
| `/recommendations` | citizen | Ranked list from the saved profile: status, relevance, the rule-by-rule reasons, **what's missing** ("income is ₹20,000 above the limit", "eligible in 3 years", "tell us your …"), filters, and an estimated yearly benefit from schemes that can be combined |
| `/assistant` | citizen, officer, admin | RAG chat in Hindi or English, scoped to all schemes or to one (`?scheme=slug`). Every answer has numbered citations, a sources panel, a confidence score, the answer mode, and 👍/👎. Low-confidence or unsupported answers are declined with the official-portal link. An "Assistant" button floats on every citizen and officer page, and each scheme page has "Ask about this scheme" |
| `/apply/{slug}` | citizen | Application form pre-filled from the profile (the fields this scheme's rules use are marked), the required-document checklist, attaching your uploaded documents, **AI pre-check**, save draft, a declaration, and submit |
| `/applications` | citizen | Every application with a status timeline (times in IST), officer remarks, the pre-check report, continue draft, **withdraw** before a decision, and **send more documents** when asked |
| 🔔 (header) | citizen | Notification bell with an unread count; every status change creates a notification |
| `/officer/dashboard` | officer | Review queue of assigned cases (oldest first), with filters for status, scheme and AI confidence, ageing chips (green < 3 days, amber < 7, red ≥ 7), counts per status, and unassigned cases they can take |
| `/officer/application/{id}` | officer | Applicant details (Aadhaar and bank account masked), each document viewer next to its extracted fields (✎ marks values the applicant corrected), status timeline, **AI verification report**, and the **decision panel**: start review, then approve / reject / request more documents with a mandatory reason. A warning shows when the officer disagrees with the AI |
| `/officer/history` | officer | Past decisions with reasons, the AI suggestion at the time, and agreed/overrode |
| `/officer/stats` | officer | Cases decided, average hours to decision, AI override rate, agreement by AI suggestion, open cases, change password |
| `/admin` | admin | Overview: live counts, recent audit entries with hash-chain status, and two-factor login setup |
| `/admin/officers` | admin | Add, edit (name, region, assigned schemes), suspend/activate, **reset password** (temporary password shown once; 2FA reset), delete (their open cases go back to the unassigned pool). Every action needs a reason |
| `/admin/schemes` | admin | All schemes with version and review date. The editor covers names, summaries and benefits (EN/HI), the portal URL, the benefit value and the **rules JSON**, with live validation and a plain-language preview; every save makes a new version. Version history with each version's rules; soft delete with a reason |
| `/admin/rules-review` | admin | **Ingestion tool**: upload a guideline PDF or paste text → the extracted rules wait in the queue with a diff against the current rules (added, changed, removed) and the exact source sentence for each rule. Approve (optionally after editing the JSON) to create a new version, or reject with a reason |
| `/admin/users` | admin | Search citizens (login IDs and names masked), filter to flagged accounts, **run the fake-account scan**, read why each account was flagged, suspend/activate, clear a flag, or delete a fake account (its data is purged and open applications withdrawn) |
| `/admin/database` | admin | Browse 14 collections with paging and search. Personal fields are masked; **Reveal** needs a reason and is written to the audit log. Password hashes, token hashes, file keys and 2FA secrets are never shown |
| `/admin/audit` | admin | The full audit log with filters (action prefix, actor role), before/after for each entry, and hash-chain verification |
| `/interview` | citizen | **Adaptive interview**: one question at a time, chosen to rule out the most schemes; live "N schemes still possible" count; "I don't know" on any question; 🎤 voice answers; "fill this from my certificate". At the end the answers go to the profile (or are used once) |
| `/planner` | citizen | **Benefit planner**: the best combination of schemes (respecting "cannot combine" pairs), a warning for each exclusion, an apply-first timeline (deadline, then documents ready), and a **one-page PDF** download |
| `/scam-check` | everyone | **Scam checker**: paste a message and get a verdict (likely scam / suspicious / looks consistent — still verify), a score, the red flags, which real scheme it pretends to be with that scheme's official portal, what to do, and where to report (cybercrime.gov.in, 1930). Messages are not stored |
| `/updates` | everyone | **What's new**: each rule or document change to a scheme, in plain language ("Age limit changed from 60 years to 65 years") |
| scheme pages | citizen | **What would make me eligible?** The smallest change that flips the result ("wait 3 years", "open a bank account"). Things that can't be changed are marked as such. `/recommendations` also lists schemes that are close to qualifying |
| `/assistant` | all | 🎤 speak your question (when voice is on) and 🔊 listen to the answer |
| `/admin/ai` | admin | **Evaluation dashboard**: run the evaluation, then see every metric, the errors behind it, past runs, model and prompt versions, and the low-confidence answer queue |
| `/admin/analytics` | admin | Applications by scheme with approval rates, status counts, officer–AI agreement, AI accuracy on definite suggestions, override rate per officer, and assistant statistics (questions, declined, 👍/👎) |

The prototype banner and the हिंदी/English switch appear on every page. The language choice is
remembered per browser, and a citizen's saved language preference is applied when they log in.

### API (interactive docs at `/docs`)

| Method | Path | Access |
|---|---|---|
| POST | `/api/auth/register` | public (citizens only; the role cannot be chosen) |
| POST | `/api/auth/login` | public. The body's `portal` must match the account's role |
| POST | `/api/auth/refresh`, `/api/auth/logout` | refresh token |
| GET | `/api/auth/me` | any role |
| POST | `/api/auth/change-password` | any role |
| POST | `/api/auth/password-reset/request`, `/confirm` | public (citizens) |
| GET | `/api/schemes`, `/api/schemes/{slug}`, `/api/schemes/stats`, `/api/schemes/vocabulary` | public (scheme info is public) |
| POST | `/api/match` | **citizen** |
| POST | `/api/schemes/{slug}/check` | any role |
| GET/POST | `/api/admin/officers` | **admin** |
| PATCH | `/api/admin/officers/{id}/status` | **admin** |
| POST/PUT/DELETE | `/api/admin/schemes[/{slug}]` | **admin**. Every edit creates a new version; delete is a soft delete |
| GET | `/api/admin/schemes/{slug}/versions` | **admin** |
| GET | `/api/admin/audit`, `/api/admin/stats` | **admin** |
| GET/PUT/DELETE | `/api/me/profile` | **citizen** (PUT needs "save my profile" consent) |
| PUT | `/api/me/consent`; POST `/api/me/consent/withdraw` | **citizen** |
| POST/DELETE | `/api/me/saved-schemes/{slug}`; GET `/api/me/checklist` | **citizen** |
| POST | `/api/me/delete-account` (password + `"confirm": "DELETE"`) | **citizen** |
| GET/POST | `/api/documents` (multipart `doc_type` + `file`) | **citizen**, own documents only |
| GET | `/api/documents/{id}`, `/api/documents/{id}/file` (decrypted) | **citizen**, owner only |
| PUT | `/api/documents/{id}/file` (replace) | **citizen**, owner only |
| POST | `/api/documents/{id}/confirm` (reviewed fields → profile) | **citizen**, owner only |
| DELETE | `/api/documents/{id}` | **citizen**, owner only |
| POST | `/api/extract` (in memory, nothing stored) | **citizen** |
| GET/POST | `/api/recommendations` (POST takes "use once" answers) | **citizen** |
| POST | `/api/ask` `{question, scheme?, profile?}` | any role |
| POST | `/api/ask/{id}/feedback` `{rating: up\|down}` | the person who asked |
| GET | `/api/assistant/status` (index, embedder, LLM mode) | any role |
| GET | `/api/search?q=` (hybrid keyword + embedding scheme search) | public |
| GET | `/api/admin/ai/queue` (declined answers + thumbs-down) | **admin** |
| GET/POST | `/api/applications` (POST creates a draft: `{scheme, form, documents}`) | **citizen**, own only |
| GET/PUT | `/api/applications/{id}` (PUT only while draft) | **citizen**, own only |
| POST | `/api/applications/{id}/precheck` | **citizen**, own only |
| POST | `/api/applications/{id}/submit` `{declaration: true}` | **citizen**, own only |
| POST | `/api/applications/{id}/withdraw`, `/resubmit` `{documents, note}` | **citizen**, own only |
| GET | `/api/notifications`; POST `/api/notifications/read` | **citizen** |
| GET | `/api/officer/queue?status=&scheme=&min_conf=&max_conf=` | **officer** |
| GET | `/api/officer/applications/{id}` (case + latest AI report) | **officer**, assigned cases only |
| POST | `/api/officer/applications/{id}/claim`, `/start-review`, `/report` (re-run), `/close` | **officer**, assigned cases only |
| GET | `/api/officer/applications/{id}/documents/{doc}/file` (audited) | **officer**, assigned cases only |
| POST | `/api/officer/applications/{id}/decision` `{action: approve\|reject\|request_docs, reason (10+ chars), requested_documents}` | **officer**, assigned cases only |
| GET | `/api/officer/history`, `/api/officer/stats` | **officer** |
| POST | `/api/auth/2fa/setup`, `/2fa/enable` `{code}`, `/2fa/disable` `{code}`; login takes `otp` | **officer, admin** |
| PATCH/DELETE | `/api/admin/officers/{id}` (`?reason=`); POST `/api/admin/officers/{id}/reset-password` | **admin** |
| GET | `/api/admin/users?q=&status=&flagged=`, `/api/admin/users/{id}` | **admin** |
| PATCH/DELETE | `/api/admin/users/{id}/status`, `/api/admin/users/{id}?reason=` | **admin** |
| POST | `/api/admin/fraud/scan`, `/api/admin/fraud/{user}/clear` | **admin** |
| GET | `/api/admin/database/{collection}?q=&skip=&limit=` (masked) | **admin** |
| POST | `/api/admin/database/{collection}/{id}/reveal` `{reason}` (audited) | **admin** |
| POST | `/api/admin/rules/ingest` (multipart `scheme` + `file` or `text`) | **admin** |
| GET/POST | `/api/admin/rules/proposals?status=`, `/api/admin/rules/proposals/{id}/decision` | **admin** |
| GET | `/api/admin/analytics`, `/api/admin/schemes-all`, `/api/admin/audit?action=&role=&target=` | **admin** |
| POST | `/api/interview/next` `{answers, skipped}` | **citizen** |
| POST | `/api/whatif` `{scheme?, profile?}` (no scheme → the closest schemes) | **citizen** |
| POST | `/api/plan` `{profile?, include_maybe}`; GET `/api/plan/pdf` | **citizen** |
| POST | `/api/scam-check` `{text}` | public (rate-limited) |
| GET | `/api/updates`, `/api/voice/status` | public |
| POST | `/api/stt` (multipart audio, ≤ 10 MB / 60 s) | any role; 503 when voice is off |
| POST | `/api/admin/ai/evaluate`; GET `/api/admin/ai/evaluations` | **admin** |
| GET | `/api/health` | public |

### Security

- **Passwords**: hashed with Argon2id. Strength rules: at least 10 characters, with upper case, lower
  case, a digit and a symbol; not a common password; must not contain the login ID.
- **Tokens**: JWT access tokens (15 min) and refresh tokens (7 days), with the role inside the token.
  Refresh tokens are stored by `jti`, rotated on every use and revoked on logout. If a revoked token
  is replayed, every session of that user is revoked.
- **Role checks**: every request re-reads the user from the database, so suspending an account or
  changing a role takes effect on the next request. Each endpoint declares the roles it accepts on
  the server, and the tests try each admin endpoint as citizen, officer and anonymous.
- **Separate portals**: logging in through the wrong portal fails with the same error as a wrong
  password, so the error doesn't reveal which role an account has.
- **Lockout**: after 5 failed logins the account is locked for 15 minutes. Login timing is the same
  whether or not the account exists.
- **Rate limits**: auth endpoints allow 10 requests per minute per IP; all of `/api` allows 300.
- **Input and transport**: Pydantic validation with `extra="forbid"`, configured CORS, security
  headers, and `Cache-Control: no-store` on API responses.
- **Audit log**: append-only and hash-chained. No update or delete route exists. Any edit made
  directly in MongoDB breaks the chain, and the admin console shows the first broken entry.
  Logged events: staff logins, lockouts, officer create/suspend, scheme create/update/delete, seed
  runs.
- **Soft deletes** for schemes (and later for users). Old scheme versions are kept in
  `scheme_versions` so applications can be judged under the version they were submitted with.
- **No secrets in code**. `APP_ENV=prod` refuses to start without `JWT_SECRET` and `STORAGE_SECRET`.

### Documents and extraction (step 3)

- **Validation**: the file type is checked from its first bytes, not its name (so a renamed `.exe`
  is refused). Images are verified with Pillow (which also blocks decompression bombs). Limits are
  5 MB and 10 PDF pages (`MAX_UPLOAD_MB`, `MAX_PDF_PAGES`).
- **Encryption at rest**: files are Fernet-encrypted before they are written to `FILE_STORAGE_DIR`.
  `FERNET_KEY` comes from the environment and never goes into the database. Deleting or replacing a
  document removes its encrypted file; the database row stays as a soft-deleted tombstone with its
  fields cleared.
- **Text**: PDFs with a text layer are read with PyMuPDF. Scans and images use Tesseract OCR in
  English and Hindi (installed in the Docker image). Without Tesseract, the result says "OCR not
  available" instead of guessing.
- **Fields**: deterministic patterns with English and Hindi labels for 12 document types, each value
  with a confidence. If an LLM is configured it reads the **masked** text as well: a value both agree
  on gets higher confidence, a disagreement is flagged, and an LLM-only value gets low confidence.
  The user reviews and can correct every field before it reaches the profile, and the profile
  records which document each value came from.
- **Aadhaar**: only the last four digits are ever kept. 12-digit numbers, 16-digit VIDs and bank
  account numbers are masked before the text reaches the LLM, logs or database. Raw OCR text is
  never stored. The profile API refuses a 12-digit number in any free-text field. A test dumps
  every collection after an Aadhaar upload and checks that the full number appears nowhere.
- **Duplicates**: each file's SHA-256 is stored. The same file uploaded from two accounts is flagged
  for the admin's fake-account review (step 7).
- **Consent**: "use once" means nothing is stored (uploads go to `/api/extract`). Withdrawing
  consent deletes the profile and every stored file. "Delete my account" also removes the login
  (the account row is soft-deleted with its personal fields cleared, and the action is logged).

### LLM wrapper (`app/ai/llm.py`)

`LLM_PROVIDER=mock` is the default and needs no key. It returns nothing, and the UI says "AI model:
not configured (mock)", so no field is ever invented. `LLM_PROVIDER=anthropic` with `LLM_API_KEY`
uses Claude (`LLM_MODEL`, default `claude-opus-5-5`) through the official `anthropic` SDK
(`requirements-ai.txt`, or build Docker with `INSTALL_AI=true`). It uses JSON-schema structured
output for extraction and has server-side refusal fallbacks turned on. Adding another provider means
writing one class with `complete` and `complete_json` methods.

### Sample documents

Run `python -m scripts.make_sample_docs` to write 8 synthetic PDFs and 2 PNG "scans" to
`sample_documents/`: Aadhaar, income certificate, ration card (AAY), caste certificate, land
record, bank passbook, marksheet and disability certificate for the demo users. Every one is
stamped "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT", and the Aadhaar-style number starts with 0, which
no real Aadhaar number does. The PNGs need Tesseract (included in the Docker image).

### Assistant and the hallucination guard (step 4)

1. **Corpus**: built only from stored, reviewed content: each scheme's overview, rules, documents
   and steps in both languages, plus the About page. That is 246 chunks, each carrying its source
   link and scheme version. The index is rebuilt in the background at start-up and after every
   admin scheme edit.
2. **Retrieval**: multilingual-e5-small embeddings (Hindi and English share one vector space) in a
   persistent ChromaDB collection, combined with keyword overlap (hybrid). An intent check
   (documents, how to apply, eligibility, benefit) moves the matching section of the best scheme to
   the top. The same index powers meaning-based search on `/explore`.
3. **Eligibility questions** ("Am I eligible…?", "क्या मैं पात्र हूँ…?") are not answered by a
   model. The rules engine runs on the person's saved profile, and its result becomes source [1].
4. **Answer**:
   - With an LLM configured, the model writes from numbered sources only, must cite after every
     sentence, and returns `NOT_FOUND` if the sources don't answer the question.
   - Without one, the answer is made of sentences copied from the best sources ("extractive" mode),
     and the UI labels it.
5. **Guard**: every sentence must cite a real source, every number in it must appear in a cited
   source, and it must be supported by the cited sources. Support is judged by an LLM entailment
   check when a model is configured, otherwise by word overlap. Unsupported sentences are removed.
6. **Abstain**: the assistant declines and links to the official portal when retrieval is weak
   (e5 similarity < 0.80), when the model says `NOT_FOUND`, when less than half the answer survives
   the guard, or when confidence (0.4 × retrieval + 0.6 × supported share) is below
   `ASSISTANT_MIN_CONFIDENCE` (0.6).
7. **Language**: the reply is in the language of the question, detected from the share of
   Devanagari characters.
8. **Logging**: every exchange is saved to `chat_logs` with ID numbers masked, the sources, the
   guard result, confidence and latency. 👎 answers and declined answers feed the admin's
   low-confidence queue.

Measured locally with the real model: relevant questions score 0.88–0.93 similarity and off-topic
ones ("capital of France", "cricket world cup") 0.74, so they are declined. After the first warm-up,
answers take about 40–80 ms in extractive mode.

### Applications (step 5)

**Lifecycle** (`app/services/applications.py`), enforced by an explicit transition table that says
which role may make each move:

```
draft ─user─► submitted ─system─► precheck_done ─officer─► under_review ─officer─► approved / rejected ─► closed
                                                        ▲        │
                                                 officer│        ▼ officer
                                                 resubmitted ◄─user── more_docs_needed
withdrawn: by the citizen from any open status (not after a decision)
```

- Every change appends `{from, to, at, by: {role, id}, note}` to `history` and creates a
  notification. Officer actions also go to the audit log.
- The update is conditional on the current status, so two officers acting at the same moment
  cannot both succeed.
- Only one open application per scheme per citizen is allowed.
- An application keeps the **scheme version** it was opened under. If an admin later changes the
  rules, the pre-check (and in step 6 the officer report) still judges it under its own version.
- Applying requires the "save my profile" data choice, because an application has to be stored.
- **Assignment** happens on submit: the least-loaded active officer, preferring officers assigned
  to that scheme, then officers whose region matches the applicant's state.
- **AI pre-check** (`app/services/precheck.py`), shown before submit and stored with the
  application. It is **guidance only** and never changes the status:
  - the rules engine result on the form answers, under the application's scheme version;
  - the required-document checklist (a ration card counts as BPL proof; Aadhaar or a birth
    certificate counts as age proof);
  - cross-document checks of name (fuzzy match ignoring Shri/Smt), date of birth, state, Aadhaar
    last 4 and income, comparing the form with each attached document;
  - risk flags: the same file uploaded by another account, or document fields never reviewed.

  It suggests "looks complete", "needs attention" or "rules not met", with a score.
### Officer review and the AI verification report (step 6)

The report (`app/services/verification.py`) is generated when a case enters the queue, again when
review starts or the applicant resubmits, and on demand. Every version is stored in `ai_reports`
with the versions of the engine, pre-check and report, so any decision can be traced to exactly
what the officer saw. It contains:

- **Rule-by-rule evidence**: for each eligibility rule, the declared value and whether an attached
  document *backs* it, *contradicts* it, or it is *self-declared only*. Age is derived from the
  date of birth on the document, as of the submission date. When a document contradicts the form,
  the rule is re-checked with the document's value. If that makes the rule fail, the suggestion
  becomes "not eligible".
- **Document completeness**, **cross-document mismatches** (name, date of birth, state, Aadhaar
  last 4, income) and **risk flags** (the same file uploaded by another account).
- A **suggested outcome**: eligible, not eligible or needs review, with reasons, a confidence score
  and a short case summary. The summary is built from the report itself, not written freely by a
  model.

Decisions are stored in `decisions` with the AI suggestion and confidence at that moment, the report
ID, and `differs_from_ai`. The officer can always disagree; the reason is mandatory and is shown to
the applicant in their timeline. Officer document views, claims and every status change are written
to the audit log. An officer can open only cases assigned to them; any other ID returns 404.

### Admin console (step 7)

- **Fake-account detection** (`app/services/fraud.py`) combines explainable rules with an anomaly
  model, and the admin makes the final call.
  - Rules: the same file uploaded from several accounts (SHA-256); the same name + date of birth or
    Aadhaar last 4 + date of birth in several accounts; impossible values (declared age far from the
    date of birth, widowed under 18, a family of 40, an absurd land holding); upload bursts (8+
    files in 10 minutes); unusually many applications.
  - Anomaly model: a scikit-learn **Isolation Forest** over eight per-account features, retrained
    on each scan, which adds a "statistically unusual" reason.
  - Each flag stores its reasons, score, features and model version. Clearing a flag needs a reason,
    and a cleared flag is not raised again unless the evidence gets stronger.
- **Rule extraction** (`app/ai/rule_extraction.py`) has two modes:
  - Without an LLM, deterministic patterns cover age ranges, income limits (lakh/crore), women,
    widows, BPL, SC/ST, disability %, income-tax payers, rural/urban, bank account and state.
  - With an LLM, the model must quote the sentence each rule came from. Any rule whose quote is not
    in the guideline is dropped (grounding check), and every rule is validated against the rules
    vocabulary.

  Nothing goes live until an admin approves it, and approval goes through the normal versioned
  scheme update.
- **Database browser**: personal fields are masked in every listing. Revealing one record requires
  a written reason that is written to the audit log. Secrets are never returned, even on reveal.
- **Every admin action** (officer changes, password resets, user suspensions and deletions, flag
  clears, data reveals, rule proposals and decisions, scheme edits, fraud scans) is written to the
  hash-chained audit log with who, when, before, after and the reason.

### Step 8: interview, what-if, planner, scam checker, change tracker, voice, evaluation

- **Adaptive interview** (`app/services/interview.py`): for every field a still-possible scheme
  depends on, it simulates each plausible answer (enum values, yes/no, or one value per interval
  between rule thresholds) and asks the field with the lowest expected number of remaining
  schemes (information gain). It stops when no question can change the result. Eligibility still
  comes from the rules engine.
- **What-if** (`app/services/whatif.py`) finds the minimal value that passes each failing rule
  (cheapest option inside "any" groups), applies all of them, and re-checks with the engine. Each
  change has a cost (open a bank account = cheap; waiting = years; gender, category and state =
  not changeable). Ages never go down.
- **Planner** (`app/services/planner.py`) picks the exact best combination by checking every subset
  of each group of mutually exclusive schemes (maximum-weight independent set), and orders the
  result by deadline, then by documents still missing.
- **Scam checker** (`app/services/scam.py`) combines a TF-IDF character n-gram + logistic regression
  classifier, trained on 90 labelled messages in Hindi and English, with 8 red-flag patterns and a
  retrieval lookup of the real scheme. A request for an OTP/PIN or a payment alone puts the message
  at "likely scam".
- **Change tracker** (`app/services/updates.py`) compares each scheme version with the previous one.
  When the rules change, it re-runs the engine on every saved profile and sends a notification to
  anyone whose result changed ("…rules changed — your result may be affected").
- **Voice**: speech-to-text uses faster-whisper (`ENABLE_VOICE=true`, `WHISPER_MODEL=small` by
  default; audio is processed in memory and never stored). The 🎤 button records in the browser;
  🔊 read-aloud uses the browser's `speechSynthesis` with hi-IN/en-IN voices.
- **Evaluation** (`app/services/evaluation.py`, `/admin/ai`) measures section 6 of the spec on
  labelled sets in `app/data/eval/`. Each run stores every number with the model and prompt versions.

**Latest run** (local, multilingual-e5-small, no LLM key = extractive mode):

| Metric | Result | How it's measured |
|---|---|---|
| Eligibility accuracy | 100% (60/60) | hand-checked profiles across 27 schemes, including boundary values |
| Rule extraction | P 1.0 / R 1.0 / F1 1.0 | 10 guideline excerpts, pattern extractor |
| RAG correctness | 100% (26/26) | answer cites the expected scheme; section matched 100% |
| Citation accuracy | 94.8% | share of citations that point to the expected scheme |
| Abstention | off-topic declined 60% → **100% after threshold calibration** · answerable lost 0% | 10 off-topic + 26 answerable questions (see Known limitations: the calibration is in-sample) |
| Document field accuracy | 100% (25 fields / 8 docs) | synthetic text-layer PDFs |
| Scam checker | P 0.875 / R 0.933 → **30/30 after fixing 2 patterns** | 30 held-out messages (in-sample after the fix) |
| Questions asked | 17.0 adaptive vs 24 full form | the same eligible set in 30/30 personas |
| Hindi speech WER | not measured | no recordings bundled |
| Median latency | 0.013 ms per rule check · 41 ms per chat answer | |

### Recommendations

Eligibility always comes from the rules engine. Within each status group, the ranking is
content-based: 0.5 × share of rules met + 0.3 × how well the scheme's target groups (women,
elderly, farmers, widows, SC/ST …, worked out from the profile) match + 0.2 × benefit size (log
scale). "What's missing" turns each failed or unknown rule into a next step with the numeric gap.
Options inside an "any of" group are shown as alternatives. The benefit estimate picks eligible
schemes greedily by value and skips ones that can't be combined.

### Eligibility engine (`app/services/eligibility.py`)

- Pure, deterministic Python with no AI involved. AI will only extract, explain and chat.
- Rules are stored as JSON trees made of `all`, `any`, `not` and leaf rules. The operators are `eq`,
  `ne`, `in`, `not_in`, `lt`, `lte`, `gt`, `gte`, `between`, `is_true` and `is_false`, applied over
  24 profile fields.
- Three-valued result: a field you didn't answer is *unknown*, not *failed*. A scheme is **likely
  eligible** if every rule passes, **not eligible** if any required rule fails, and **maybe**
  otherwise.
- Every rule is explained in both Hindi and English, with your value as evidence and an optional
  note.
- Ranking order: status first, then the share of rules met, then estimated annual benefit. State
  schemes are hidden for people who live in another state.
- Rules are validated on every admin save and every seed load: field names, operators, value types,
  allowed values and unique rule IDs.

---

## Project layout

```
app/
  config.py            settings from env (.env)
  db.py                Motor client, indexes, mongomock switch
  main.py              FastAPI factory, middleware, error handlers
  server.py            ASGI entry (API + NiceGUI)
  seed.py              synthetic seed data
  data/                schemes.json (30 schemes), documents_catalog.json
  models/              Pydantic request/response models
  routers/             auth, schemes, admin
  security/            passwords, JWT, rate limiter, role dependencies
  services/            users, schemes, eligibility (rules engine), audit
  ui/                  NiceGUI front-end: theme, i18n, API client, pages/
scripts/seed.py        seed a real MongoDB
tests/                 pytest suite
```

The UI talks to the backend only over HTTP (`app/ui/api_client.py`), sending requests in-process
through httpx's ASGI transport. Those requests go through the same validation, role checks and
rate limits as any outside client.

---

## Assumptions

1. **Scheme data is sample data.** The 30 schemes (27 central, 3 state) were compiled for this
   prototype from public knowledge and have **not** been checked against the live portals. They are
   marked `data_status: "sample"`, and the UI says so on every scheme page. Some rules are simplified
   on purpose, and each of those carries a note in the data:
   - PM-JAY really uses SECC deprivation data.
   - The Central Sector Scholarship uses "80% marks" in place of "80th percentile".
   - NFBS is modelled as BPL + widowed.
   - Amounts such as Ladli Behna ₹1,500/month and the Mudra and SVANidhi limits should be
     re-checked.
2. **"Last verified"** is shown as "Last reviewed". For seeded schemes it is the date the seed was
   compiled (7 Nov 2026). An admin edit sets it to the date of the edit.
3. **Password reset** has no e-mail or SMS gateway. Outside `prod`, the API returns the one-time code
   (`dev_reset_token`) and the UI shows it with a clear "Prototype" note. In `prod` it is never
   returned. Officers and admins cannot reset their own passwords; that is an admin action (step 7).
4. **Consent** is collected at registration and can be changed or withdrawn at `/start`.
   Withdrawing deletes the stored profile and files right away. Retention: stored data is kept while
   the account is active; "delete my account" clears it immediately (the account row is kept only as
   a soft-deleted tombstone).
5. **Scheme catalogue reads are public** because scheme information is public. Matching a personal
   profile (`/api/match`) requires a citizen login. Answers on `/check` are kept only in the
   browser tab's server-side memory (`app.storage.tab`) and are not saved to the database.
6. **Tokens for the UI** are kept in NiceGUI's server-side user storage, keyed by a signed cookie.
   That storage is a JSON file under `.nicegui/` (a volume in Docker).
7. **Login IDs** can be an e-mail address, a 10-digit Indian mobile number, or a username.
8. **Two-factor login** is optional and only for officers and admins: authenticator-app codes
   (RFC 6238). The setup screen shows the key as text to type into the app; there is no QR image,
   to avoid another dependency. An admin password reset also turns off that officer's 2FA, so a
   lost phone can be recovered.
11. **Fake-account detection** runs when the admin presses "Run scan", not on a timer. The anomaly
    model is retrained on all current citizen accounts each time, and is skipped (and says so) with
    fewer than 8 accounts.
9. **Uploaded files are encrypted with one app-wide Fernet key.** Rotating it would need a re-encrypt
   job (not built). In `dev`, a missing key is generated at start-up, so files uploaded in that run
   cannot be decrypted after a restart; the API returns `file_unavailable` in that case.
10. **The document viewer** turns PDFs into page images on the server (first 5 pages), so it works in
    every browser without exposing a file URL. Officers will see documents only through applications
    assigned to them (step 6).

## Known limitations

- The rate limiter is in memory and per process. Running more than one instance would need Redis or
  MongoDB counters.
- The scheme editor edits the main text fields and the rules (as JSON with live validation and a
  plain-language preview). Documents, steps and exclusions are kept as they are; change those
  through the API (`PUT /api/admin/schemes/{slug}`, try it at `/docs`). New schemes are also created
  through the API.
- The first start downloads the embedding model (about 470 MB) from Hugging Face; later starts use
  the cache. The very first question waits for the model and the index (up to about a minute).
  Without `sentence-transformers` the app uses the hash embedder: it works, but is much weaker
  across Hindi and English, and `/api/assistant/status` reports it.
- Extractive mode (no LLM key) answers by quoting the scheme data, which is accurate but wordy.
  Set `LLM_PROVIDER=anthropic` for natural answers, which go through the same guard.
- **The evaluation sets are small and synthetic**, and they were written alongside the code. Two
  settings were tuned after looking at them, so those two numbers are in-sample, not unseen-data
  results:
  - The e5 abstain threshold (0.83) was set in the gap between that set's relevant (≥ 0.852) and
    off-topic (≤ 0.811) questions.
  - The scam red-flag patterns ("निःशुल्क" means free, "share the 6-digit code") were fixed after
    seeing 3 errors on `scam_eval.json`.

  A real deployment needs fresh, real, labelled data.
- **Hindi speech accuracy (WER) is not measured**: no recordings are bundled. Drop `.wav` files with
  matching `.txt` transcripts into `app/data/eval/speech/` and the dashboard will compute it.
  Speech-to-text was checked end to end on English only: Windows' synthetic voice → faster-whisper
  `base` gave a word-for-word transcript (18 s cold start including the model download, 1.2 s warm).
- **Read-aloud** uses the browser's speech synthesis, so there is no `/tts` endpoint. Hindi voices
  depend on the device (present in Chrome, Edge and Android).
- **The plan PDF is English only**: the built-in PDF fonts have no Devanagari glyphs.
- **Not built**: the Telegram bot (spec 3.17), community worker batch mode, the scheme similarity
  graph and learning-to-rank (spec P4 stretch items), and automatic diffing of official guideline
  documents. The change tracker covers rule changes made in the app.
- OCR needs the Tesseract binary. It is in the Docker image; on a bare Windows machine install it
  (with Hindi data) or upload text PDFs. Without it, image uploads say so clearly.
- Field patterns are tuned to the synthetic samples and common certificate wording. Real
  certificates vary by state, which is why every field goes through user review.
