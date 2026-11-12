"""Officer pages: /officer/dashboard (queue), /officer/application/{id} (case + AI report + decision),
/officer/history, /officer/stats."""

from __future__ import annotations

import base64
from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import ui

from app.ui import api_client
from app.ui.i18n import L, error_text, t
from app.ui.pages.applications import STATUS_KIND
from app.ui.pages.documents import _doc_type_names, _display_value, _field_label
from app.ui.pages.check import load_vocab
from app.ui.theme import chip, fmt_dt, frame, guard, link_button, notify_error

SKIP = "__all__"
SUG_KIND = {"eligible": "eligible", "not_eligible": "not_eligible", "needs_review": "maybe"}
EV = {"supported": ("verified", "sf-rule-pass"), "contradicted": ("report", "sf-rule-fail"),
      "self_declared": ("person", "sf-rule-unknown")}


def _sug_chip(s: str | None, conf: float | None) -> None:
    if not s:
        chip("—", "neutral")
        return
    chip(f"{t('sug_' + s)}" + (f" · {round(conf * 100)}%" if conf is not None else ""), SUG_KIND[s], "smart_toy")


def _age_chip(days: float) -> None:
    kind = "eligible" if days < 3 else "maybe" if days < 7 else "not_eligible"
    chip(f"{days:g} {t('days')}", kind, "schedule")


