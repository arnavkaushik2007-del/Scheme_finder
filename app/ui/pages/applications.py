"""/apply/{slug} (pre-filled form, document checklist, AI pre-check, submit) and /applications (tracking)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import ui

from app.ui import api_client
from app.ui.i18n import L, error_text, t
from app.ui.pages.check import load_vocab, profile_from_inputs, profile_inputs
from app.ui.pages.documents import _doc_type_names
from app.ui.theme import (
    chip, fmt_date, fmt_dt, frame, guard, icon_tile, link_button, notify_error, render_rule_tree, status_chip,
)

PERSONAL = [("full_name", "full_name"), ("date_of_birth", "dob"), ("aadhaar_last4", "aadhaar_last4"),
            ("district", "district"), ("address", "address"), ("bank_account_last4", "bank_last4")]
STATUS_KIND = {"draft": "neutral", "submitted": "maybe", "precheck_done": "maybe", "under_review": "maybe",
               "more_docs_needed": "not_eligible", "resubmitted": "maybe", "approved": "eligible",
               "rejected": "not_eligible", "closed": "neutral", "withdrawn": "neutral"}
PC_KIND = {"looks_complete": "eligible", "needs_attention": "maybe", "likely_not_eligible": "not_eligible"}


def render_precheck(pc: dict[str, Any]) -> None:
    with ui.column().classes("gap-3 w-full"):
        with ui.row().classes("items-center gap-3"):
            ui.label(t("precheck")).classes("sf-h3")
            chip(t(f"pc_{pc['suggested']}"), PC_KIND[pc["suggested"]], "smart_toy")
            status_chip(pc["eligibility"]["status"])
        ui.label(t("precheck_note") + f" · {pc['engine']} · v{pc['scheme_version']}").classes("text-xs sf-muted")
        with ui.expansion(t("rules_title"), icon="rule").classes("w-full rounded-xl bg-[var(--sf-cream)]"):
            render_rule_tree(pc["eligibility"]["tree"])
        if pc["missing_documents"]:
            ui.label(t("missing_docs")).classes("font-semibold text-sm")
            with ui.row().classes("gap-2"):
                for d in pc["documents"]:
                    if not d["attached"]:
                        chip(L(d["name"]), "not_eligible", "description")
        for f in pc["findings"]:
            icon, cls = {"warning": ("warning_amber", "text-[var(--sf-amber)]"), "risk": ("gpp_maybe", "text-[var(--sf-red)]"),
                         "info": ("info", "sf-muted")}[f["level"]]
            with ui.row().classes("items-start gap-2 no-wrap"):
                ui.icon(icon, size="18px").classes(f"{cls} mt-[2px]")
                ui.label(L(f)).classes("text-sm")


@ui.page("/apply/{slug}")
async def apply_page(slug: str) -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    scheme = await api_client.call("GET", f"/api/schemes/{slug}")
    if not scheme.ok:
        return RedirectResponse("/recommendations", status_code=303)
    s = scheme.data
    apps = await api_client.call("GET", "/api/applications")
    existing = next((a for a in (apps.data or []) if a["scheme_slug"] == slug and a["status"] in
                     ("draft", "submitted", "precheck_done", "under_review", "more_docs_needed", "resubmitted")), None)
    prof = (await api_client.call("GET", "/api/me/profile")).data or {}
    docs = (await api_client.call("GET", "/api/documents")).data or []
    vocab = await load_vocab()
    names = await _doc_type_names()
    if existing and existing["status"] != "draft":
        with frame(t("apply")):
            with ui.column().classes("sf-card p-8 gap-3 items-start"):
                ui.label(t("already_applied")).classes("text-lg")
                status_chip_text(existing)
                link_button(t("open_it"), f"/applications#{existing['id']}", icon="open_in_new")
        return None

    draft = existing
    form = (draft or {}).get("form") or {"eligibility": prof.get("eligibility", {}), "personal": prof.get("personal", {})}
    rule_fields = set(s.get("rule_fields", []))
    with frame(f"{t('apply')} · {L(s['name'])}"):
        with ui.column().classes("gap-1"):
            ui.label(f"{t('apply_title')} {L(s['name'])}").classes("sf-title")
            ui.label(t("apply_sub")).classes("sf-muted")
        state: dict[str, Any] = {"id": (draft or {}).get("id"), "attached": set((draft or {}).get("documents", []))}
        with ui.element("div").classes("grid lg:grid-cols-2 gap-6 w-full items-start"):
            with ui.column().classes("gap-6"):
                with ui.column().classes("sf-card p-6 gap-3 w-full"):
                    ui.label(t("basics")).classes("sf-h3")
                    inputs = profile_inputs(vocab, form["eligibility"], prof.get("sources"), expanded=bool(rule_fields - set(
                        ["state", "residence", "age", "gender", "category", "annual_income", "occupation", "family_size"])))
                    for f, w in inputs.items():
                        if f in rule_fields:
                            w.classes("border-l-4 border-[var(--sf-orange)] rounded-xl")
                with ui.column().classes("sf-card p-6 gap-3 w-full"):
                    ui.label(t("personal")).classes("sf-h3")
                    pins = {}
                    for key, label in PERSONAL:
                        pins[key] = ui.input(t(label), value=form["personal"].get(key) or "") \
                            .props("outlined dense").classes("sf-input w-full")
            with ui.column().classes("gap-6"):
                with ui.column().classes("sf-card p-6 gap-3 w-full"):
                    ui.label(t("attach_docs")).classes("sf-h3")
                    ui.label(t("required")).classes("sf-kicker")
                    have_types = {d["doc_type"] for d in docs}
                    with ui.row().classes("gap-2"):
                        for d in s["documents_detail"]:
                            ok = d["id"] in have_types or (d["id"] == "bpl_card" and "ration_card" in have_types)
                            chip(L(d["name"]), "eligible" if ok else "neutral", "check" if ok else "radio_button_unchecked")
                    ui.label(t("your_docs")).classes("sf-kicker mt-2")
                    if not docs:
                        ui.label(t("no_docs")).classes("sf-muted text-sm")
                    for d in docs:
                        def toggle(e: Any, did: str = d["id"]) -> None:
                            (state["attached"].add if e.value else state["attached"].discard)(did)
                        ui.checkbox(f"{names.get(d['doc_type'], d['doc_type'])} — {d['filename']}",
                                    value=d["id"] in state["attached"] or (draft is None and d["doc_type"] in {x["id"] for x in s["documents_detail"]} | {"ration_card"}),
                                    on_change=toggle)
                        if draft is None and (d["doc_type"] in {x["id"] for x in s["documents_detail"]} or d["doc_type"] == "ration_card"):
                            state["attached"].add(d["id"])
                    ui.link(t("upload_more") + " →", "/documents").classes("sf-link text-sm")
                pc_box = ui.column().classes("sf-card p-6 gap-3 w-full")
                with pc_box:
                    if draft and draft.get("precheck"):
                        render_precheck(draft["precheck"])
                    else:
                        ui.label(t("precheck")).classes("sf-h3")
                        ui.label(t("precheck_note")).classes("text-sm sf-muted")

        def payload() -> dict[str, Any]:
            personal = {k: (w.value or "").strip() for k, w in pins.items() if (w.value or "").strip()}
            return {"form": {"eligibility": profile_from_inputs(inputs, vocab), "personal": personal},
                    "documents": sorted(state["attached"])}

        async def save() -> bool:
            body = payload()
            if state["id"]:
                res = await api_client.call("PUT", f"/api/applications/{state['id']}", json=body)
            else:
                res = await api_client.call("POST", "/api/applications", json={"scheme": slug, **body})
            if not res.ok:
                notify_error(res)
                return False
            state["id"] = res.data["id"]
            return True

        async def on_save() -> None:
            if await save():
                ui.notify(t("saved_ok"), type="positive", position="top")

        async def on_precheck() -> None:
            if not await save():
                return
            res = await api_client.call("POST", f"/api/applications/{state['id']}/precheck")
            if not res.ok:
                notify_error(res)
                return
            pc_box.clear()
            with pc_box:
                render_precheck(res.data)

        async def on_submit() -> None:
            if not decl.value:
                ui.notify(error_text("declaration_required"), type="warning", position="top")
                return
            if not await save():
                return
            res = await api_client.call("POST", f"/api/applications/{state['id']}/submit", json={"declaration": True})
            if not res.ok:
                notify_error(res)
                return
            ui.notify(t("submitted_ok"), type="positive", position="top")
            ui.navigate.to(f"/applications#{state['id']}")

        with ui.column().classes("sf-card p-6 gap-3 w-full"):
            decl = ui.checkbox(t("declaration"))
            with ui.row().classes("gap-3"):
                ui.button(t("save_draft"), icon="save", on_click=on_save).props("outline no-caps").classes("sf-btn sf-btn-outline")
                ui.button(t("run_precheck"), icon="smart_toy", on_click=on_precheck).props("outline no-caps") \
                    .classes("sf-btn sf-btn-outline")
                ui.button(t("submit_app"), icon="send", on_click=on_submit).props("unelevated no-caps").classes("sf-btn")
    return None


def status_chip_text(a: dict[str, Any]) -> None:
    chip(L(a["status_text"]), STATUS_KIND.get(a["status"], "neutral"))


OPEN_STATUSES = ("draft", "submitted", "precheck_done", "under_review", "more_docs_needed", "resubmitted")
DECIDED_STATUSES = ("approved", "rejected", "closed", "withdrawn")
STEPS = [("submitted", {"en": "Submitted", "hi": "जमा"}), ("precheck_done", {"en": "AI check", "hi": "AI जाँच"}),
         ("under_review", {"en": "Officer review", "hi": "अधिकारी समीक्षा"}), ("decision", {"en": "Decision", "hi": "निर्णय"})]
STEP_INDEX = {"draft": -1, "submitted": 0, "precheck_done": 1, "under_review": 2, "more_docs_needed": 2,
              "resubmitted": 2, "approved": 3, "rejected": 3, "closed": 3, "withdrawn": -1}


@ui.page("/applications")
async def applications_page() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    from app.ui.theme import page_header

    with frame(t("nav_apps"), active="/applications"):
        actions = page_header(t("apps_title"))
        with actions:
            link_button(t("nav_reco"), "/recommendations", icon="recommend", outline=True)
        state: dict[str, Any] = {"tab": "all"}
        res0 = await api_client.call("GET", "/api/applications")
        rows0 = res0.data if res0.ok else []
        n_open = sum(1 for a in rows0 if a["status"] in OPEN_STATUSES)
        if rows0:
            async def set_tab(e: Any) -> None:
                state["tab"] = e.value
                await listing.refresh()

            ui.toggle({"all": f"{t('all')} · {len(rows0)}", "open": f"{t('pending')} · {n_open}",
                       "decided": f"{t('decision')} · {len(rows0) - n_open}"}, value="all", on_change=set_tab) \
                .props("no-caps unelevated rounded toggle-color=primary").classes("border border-[var(--sf-line)] rounded-xl")

        @ui.refreshable
        async def listing() -> None:
            res = await api_client.call("GET", "/api/applications")
            if not res.ok:
                notify_error(res)
                return
            rows = res.data
            if state["tab"] == "open":
                rows = [a for a in rows if a["status"] in OPEN_STATUSES]
            elif state["tab"] == "decided":
                rows = [a for a in rows if a["status"] in DECIDED_STATUSES]
            if not res.data:
                with ui.column().classes("sf-card p-10 gap-3 items-center text-center w-full"):
                    icon_tile("assignment", "#0f4c35", "#e7f1eb", 64)
                    ui.label(t("apps_empty")).classes("sf-lead max-w-md")
                    link_button(t("nav_reco"), "/recommendations", icon="recommend")
                return
            if not rows:
                ui.label(t("none_found")).classes("sf-muted")
                return
            docs = (await api_client.call("GET", "/api/documents")).data or []
            names = await _doc_type_names()
            cats = {s["slug"]: s["category"] for s in ((await api_client.call("GET", "/api/schemes", auth=False)).data or [])}
            # applications that need the citizen's action first
            rows.sort(key=lambda a: (a["status"] != "more_docs_needed", a["status"] != "draft"))
            for a in rows:
                _app_card(a, docs, names, listing, cats.get(a["scheme_slug"], "social_welfare"))

        with ui.column().classes("gap-4 w-full"):
            await listing()
    return None


def _stepper(status: str) -> None:
    idx = STEP_INDEX.get(status, -1)
    bad = status == "rejected"
    with ui.row().classes("w-full items-center gap-0 no-wrap"):
        for i, (_, label) in enumerate(STEPS):
            done, current = i < idx or (i == idx and i == 3), i == idx and i != 3
            color = "#b3412e" if bad and i == 3 else "#0f4c35" if done else "#e0761b" if current else "#d8ccb4"
            with ui.column().classes("items-center gap-1 shrink-0 w-[70px] sm:w-[96px]"):
                ui.html(f'<div style="width:30px;height:30px;border-radius:999px;background:{color};display:flex;'
                        f'align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px">'
                        + (('<span class="material-icons" style="font-size:18px">'
                            + ("close" if bad and i == 3 else "check") + "</span>") if done else str(i + 1))
                        + "</div>", sanitize=False)
                ui.label(L(label)).classes("text-[11px] sm:text-xs text-center leading-tight " +
                                           ("font-semibold text-[var(--sf-ink)]" if done or current else "sf-muted"))
            if i < len(STEPS) - 1:
                ui.element("div").classes("h-[3px] flex-grow rounded-full -mt-5").style(
                    f"background:{'#0f4c35' if i < idx else '#e5dac4'}")


def _app_card(a: dict[str, Any], docs: list[dict[str, Any]], names: dict[str, str], refresh: Any,
              category: str) -> None:
    from app.services.applications import STATUS_TEXT
    from app.ui.theme import category_badge

    officer_notes = [h for h in a["history"] if h["by"]["role"] == "officer" and h.get("note")]
    with ui.column().classes("sf-card p-5 md:p-6 gap-4 w-full").props(f'id="{a["id"]}"'):
        with ui.row().classes("w-full items-start gap-3 no-wrap"):
            category_badge(category, 48)
            with ui.column().classes("gap-1 flex-grow min-w-0"):
                ui.label(L(a["scheme_name"])).classes("text-lg md:text-xl font-bold leading-snug")
                ui.label(f"#{a['id'][:8].upper()} · v{a['scheme_version']} · {fmt_date(str(a['created_at']))}") \
                    .classes("text-xs sf-muted")
            status_chip_text(a)

        # call to action first: what the citizen needs to do now
        if a["status"] == "more_docs_needed":
            with ui.column().classes("gap-2 p-4 rounded-2xl w-full").style(
                    "background:var(--sf-orange-50);border:1px solid #f3cfa6"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("priority_high", size="20px").classes("text-[var(--sf-orange)]")
                    ui.label(t("action_needed")) \
                        .classes("font-bold text-[var(--sf-orange)]")
                if officer_notes:
                    ui.label(f"{t('officer_remark')}: {officer_notes[-1]['note']}").classes("leading-snug")
                ui.button(t("resubmit"), icon="upload_file",
                          on_click=lambda a=a: _resubmit_dialog(a, docs, names, refresh)) \
                    .props("unelevated no-caps color=secondary").classes("sf-btn self-start")
        elif a["status"] in ("approved", "rejected") or (a["status"] == "closed" and a.get("decision")):
            approved = (a.get("decision") or {}).get("action") == "approve" or a["status"] == "approved"
            with ui.row().classes("items-start gap-3 p-4 rounded-2xl w-full no-wrap").style(
                    f"background:{'var(--sf-green-50)' if approved else 'var(--sf-red-50)'}"):
                ui.icon("verified" if approved else "cancel", size="24px").classes(
                    "sf-rule-pass" if approved else "sf-rule-fail")
                with ui.column().classes("gap-0"):
                    ui.label(L(STATUS_TEXT["approved" if approved else "rejected"])).classes("font-bold")
                    if officer_notes:
                        ui.label(officer_notes[-1]["note"]).classes("text-sm")
        elif a["status"] == "draft":
            with ui.row().classes("items-center gap-3 p-4 rounded-2xl w-full bg-[var(--sf-cream)]"):
                ui.icon("edit_note", size="22px").classes("sf-muted")
                ui.label(L(STATUS_TEXT["draft"])).classes("font-semibold flex-grow")
                link_button(t("continue_draft"), f"/apply/{a['scheme_slug']}", icon="edit")

        if a["status"] not in ("draft", "withdrawn"):
            _stepper(a["status"])

        with ui.expansion(t("timeline"), icon="history").classes("w-full rounded-xl border border-[var(--sf-line)]"):
            with ui.column().classes("gap-0 w-full pt-2"):
                for i, h in enumerate(a["history"]):
                    last = i == len(a["history"]) - 1
                    with ui.row().classes("items-start gap-3 no-wrap"):
                        with ui.column().classes("items-center gap-0"):
                            ui.element("div").classes("w-3 h-3 rounded-full mt-1 " + (
                                "bg-[var(--sf-orange)]" if last else "bg-[var(--sf-green)]"))
                            if not last:
                                ui.element("div").classes("w-[2px] h-8 bg-[var(--sf-line)]")
                        with ui.column().classes("gap-0 pb-2"):
                            who = {"user": t("by_you"), "officer": t("by_officer"), "system": t("by_system")}.get(
                                h["by"]["role"], "")
                            ui.label(L(STATUS_TEXT[h["to"]])).classes(
                                "font-semibold" + (" text-[var(--sf-orange)]" if last else ""))
                            ui.label(f"{fmt_dt(h['at'])} · {who}").classes("text-xs sf-muted")
                            if h.get("note") and h["by"]["role"] != "system":
                                label = t("officer_remark") + ": " if h["by"]["role"] == "officer" else ""
                                ui.label(label + h["note"]).classes("text-sm bg-[var(--sf-cream)] px-3 py-1 rounded-lg mt-1")
        if a.get("precheck") and a["status"] != "draft":
            with ui.expansion(t("precheck"), icon="smart_toy").classes("w-full rounded-xl border border-[var(--sf-line)]"):
                render_precheck(a["precheck"])

        with ui.row().classes("gap-3 items-center"):
            ui.link(t("view_scheme") + " →", f"/scheme/{a['scheme_slug']}").classes("sf-link")
            if a["status"] in OPEN_STATUSES:
                async def do_withdraw(a: dict[str, Any] = a) -> None:
                    with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3"):
                        ui.label(t("withdraw_confirm"))

                        async def yes() -> None:
                            res = await api_client.call("POST", f"/api/applications/{a['id']}/withdraw", json={})
                            dlg.close()
                            if not res.ok:
                                notify_error(res)
                                return
                            await refresh.refresh()

                        with ui.row().classes("justify-end w-full"):
                            ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
                            ui.button(t("withdraw"), on_click=yes).props("unelevated no-caps color=negative").classes("sf-btn")
                    dlg.open()

                ui.button(t("withdraw"), icon="undo", on_click=do_withdraw).props("flat no-caps color=negative") \
                    .classes("ml-auto")


def _resubmit_dialog(a: dict[str, Any], docs: list[dict[str, Any]], names: dict[str, str], refresh: Any) -> None:
    chosen: set[str] = set()
    with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3 w-[min(520px,95vw)]"):
        ui.label(t("resubmit")).classes("sf-h3 text-[1.45rem]")
        for d in docs:
            if d["id"] in a["documents"]:
                continue
            ui.checkbox(f"{names.get(d['doc_type'], d['doc_type'])} — {d['filename']}",
                        on_change=lambda e, did=d["id"]: (chosen.add if e.value else chosen.discard)(did))
        ui.link(t("upload_more") + " →", "/documents").classes("sf-link text-sm")
        note = ui.textarea(t("resubmit_note")).props("outlined").classes("sf-input w-full")

        async def send() -> None:
            res = await api_client.call("POST", f"/api/applications/{a['id']}/resubmit",
                                        json={"documents": sorted(chosen), "note": (note.value or "").strip() or None})
            if not res.ok:
                notify_error(res)
                return
            dlg.close()
            await refresh.refresh()

        with ui.row().classes("justify-end w-full"):
            ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
            ui.button(t("send"), on_click=send).props("unelevated no-caps").classes("sf-btn")
    dlg.open()