@ui.page("/officer/dashboard")
async def officer_queue() -> RedirectResponse | None:
    if (redirect := guard("officer")):
        return redirect
    from app.ui.theme import icon_tile, page_header

    schemes = (await api_client.call("GET", "/api/schemes", auth=False)).data or []
    with frame(t("off_queue"), active="/officer/dashboard"):
        actions = page_header(t("off_queue"), kicker="OFFICER · अधिकारी")
        with actions:
            link_button(t("ask_rules"), "/assistant", icon="forum", outline=True)
        filters: dict[str, Any] = {"status": None, "scheme": None, "conf": None}

        async def fetch() -> dict[str, Any] | None:
            params: dict[str, Any] = {"status": filters["status"], "scheme": filters["scheme"]}
            if filters["conf"] == "low":
                params["max_conf"] = 0.5
            elif filters["conf"] == "high":
                params["min_conf"] = 0.8
            res = await api_client.call("GET", "/api/officer/queue", params=params)
            if not res.ok:
                notify_error(res)
                return None
            return res.data

        tiles = [("precheck_done", t("off_new"), "fiber_new", "#e0761b", "#fdf0e3"),
                 ("under_review", t("off_in_review"), "rate_review", "#1d4ed8", "#e7eefc"),
                 ("more_docs_needed", t("off_waiting"), "hourglass_top", "#a86a00", "#fff4dc"),
                 ("resubmitted", t("off_resubmitted"), "upload_file", "#6d28d9", "#efe9fd"),
                 ("decided_7d", t("off_decided"), "task_alt", "#0f4c35", "#e7f1eb")]

        @ui.refreshable
        async def body() -> None:
            d = await fetch()
            if d is None:
                return
            # ---- compact, clickable counts (click = filter by that status)
            with ui.element("div").classes("grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 w-full"):
                for key, label, icon, color, tint in tiles:
                    active = filters["status"] == key

                    async def pick(k: str = key) -> None:
                        if k == "decided_7d":
                            ui.navigate.to("/officer/history")
                            return
                        filters["status"] = None if filters["status"] == k else k
                        await body.refresh()

                    with ui.row().classes("sf-card sf-card-hover p-3 gap-3 items-center no-wrap cursor-pointer").style(
                            f"border-color:{color}" if active else "").on("click", pick):
                        icon_tile(icon, color, tint, 40)
                        with ui.column().classes("gap-0 min-w-0"):
                            ui.label(str(d["counts"].get(key, 0))).classes("sf-serif text-2xl font-extrabold leading-none") \
                                .style(f"color:{color}")
                            ui.label(label).classes("text-xs sf-muted leading-tight")
            # ---- filters
            with ui.row().classes("gap-3 items-center w-full"):
                def setter(key: str):
                    async def _set(e: Any) -> None:
                        filters[key] = None if e.value == SKIP else e.value
                        await body.refresh()
                    return _set
                ui.select({SKIP: t("all"), "precheck_done": t("off_new"), "under_review": t("off_in_review"),
                           "more_docs_needed": t("off_waiting"), "resubmitted": t("off_resubmitted")},
                          value=filters["status"] or SKIP, label=t("status"), on_change=setter("status")) \
                    .props("outlined dense").classes("sf-input min-w-[200px] flex-grow sm:flex-grow-0")
                ui.select({SKIP: t("all"), **{s["slug"]: L(s["name"]) for s in schemes}}, value=filters["scheme"] or SKIP,
                          label=t("scheme"), with_input=True, on_change=setter("scheme")) \
                    .props("outlined dense").classes("sf-input min-w-[240px] flex-grow sm:flex-grow-0")
                ui.select({SKIP: t("conf_any"), "low": t("conf_low"), "high": t("conf_high")},
                          value=filters["conf"] or SKIP, label=t("min_confidence"), on_change=setter("conf")) \
                    .props("outlined dense").classes("sf-input min-w-[180px] flex-grow sm:flex-grow-0")
            # ---- cases
            if not d["items"]:
                with ui.column().classes("sf-card p-10 items-center text-center gap-3 w-full"):
                    icon_tile("inbox", "#0f4c35", "#e7f1eb", 60)
                    ui.label(t("queue_empty")).classes("sf-lead max-w-md")
            with ui.column().classes("gap-3 w-full"):
                for i in d["items"]:
                    with ui.link(target=f"/officer/application/{i['id']}").classes("no-underline text-inherit w-full"):
                        with ui.element("div").classes(
                                "sf-card sf-card-hover p-4 grid gap-3 items-center justify-items-start w-full "
                                "md:grid-cols-[minmax(0,1.5fr)_minmax(0,1.6fr)_auto_auto_auto]"):
                            with ui.row().classes("items-center gap-3 no-wrap min-w-0"):
                                ui.html(f'<div class="sf-avatar">{(i["applicant"][:1] or "?").upper()}</div>',
                                        sanitize=False)
                                with ui.column().classes("gap-0 min-w-0"):
                                    ui.label(i["applicant"]).classes("font-semibold truncate")
                                    ui.label(f"#{i['id'][:8].upper()}").classes("text-xs sf-muted")
                            ui.label(L(i["scheme_name"])).classes("text-sm leading-snug")
                            chip(L(i["status_text"]), STATUS_KIND.get(i["status"], "neutral"))
                            _sug_chip(i["ai_suggested"], i["ai_confidence"])
                            _age_chip(i["age_days"])
            if d["unassigned"]:
                with ui.column().classes("sf-card p-5 gap-3 w-full"):
                    ui.label(t("unassigned")).classes("sf-h3")
                    for i in d["unassigned"]:
                        with ui.row().classes("items-center gap-4 w-full"):
                            ui.label(f"{i['applicant']} — {L(i['scheme_name'])}").classes("flex-grow")
                            _age_chip(i["age_days"])

                            async def take(aid: str = i["id"]) -> None:
                                r = await api_client.call("POST", f"/api/officer/applications/{aid}/claim")
                                if r.ok:
                                    ui.navigate.to(f"/officer/application/{aid}")
                                else:
                                    notify_error(r)

                            ui.button(t("claim"), on_click=take).props("outline no-caps dense").classes("sf-btn sf-btn-outline")

        with ui.column().classes("gap-5 w-full"):
            await body()
    return None


def _show(field: str, value: Any) -> str:
    """Rule values in the reader's language (Yes/No, enum labels, ₹ amounts)."""
    from app.services.eligibility import FIELDS, format_value
    from app.ui.i18n import lang

    return format_value(field, value, lang()) if field in FIELDS else str(value)


def _render_report(r: dict[str, Any]) -> None:
    with ui.column().classes("gap-4 w-full"):
        with ui.row().classes("items-center gap-3 w-full"):
            _sug_chip(r["suggested"], r["confidence"])
            ui.linear_progress(r["confidence"], show_value=False).props(
                f"rounded size=10px color={'positive' if r['confidence'] >= 0.8 else 'warning'}").classes("w-40")
            ui.label(f"{r['version']} · {r['engine']} · v{r['scheme_version']} · {fmt_dt(r['generated_at'])}") \
                .classes("text-xs sf-muted")
        ui.label(L(r["note"])).classes("text-xs sf-muted -mt-2")
        with ui.column().classes("gap-1 p-3 rounded-xl bg-[var(--sf-cream)] w-full"):
            ui.label(t("case_summary")).classes("font-semibold text-sm")
            ui.label(L(r["summary"])).classes("text-sm")
            for reason in r["reasons"]:
                ui.label("• " + L(reason)).classes("text-sm")

        ui.label(t("rules_title")).classes("font-semibold")
        for rule in r["rules"]:
            icon, cls = EV[rule["evidence"]]
            oicon = {"pass": "check_circle", "fail": "cancel", "unknown": "help_outline"}[rule["outcome"]]
            ocls = {"pass": "sf-rule-pass", "fail": "sf-rule-fail", "unknown": "sf-rule-unknown"}[rule["outcome"]]
            with ui.column().classes("gap-1 w-full border-b border-[var(--sf-line)] pb-2"):
                with ui.row().classes("items-start gap-2 no-wrap w-full"):
                    ui.icon(oicon, size="20px").classes(f"{ocls} mt-[2px]")
                    with ui.column().classes("gap-0 flex-grow"):
                        ui.label(L(rule["text"])).classes("font-medium")
                        shown = "—" if rule["declared"] is None else _show(rule["field"], rule["declared"])
                        ui.label(f"{t('declared')}: {shown}").classes("text-xs sf-muted")
                    with ui.row().classes("items-center gap-1 shrink-0"):
                        ui.icon(icon, size="18px").classes(cls)
                        ui.label(t(f"ev_{rule['evidence']}")).classes(f"text-xs {cls}")
                for s in rule["supporting"]:
                    ui.label(f"✓ {L(s['doc_name'])}: {_show(rule['field'], s['value'])}" + (f" ({round(s['confidence'] * 100)}%)"
                                                                       if s.get("confidence") else "")) \
                        .classes("text-xs sf-rule-pass pl-7")
                for s in rule["contradicting"]:
                    ui.label(f"✗ {L(s['doc_name'])}: {_show(rule['field'], s['value'])}").classes("text-xs sf-rule-fail pl-7")
                if rule.get("outcome_with_document_value"):
                    ui.label(f"→ {t('with_doc_value')}: {t('status_' + rule['outcome_with_document_value'])}") \
                        .classes("text-xs sf-rule-fail pl-7 font-semibold")

        ui.label(t("completeness_checklist")).classes("font-semibold")
        with ui.row().classes("gap-2"):
            for d in r["documents"]:
                chip(L(d["name"]), "eligible" if d["attached"] else "not_eligible",
                     "check" if d["attached"] else "close")
        for title, items in ((t("mismatches"), r["mismatches"]), (t("risk_flags"), r["risk_flags"])):
            ui.label(title).classes("font-semibold")
            if not items:
                ui.label(t("none_found")).classes("text-sm sf-muted")
            for f in items:
                with ui.row().classes("items-start gap-2 no-wrap"):
                    ui.icon("warning_amber" if f["level"] == "warning" else "gpp_maybe", size="18px") \
                        .classes("text-[var(--sf-amber)] mt-[2px]" if f["level"] == "warning" else "text-[var(--sf-red)] mt-[2px]")
                    ui.label(L(f)).classes("text-sm")


@ui.page("/officer/application/{app_id}")
async def officer_case(app_id: str) -> RedirectResponse | None:
    if (redirect := guard("officer")):
        return redirect
    res = await api_client.call("GET", f"/api/officer/applications/{app_id}")
    if not res.ok:
        return RedirectResponse("/officer/dashboard", status_code=303)
    vocab = await load_vocab()
    names = await _doc_type_names()
    catalog = (await api_client.call("GET", f"/api/schemes/{res.data['application']['scheme_slug']}", auth=False)).data
    with frame(t("off_queue"), active="/officer/dashboard"):
        page = ui.column().classes("w-full gap-6")

    async def render() -> None:
        r = await api_client.call("GET", f"/api/officer/applications/{app_id}")
        if not r.ok:
            notify_error(r)
            return
        d = r.data
        a, p, report = d["application"], d["applicant"], d["report"]
        page.clear()
        with page:
            ui.link(t("back"), "/officer/dashboard").classes("sf-link")
            with ui.row().classes("w-full items-start justify-between gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label(f"{L(a['scheme_name'])}").classes("sf-title")
                    ui.label(f"#{a['id'][:8].upper()} · v{a['scheme_version']} · {fmt_dt(a['submitted_at'])}") \
                        .classes("text-xs sf-muted")
                with ui.row().classes("gap-2 items-center"):
                    chip(L(a["status_text"]), STATUS_KIND.get(a["status"], "neutral"))
                    _age_chip(a["age_days"])
            with ui.element("div").classes("grid xl:grid-cols-[1.1fr_1fr] gap-6 w-full items-start"):
                # ---------------------------------------------------- left: applicant + documents
                with ui.column().classes("gap-6 min-w-0"):
                    with ui.column().classes("sf-card p-5 gap-2 w-full"):
                        ui.label(t("applicant")).classes("sf-h3")
                        with ui.element("div").classes("grid grid-cols-2 gap-x-6 gap-y-1 w-full"):
                            for label, val in ((t("full_name"), p["full_name"]), (t("dob"), p["date_of_birth"]),
                                               ("Aadhaar", p["aadhaar_masked"]), (t("state"), p["state"]),
                                               (t("district"), p["district"]), (t("bank_last4"), p["bank_masked"]),
                                               (t("address"), p["address"])):
                                ui.label(label).classes("sf-muted text-sm")
                                ui.label(val or "—").classes("font-medium text-sm")
                        with ui.expansion(t("basics"), icon="list").classes("w-full rounded-xl bg-[var(--sf-cream)]"):
                            for k, v in (a["form"] or {}).get("eligibility", {}).items():
                                with ui.row().classes("justify-between w-full"):
                                    ui.label(_field_label(vocab, k)).classes("sf-muted text-sm")
                                    ui.label(_display_value(vocab, k, v)).classes("text-sm font-medium")
                    for doc in d["documents"]:
                        with ui.column().classes("sf-card p-5 gap-3 w-full"):
                            with ui.row().classes("items-center justify-between w-full"):
                                ui.label(names.get(doc["doc_type"], doc["doc_type"])).classes("font-bold")
                                if doc.get("duplicate_across_accounts"):
                                    chip(t("risk_flags"), "not_eligible", "gpp_maybe")
                            if doc["deleted"]:
                                ui.label(t("doc_deleted")).classes("sf-muted")
                                continue
                            with ui.element("div").classes("grid md:grid-cols-2 gap-4 w-full"):
                                viewer = ui.column().classes("w-full min-h-[120px] bg-[var(--sf-cream)] rounded-xl p-2")

                                async def show(doc: dict[str, Any] = doc, box: ui.column = viewer) -> None:
                                    fr = await api_client.call(
                                        "GET", f"/api/officer/applications/{app_id}/documents/{doc['id']}/file")
                                    if not fr.ok:
                                        notify_error(fr)
                                        return
                                    data = fr.data["_bytes"]
                                    box.clear()
                                    with box:
                                        if doc["content_type"] == "application/pdf":
                                            import pymupdf

                                            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                                                for pg in list(pdf)[:3]:
                                                    src = base64.b64encode(pg.get_pixmap(dpi=90).tobytes("png")).decode()
                                                    ui.image("data:image/png;base64," + src).classes("w-full rounded")
                                        else:
                                            ui.image(f"data:{doc['content_type']};base64,"
                                                     + base64.b64encode(data).decode()).classes("w-full rounded")

                                with viewer:
                                    ui.button(t("view"), icon="visibility", on_click=show).props("flat no-caps")
                                with ui.column().classes("gap-1"):
                                    fields = ((doc.get("extracted") or {}).get("fields") or {})
                                    confirmed = doc.get("confirmed_fields") or {}
                                    for name, f in fields.items():
                                        val = confirmed.get(name, f["value"])
                                        with ui.row().classes("justify-between w-full gap-2 no-wrap"):
                                            ui.label(_field_label(vocab, name)).classes("text-xs sf-muted")
                                            ui.label(_display_value(vocab, name, val)
                                                     + (" ✎" if name in confirmed and confirmed[name] != f["value"] else "")) \
                                                .classes("text-xs font-semibold text-right")
                                    chip(t(f"status_{doc['status']}"), "eligible" if doc["status"] == "confirmed" else "maybe")
                    with ui.column().classes("sf-card p-5 gap-2 w-full"):
                        ui.label(t("timeline")).classes("sf-h3")
                        from app.services.applications import STATUS_TEXT

                        for h in a["history"]:
                            ui.label(f"{fmt_dt(h['at'])} · {L(STATUS_TEXT[h['to']])}"
                                     + (f" — {h['note']}" if h.get("note") and h["by"]["role"] != "system" else "")) \
                                .classes("text-sm")
                # ---------------------------------------------------- right: AI report + decision
                with ui.column().classes("gap-6 min-w-0 xl:sticky xl:top-24"):
                    with ui.column().classes("sf-card p-5 gap-3 w-full"):
                        with ui.row().classes("items-center justify-between w-full"):
                            ui.label(t("ai_report")).classes("sf-h3")

                            async def rerun() -> None:
                                rr = await api_client.call("POST", f"/api/officer/applications/{app_id}/report")
                                if rr.ok:
                                    await render()
                                else:
                                    notify_error(rr)

                            ui.button(t("regenerate"), icon="refresh", on_click=rerun).props("flat dense no-caps")
                        _render_report(report)
                        ui.link(t("ask_rules") + " →", f"/assistant?scheme={a['scheme_slug']}").classes("sf-link text-sm")
                    _decision_panel(a, report, catalog, render)
                    if d["decisions"]:
                        with ui.column().classes("sf-card p-5 gap-2 w-full"):
                            ui.label(t("past_decisions")).classes("font-bold")
                            for dec in d["decisions"]:
                                ui.label(f"{fmt_dt(dec['at'])} · {t(dec['action'])} · "
                                         + (t("overrode_ai") if dec["differs_from_ai"] else t("agreed_with_ai"))).classes("text-sm")
                                ui.label(dec["reason"]).classes("text-xs sf-muted")

    await render()
    return None


def _decision_panel(a: dict[str, Any], report: dict[str, Any], scheme: dict[str, Any], rerender: Any) -> None:
    with ui.column().classes("sf-card p-5 gap-3 w-full border-2 border-[var(--sf-green)]"):
        ui.label(t("decision")).classes("sf-h3")
        status = a["status"]
        if status in ("precheck_done", "resubmitted"):
            ui.label(t("start_review_hint")).classes("sf-muted text-sm")

            async def start() -> None:
                r = await api_client.call("POST", f"/api/officer/applications/{a['id']}/start-review")
                if r.ok:
                    await rerender()
                else:
                    notify_error(r)

            ui.button(t("start_review"), icon="play_arrow", on_click=start).props("unelevated no-caps").classes("sf-btn")
            return
        if status in ("approved", "rejected"):
            async def close() -> None:
                r = await api_client.call("POST", f"/api/officer/applications/{a['id']}/close")
                if r.ok:
                    await rerender()
                else:
                    notify_error(r)

            ui.button(t("close_case"), icon="archive", on_click=close).props("outline no-caps").classes("sf-btn sf-btn-outline")
            return
        if status != "under_review":
            ui.label(L(a["status_text"])).classes("sf-muted")
            return
        equivalent = {"approve": "eligible", "reject": "not_eligible", "request_docs": "needs_review"}
        action = ui.radio({"approve": t("approve"), "request_docs": t("request_docs"), "reject": t("reject")},
                          value={v: k for k, v in equivalent.items()}.get(report["suggested"], "request_docs")) \
            .props("inline")
        warn = ui.row().classes("items-start gap-2 p-2 rounded-lg bg-[var(--sf-amber-50)] no-wrap")
        with warn:
            ui.icon("info", size="18px").classes("text-[var(--sf-amber)]")
            ui.label(t("disagree_warning")).classes("text-sm")
        docs_sel = ui.select({d["id"]: L(d["name"]) for d in scheme["documents_detail"]}, multiple=True,
                             label=t("which_docs"), value=[d for d in report["missing_documents"]
                                                          if d in {x["id"] for x in scheme["documents_detail"]}]) \
            .props("outlined dense use-chips").classes("sf-input w-full")
        reason = ui.textarea(t("reason_required")).props("outlined counter maxlength=1000").classes("sf-input w-full")

        def sync() -> None:
            warn.set_visibility(equivalent[action.value] != report["suggested"])
            docs_sel.set_visibility(action.value == "request_docs")

        action.on_value_change(lambda _: sync())
        sync()

        async def submit() -> None:
            text = (reason.value or "").strip()
            if len(text) < 10:
                ui.notify(t("reason_required"), type="warning", position="top")
                return
            r = await api_client.call("POST", f"/api/officer/applications/{a['id']}/decision", json={
                "action": action.value, "reason": text,
                "requested_documents": list(docs_sel.value or []) if action.value == "request_docs" else []})
            if not r.ok:
                ui.notify(error_text(r.code, r.detail), type="negative", position="top")
                return
            ui.notify(t("decision_saved"), type="positive", position="top")
            await rerender()

        ui.button(t("submit_decision"), icon="gavel", on_click=submit).props("unelevated no-caps").classes("sf-btn")


@ui.page("/officer/history")
async def officer_history() -> RedirectResponse | None:
    if (redirect := guard("officer")):
        return redirect
    res = await api_client.call("GET", "/api/officer/history")
    with frame(t("off_history"), active="/officer/history"):
        ui.label(t("past_decisions")).classes("sf-title")
        rows = res.data if res.ok else []
        if not rows:
            ui.label(t("none_found")).classes("sf-muted")
        with ui.column().classes("gap-3 w-full"):
            for d in rows:
                with ui.column().classes("sf-card p-4 gap-1 w-full"):
                    with ui.row().classes("items-center gap-3 w-full"):
                        ui.label(f"{d.get('applicant') or '—'} — {L(d.get('scheme_name'))}").classes("font-bold flex-grow")
                        chip(t(d["action"]), {"approve": "eligible", "reject": "not_eligible"}.get(d["action"], "maybe"))
                        chip(t("overrode_ai") if d["differs_from_ai"] else t("agreed_with_ai"),
                             "maybe" if d["differs_from_ai"] else "neutral", "smart_toy")
                    ui.label(d["reason"]).classes("text-sm")
                    ui.label(f"{fmt_dt(d['at'])} · {t('ai_suggestion')}: "
                             + (t('sug_' + d['ai_suggested']) if d.get('ai_suggested') else '—')).classes("text-xs sf-muted")
                    ui.link(t("open") + " →", f"/officer/application/{d['application_id']}").classes("sf-link text-sm")
    return None


@ui.page("/officer/stats")
async def officer_stats() -> RedirectResponse | None:
    if (redirect := guard("officer")):
        return redirect
    res = await api_client.call("GET", "/api/officer/stats")
    with frame(t("off_stats"), active="/officer/stats"):
        ui.label(t("off_stats")).classes("sf-title")
        if not res.ok:
            notify_error(res)
            return None
        s = res.data
        with ui.element("div").classes("grid grid-cols-2 lg:grid-cols-4 gap-5 w-full"):
            for label, value in ((t("cases_decided"), str(s["cases_decided"])),
                                 (t("avg_time"), f"{s['avg_hours_to_decision']} {t('hours')}" if s["avg_hours_to_decision"] is not None else "—"),
                                 (t("override_rate"), f"{round(s['override_rate'] * 100)}%" if s["override_rate"] is not None else "—"),
                                 (t("open_cases"), str(s["open_cases"]))):
                with ui.column().classes("sf-card p-5 gap-1"):
                    ui.label(value).classes("sf-title")
                    ui.label(label).classes("sf-muted")
        with ui.column().classes("sf-card p-5 gap-2 w-full"):
            ui.label(t("agreement_table")).classes("font-bold")
            with ui.element("table").classes("sf-table w-full"):
                with ui.element("tr"):
                    for h in (t("ai_suggestion"), "Total", t("agreed_with_ai")):
                        with ui.element("th"):
                            ui.label(h)
                for k, v in s["agreement_by_ai_suggestion"].items():
                    with ui.element("tr"):
                        for val in (t("sug_" + k), str(v["total"]), str(v["agreed"])):
                            with ui.element("td"):
                                ui.label(val)
            ui.label(" · ".join(f"{t(k)}: {v}" for k, v in s["by_action"].items())).classes("text-sm sf-muted")
        from app.ui.pages.dashboards import _change_password_card

        from app.ui.pages.admin import twofa_card

        me = await api_client.call("GET", "/api/auth/me")
        with ui.element("div").classes("grid md:grid-cols-2 gap-5 w-full"):
            _change_password_card()
            twofa_card(bool(me.ok and me.data.get("totp_enabled")))
    return None
