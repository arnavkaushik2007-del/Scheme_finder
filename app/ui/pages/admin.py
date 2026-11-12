"""Admin console pages: overview, officers, schemes & rules, rules review, users, database, audit, analytics."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import events, ui

from app.ui import api_client
from app.ui.i18n import L, error_text, t
from app.ui.theme import chip, fmt_dt, frame, guard, notify_error

NAV = [("adm_overview", "/admin", "dashboard"), ("adm_officers", "/admin/officers", "badge"),
       ("adm_schemes", "/admin/schemes", "inventory_2"), ("adm_rules_review", "/admin/rules-review", "rule"),
       ("adm_users", "/admin/users", "groups"), ("adm_database", "/admin/database", "storage"),
       ("adm_audit", "/admin/audit", "receipt_long"), ("adm_analytics", "/admin/analytics", "insights"),
       ("adm_ai", "/admin/ai", "smart_toy")]


@contextmanager
def admin_frame(key: str) -> Iterator[ui.column]:
    path = next(p for k, p, _ in NAV if k == key)
    with frame(t(key), active="/admin"):
        with ui.element("div").classes("grid lg:grid-cols-[220px_1fr] gap-6 w-full items-start"):
            with ui.column().classes("sf-card p-3 gap-1 lg:sticky lg:top-24 max-lg:!flex-row max-lg:flex-wrap"):
                ui.label("ADMIN · एडमिन").classes("sf-kicker px-2 pt-1 max-lg:hidden")
                for k, p, icon in NAV:
                    active = p == path
                    with ui.link(target=p).classes("no-underline lg:w-full"):
                        with ui.row().classes("items-center gap-2 px-3 py-2 rounded-xl w-full no-wrap " + (
                                "bg-[var(--sf-green)] text-white" if active else "text-[var(--sf-ink)] hover:bg-[var(--sf-cream-2)]")):
                            ui.icon(icon, size="20px")
                            ui.label(t(k)).classes("font-medium")
            with ui.column().classes("gap-5 min-w-0 w-full") as body:
                ui.label(t(key)).classes("sf-title")
                yield body


def _reason_dialog(title: str, on_ok: Any, *, danger: bool = False, label: str | None = None) -> None:
    with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3 w-[min(460px,92vw)]"):
        ui.label(title).classes("sf-h3")
        reason = ui.input(label or t("reason")).props("outlined dense autofocus").classes("sf-input w-full")

        async def ok() -> None:
            if len((reason.value or "").strip()) < 5:
                ui.notify(label or t("reason"), type="warning", position="top")
                return
            dlg.close()
            await on_ok(reason.value.strip())

        with ui.row().classes("justify-end w-full"):
            ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
            ui.button(t("save"), on_click=ok).props("unelevated no-caps" + (" color=negative" if danger else "")) \
                .classes("sf-btn")
    dlg.open()


def _table(headers: list[str]) -> ui.element:
    wrap = ui.element("div").classes("w-full overflow-x-auto")
    with wrap:
        table = ui.element("table").classes("sf-table w-full text-sm")
        with table:
            with ui.element("tr"):
                for h in headers:
                    with ui.element("th"):
                        ui.label(h)
    return table


def _td(text: Any = "") -> ui.element:
    cell = ui.element("td")
    if text != "":
        with cell:
            ui.label(str(text))
    return cell

# ============================================================================ overview


@ui.page("/admin")
async def admin_overview() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    stats = await api_client.call("GET", "/api/admin/stats")
    audit = await api_client.call("GET", "/api/admin/audit", params={"limit": 8})
    me = await api_client.call("GET", "/api/auth/me")
    with admin_frame("adm_overview"):
        if stats.ok:
            with ui.element("div").classes("grid grid-cols-2 xl:grid-cols-4 gap-4 w-full"):
                for key, icon in (("users", "groups"), ("officers", "badge"), ("schemes", "inventory_2"),
                                  ("audit_entries", "receipt_long")):
                    with ui.column().classes("sf-card p-5 gap-1"):
                        ui.icon(icon, size="24px").classes("text-[var(--sf-orange)]")
                        ui.label(str(stats.data[key])).classes("sf-title")
                        ui.label(t(key)).classes("sf-muted")
        with ui.element("div").classes("grid md:grid-cols-2 gap-5 w-full"):
            with ui.column().classes("sf-card p-5 gap-2"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(t("audit_title")).classes("font-bold text-lg")
                    if audit.ok:
                        chip(t("chain_ok") if audit.data["chain_ok"] else t("chain_bad"),
                             "eligible" if audit.data["chain_ok"] else "not_eligible", "verified")
                for e in (audit.data["entries"] if audit.ok else []):
                    ui.label(f"#{e['seq']} · {e['action']} · {e['actor'].get('role')} · {fmt_dt(e['ts'])}").classes("text-sm")
                ui.link(t("adm_audit") + " →", "/admin/audit").classes("sf-link text-sm")
            twofa_card(bool(me.ok and me.data.get("totp_enabled")))
    return None


def twofa_card(enabled: bool) -> None:
    """Optional authenticator-app 2FA for the signed-in officer/admin."""
    with ui.column().classes("sf-card p-5 gap-3"):
        ui.label(t("security")).classes("font-bold text-lg")
        box = ui.column().classes("gap-2 w-full")

        def draw(on: bool) -> None:
            box.clear()
            with box:
                ui.label(t("twofa_on") if on else t("twofa_off")).classes("text-sm")
                code = ui.input(t("twofa_code")).props("outlined dense maxlength=6 inputmode=numeric") \
                    .classes("sf-input w-48")
                if on:
                    async def off() -> None:
                        r = await api_client.call("POST", "/api/auth/2fa/disable", json={"code": code.value or ""})
                        if r.ok:
                            draw(False)
                        else:
                            notify_error(r)
                    ui.button(t("twofa_disable"), on_click=off).props("outline no-caps").classes("sf-btn sf-btn-outline")
                    return
                code.set_visibility(False)
                key_label = ui.label("").classes("font-mono text-sm break-all")

                async def setup() -> None:
                    r = await api_client.call("POST", "/api/auth/2fa/setup")
                    if not r.ok:
                        notify_error(r)
                        return
                    key_label.text = f"{t('twofa_key')} {r.data['secret']}"
                    code.set_visibility(True)
                    on_btn.set_visibility(True)

                async def enable() -> None:
                    r = await api_client.call("POST", "/api/auth/2fa/enable", json={"code": code.value or ""})
                    if r.ok:
                        draw(True)
                    else:
                        notify_error(r)

                ui.button(t("twofa_setup"), icon="phonelink_lock", on_click=setup).props("outline no-caps") \
                    .classes("sf-btn sf-btn-outline")
                on_btn = ui.button(t("twofa_enable"), on_click=enable).props("unelevated no-caps").classes("sf-btn")
                on_btn.set_visibility(False)

        draw(enabled)

# ============================================================================ officers


@ui.page("/admin/officers")
async def admin_officers() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    schemes = (await api_client.call("GET", "/api/schemes", auth=False)).data or []
    scheme_opts = {s["slug"]: L(s["name"]) for s in schemes}
    with admin_frame("adm_officers") as body:
        @ui.refreshable
        async def listing() -> None:
            res = await api_client.call("GET", "/api/admin/officers")
            if not res.ok:
                notify_error(res)
                return
            table = _table([t("full_name"), t("login_id"), t("region"), t("assigned_schemes"), t("status"), ""])
            with table:
                for o in res.data:
                    with ui.element("tr"):
                        _td(o["full_name"])
                        _td(o["login_id"])
                        _td(o["region"] or "—")
                        _td(", ".join(scheme_opts.get(s, s) for s in o["assigned_schemes"]) or "—")
                        with _td():
                            chip(t(o["status"]), "eligible" if o["status"] == "active" else "not_eligible")
                        with _td():
                            with ui.row().classes("gap-1 no-wrap"):
                                ui.button(icon="edit", on_click=lambda o=o: edit(o)).props("flat round dense") \
                                    .tooltip(t("edit"))
                                ui.button(icon="key", on_click=lambda o=o: reset(o)).props("flat round dense") \
                                    .tooltip(t("reset_password"))
                                new = "suspended" if o["status"] == "active" else "active"
                                ui.button(icon="block" if new == "suspended" else "check_circle",
                                          on_click=lambda o=o, s=new: set_status(o, s)).props("flat round dense") \
                                    .tooltip(t("suspend") if new == "suspended" else t("activate"))
                                ui.button(icon="delete", on_click=lambda o=o: remove(o)) \
                                    .props("flat round dense color=negative").tooltip(t("delete"))

        def edit(o: dict[str, Any]) -> None:
            with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3 w-[min(520px,95vw)]"):
                ui.label(f"{t('edit')}: {o['full_name']}").classes("sf-h3")
                name = ui.input(t("full_name"), value=o["full_name"]).props("outlined dense").classes("sf-input w-full")
                region = ui.input(t("region"), value=o["region"]).props("outlined dense").classes("sf-input w-full")
                assigned = ui.select(scheme_opts, multiple=True, value=o["assigned_schemes"], label=t("assigned_schemes"),
                                     with_input=True).props("outlined dense use-chips").classes("sf-input w-full")
                reason = ui.input(t("reason")).props("outlined dense").classes("sf-input w-full")

                async def save() -> None:
                    r = await api_client.call("PATCH", f"/api/admin/officers/{o['id']}", json={
                        "full_name": name.value, "region": region.value, "assigned_schemes": assigned.value or [],
                        "reason": reason.value or ""})
                    if not r.ok:
                        notify_error(r)
                        return
                    dlg.close()
                    await listing.refresh()

                with ui.row().classes("justify-end w-full"):
                    ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
                    ui.button(t("save"), on_click=save).props("unelevated no-caps").classes("sf-btn")
            dlg.open()

        def reset(o: dict[str, Any]) -> None:
            async def go(reason: str) -> None:
                r = await api_client.call("POST", f"/api/admin/officers/{o['id']}/reset-password", json={"reason": reason})
                if not r.ok:
                    notify_error(r)
                    return
                with ui.dialog() as d2, ui.card().classes("sf-card p-6 gap-3"):
                    ui.label(t("temp_password"))
                    ui.label(r.data["temporary_password"]).classes("font-mono text-xl font-bold select-all")
                    ui.button(t("save"), on_click=d2.close).props("unelevated no-caps").classes("sf-btn")
                d2.open()
            _reason_dialog(f"{t('reset_password')}: {o['full_name']}", go)

        def set_status(o: dict[str, Any], s: str) -> None:
            async def go(reason: str) -> None:
                r = await api_client.call("PATCH", f"/api/admin/officers/{o['id']}/status", json={"status": s, "reason": reason})
                if r.ok:
                    await listing.refresh()
                else:
                    notify_error(r)
            _reason_dialog(f"{t('suspend') if s == 'suspended' else t('activate')}: {o['full_name']}", go)

        def remove(o: dict[str, Any]) -> None:
            async def go(reason: str) -> None:
                r = await api_client.call("DELETE", f"/api/admin/officers/{o['id']}", params={"reason": reason})
                if r.ok:
                    await listing.refresh()
                else:
                    notify_error(r)
            _reason_dialog(f"{t('delete')}: {o['full_name']}", go, danger=True)

        def add() -> None:
            with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3 w-[min(480px,92vw)]"):
                ui.label(t("add_officer")).classes("sf-h3 text-[1.45rem]")
                f_name = ui.input(t("full_name")).props("outlined dense").classes("sf-input w-full")
                f_login = ui.input(t("login_id")).props("outlined dense").classes("sf-input w-full")
                f_pw = ui.input(t("password"), password=True, password_toggle_button=True) \
                    .props("outlined dense autocomplete=new-password").classes("sf-input w-full")
                ui.label(t("pw_rules")).classes("text-xs sf-muted")
                f_region = ui.input(t("region")).props("outlined dense").classes("sf-input w-full")
                f_assigned = ui.select(scheme_opts, multiple=True, value=[], label=t("assigned_schemes"), with_input=True) \
                    .props("outlined dense use-chips").classes("sf-input w-full")

                async def create() -> None:
                    r = await api_client.call("POST", "/api/admin/officers", json={
                        "full_name": (f_name.value or "").strip(), "login_id": (f_login.value or "").strip(),
                        "password": f_pw.value or "", "region": (f_region.value or "").strip(),
                        "assigned_schemes": f_assigned.value or []})
                    if not r.ok:
                        ui.notify(error_text(r.code, r.detail), type="negative", position="top")
                        return
                    dlg.close()
                    await listing.refresh()

                with ui.row().classes("justify-end w-full"):
                    ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
                    ui.button(t("save"), on_click=create).props("unelevated no-caps").classes("sf-btn")
            dlg.open()

        with body:
            ui.button(t("add_officer"), icon="person_add", on_click=add).props("unelevated no-caps").classes("sf-btn self-start")
            with ui.column().classes("sf-card p-4 w-full"):
                await listing()
    return None

# ============================================================================ schemes


@ui.page("/admin/schemes")
async def admin_schemes() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    with admin_frame("adm_schemes") as body:
        @ui.refreshable
        async def listing() -> None:
            res = await api_client.call("GET", "/api/admin/schemes-all")
            if not res.ok:
                notify_error(res)
                return
            table = _table([t("scheme"), t("level"), "v", t("verified_on"), ""])
            with table:
                for s in res.data:
                    with ui.element("tr"):
                        with _td():
                            ui.label(L(s["name"])).classes("font-semibold")
                            ui.label(s["slug"]).classes("text-xs sf-muted")
                        _td(t("central") if s["level"] == "central" else s["state"])
                        _td(f"v{s['version']}")
                        _td(s.get("last_verified", ""))
                        with _td():
                            with ui.row().classes("gap-1 no-wrap"):
                                ui.button(icon="edit", on_click=lambda s=s: editor(s)).props("flat round dense").tooltip(t("edit"))
                                ui.button(icon="history", on_click=lambda s=s: versions(s)).props("flat round dense") \
                                    .tooltip(t("versions"))
                                ui.button(icon="delete", on_click=lambda s=s: remove(s)) \
                                    .props("flat round dense color=negative").tooltip(t("delete"))

        def editor(s: dict[str, Any]) -> None:
            from app.services.eligibility import RuleError, describe_rule, iter_leaves, validate_rules

            with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3 w-[min(860px,96vw)] max-h-[92vh] overflow-auto"):
                ui.label(f"{t('edit')}: {L(s['name'])} (v{s['version']})").classes("sf-h3")
                fields: dict[str, Any] = {}
                with ui.element("div").classes("grid md:grid-cols-2 gap-3 w-full"):
                    for key in ("name", "summary", "benefit"):
                        for lg in ("en", "hi"):
                            fields[(key, lg)] = ui.textarea(f"{key} ({lg})", value=s[key][lg]).props("outlined dense autogrow") \
                                .classes("sf-input w-full")
                    fields["official_url"] = ui.input("official_url", value=s["official_url"]).props("outlined dense").classes("sf-input w-full")
                    fields["annual_benefit_inr"] = ui.number("annual_benefit_inr", value=s["annual_benefit_inr"], min=0) \
                        .props("outlined dense").classes("sf-input w-full")
                rules = ui.textarea(t("rules_json"), value=json.dumps(s["rules"], ensure_ascii=False, indent=2)) \
                    .props("outlined autogrow").classes("sf-input w-full font-mono text-xs")
                preview = ui.column().classes("gap-1 p-3 rounded-xl bg-[var(--sf-cream)] w-full")

                def check() -> dict[str, Any] | None:
                    preview.clear()
                    with preview:
                        try:
                            tree = json.loads(rules.value or "{}")
                            validate_rules(tree)
                        except (json.JSONDecodeError, RuleError) as exc:
                            ui.label(str(exc)).classes("text-sm sf-rule-fail")
                            return None
                        ui.label(t("rules_valid")).classes("text-sm sf-rule-pass font-semibold")
                        for leaf in iter_leaves(tree):
                            ui.label("• " + describe_rule(leaf, "en") + " / " + describe_rule(leaf, "hi")).classes("text-sm")
                        return tree

                ui.button(t("preview"), icon="visibility", on_click=check).props("flat no-caps")
                note = ui.input(t("change_note")).props("outlined dense").classes("sf-input w-full")

                async def save() -> None:
                    tree = check()
                    if tree is None:
                        return
                    payload = {k: s[k] for k in ("slug", "category", "level", "state", "ministry", "benefit_type",
                                                 "beneficiary_groups", "deadline", "source_url", "documents", "steps",
                                                 "exclusive_with", "tags")}
                    for key in ("name", "summary", "benefit"):
                        payload[key] = {lg: fields[(key, lg)].value for lg in ("en", "hi")}
                    payload.update({"official_url": fields["official_url"].value,
                                    "annual_benefit_inr": int(fields["annual_benefit_inr"].value or 0),
                                    "rules": tree, "change_note": note.value or ""})
                    r = await api_client.call("PUT", f"/api/admin/schemes/{s['slug']}", json=payload)
                    if not r.ok:
                        msg = error_text(r.code, r.detail)
                        if r.code == "validation_error":
                            msg += " " + "; ".join(e["msg"] for e in r.detail.get("errors", [])[:3])
                        ui.notify(msg, type="negative", position="top", multi_line=True)
                        return
                    dlg.close()
                    ui.notify(f"v{r.data['version']} ✓", type="positive")
                    await listing.refresh()

                with ui.row().classes("justify-end w-full"):
                    ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
                    ui.button(t("save"), on_click=save).props("unelevated no-caps").classes("sf-btn")
                check()
            dlg.open()

        async def versions(s: dict[str, Any]) -> None:
            r = await api_client.call("GET", f"/api/admin/schemes/{s['slug']}/versions")
            with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3 w-[min(720px,95vw)] max-h-[85vh] overflow-auto"):
                ui.label(f"{t('versions')}: {L(s['name'])}").classes("sf-h3")
                for v in (r.data if r.ok else []):
                    with ui.expansion(f"v{v['version']} · {fmt_dt(v['changed_at'])} · {v.get('change_note') or ''}") \
                            .classes("w-full rounded-xl border border-[var(--sf-line)]"):
                        ui.code(json.dumps(v["data"]["rules"], ensure_ascii=False, indent=2), language="json").classes("w-full")
                ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
            dlg.open()

        def remove(s: dict[str, Any]) -> None:
            async def go(reason: str) -> None:
                r = await api_client.call("DELETE", f"/api/admin/schemes/{s['slug']}", params={"reason": reason})
                if r.ok:
                    await listing.refresh()
                else:
                    notify_error(r)
            _reason_dialog(f"{t('delete')}: {L(s['name'])}", go, danger=True)

        with body:
            with ui.column().classes("sf-card p-4 w-full"):
                await listing()
    return None

# ============================================================================ rules review


@ui.page("/admin/rules-review")
async def admin_rules_review() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    schemes = (await api_client.call("GET", "/api/schemes", auth=False)).data or []
    with admin_frame("adm_rules_review") as body:
        state: dict[str, Any] = {"file": None, "filename": None, "status": "pending"}

        @ui.refreshable
        async def proposals() -> None:
            r = await api_client.call("GET", "/api/admin/rules/proposals", params={"status": state["status"]})
            rows = r.data if r.ok else []
            if not rows:
                ui.label(t("none_found")).classes("sf-muted")
            for p in rows:
                _proposal_card(p, proposals)

        with body:
            with ui.column().classes("sf-card p-5 gap-3 w-full"):
                ui.label(t("ingest_title")).classes("sf-h3")
                ui.label(t("ingest_sub")).classes("text-sm sf-muted")
                scheme = ui.select({s["slug"]: L(s["name"]) for s in schemes}, label=t("scheme"), with_input=True) \
                    .props("outlined dense").classes("sf-input w-full md:w-96")

                async def got_file(e: events.UploadEventArguments) -> None:
                    state["file"] = await e.file.read()
                    state["filename"] = e.file.name

                ui.upload(on_upload=got_file, auto_upload=True, max_file_size=10 * 1024 * 1024) \
                    .props('accept=".pdf" flat bordered').classes("w-full")
                text = ui.textarea(t("paste_text")).props("outlined autogrow").classes("sf-input w-full")

                async def run() -> None:
                    if not scheme.value:
                        ui.notify(t("scheme"), type="warning")
                        return
                    files = {"file": (state["filename"], state["file"], "application/pdf")} if state["file"] else None
                    r = await api_client.call("POST", "/api/admin/rules/ingest",
                                              data={"scheme": scheme.value, **({"text": text.value} if text.value else {})},
                                              files=files)
                    if not r.ok:
                        notify_error(r)
                        return
                    state["file"] = None
                    text.value = ""
                    state["status"] = "pending"
                    await proposals.refresh()

                ui.button(t("extract"), icon="auto_fix_high", on_click=run).props("unelevated no-caps").classes("sf-btn self-start")

            async def set_status(e: Any) -> None:
                state["status"] = e.value
                await proposals.refresh()

            ui.toggle({"pending": t("pending"), "approved": t("approved"), "rejected": t("rejected")}, value="pending",
                      on_change=set_status).props("no-caps unelevated rounded")
            with ui.column().classes("gap-4 w-full"):
                await proposals()
    return None


def _proposal_card(p: dict[str, Any], refresh: Any) -> None:
    from app.services.eligibility import describe_rule, iter_leaves

    with ui.column().classes("sf-card p-5 gap-3 w-full"):
        with ui.row().classes("w-full items-start justify-between"):
            with ui.column().classes("gap-0"):
                ui.label(L(p["scheme_name"])).classes("font-bold text-lg")
                ui.label(f"{p['source'].get('filename') or 'text'} · {p['method']} · base v{p['base_version']} · "
                         f"{fmt_dt(p['created_at'])}").classes("text-xs sf-muted")
            chip(t(p["status"]), {"pending": "maybe", "approved": "eligible", "rejected": "not_eligible"}[p["status"]])
        with ui.element("div").classes("grid md:grid-cols-2 gap-4 w-full"):
            with ui.column().classes("gap-1"):
                for key, kind in (("added", "eligible"), ("changed", "maybe"), ("removed", "not_eligible"), ("unchanged", "neutral")):
                    items = p["diff"].get(key, [])
                    if not items:
                        continue
                    ui.label(t(key)).classes("sf-kicker mt-1")
                    for it in items:
                        if key == "changed":
                            ui.label(f"{L(it['from'])}  →  {L(it['to'])}").classes("text-sm")
                        else:
                            chip(L(it), kind)
            with ui.column().classes("gap-1"):
                ui.label(t("source_quote")).classes("sf-kicker")
                leaves = {l["id"]: l for l in iter_leaves(p["proposed_rules"])}
                for rid, quote in p["evidence"].items():
                    if rid in leaves:
                        ui.label(f"{describe_rule(leaves[rid], 'en')}: “{quote}”").classes("text-xs")
                for d in p.get("dropped", []):
                    ui.label("✗ " + d).classes("text-xs sf-rule-fail")
        if p["status"] != "pending":
            if p.get("decision_reason"):
                ui.label(p["decision_reason"]).classes("text-sm sf-muted")
            return
        rules = ui.textarea(t("rules_json"), value=json.dumps(p["proposed_rules"], ensure_ascii=False, indent=2)) \
            .props("outlined autogrow").classes("sf-input w-full font-mono text-xs")
        reason = ui.input(t("reason")).props("outlined dense").classes("sf-input w-full")

        async def decide(approve: bool) -> None:
            if len((reason.value or "").strip()) < 5:
                ui.notify(t("reason"), type="warning", position="top")
                return
            try:
                edited = json.loads(rules.value or "{}")
            except json.JSONDecodeError as exc:
                ui.notify(str(exc), type="negative")
                return
            r = await api_client.call("POST", f"/api/admin/rules/proposals/{p['id']}/decision", json={
                "approve": approve, "reason": reason.value.strip(),
                "rules": edited if approve and edited != p["proposed_rules"] else None})
            if not r.ok:
                notify_error(r)
                return
            await refresh.refresh()

        with ui.row().classes("gap-2"):
            ui.button(t("approve_live"), icon="check", on_click=lambda: decide(True)).props("unelevated no-caps").classes("sf-btn")
            ui.button(t("reject"), icon="close", on_click=lambda: decide(False)).props("flat no-caps color=negative")

# ============================================================================ users


@ui.page("/admin/users")
async def admin_users() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    with admin_frame("adm_users") as body:
        f: dict[str, Any] = {"q": "", "flagged": False}

        @ui.refreshable
        async def listing() -> None:
            r = await api_client.call("GET", "/api/admin/users", params={"q": f["q"] or None, "flagged": str(f["flagged"]).lower()})
            if not r.ok:
                notify_error(r)
                return
            if not r.data:
                ui.label(t("none_found")).classes("sf-muted")
            for u in r.data:
                with ui.column().classes("sf-card p-4 gap-2 w-full"):
                    with ui.row().classes("w-full items-center gap-3"):
                        with ui.column().classes("gap-0 flex-grow"):
                            ui.label(f"{u['full_name']} · {u['login_id']}").classes("font-semibold")
                            ui.label(f"{u['applications']} app · {u['documents']} docs · {fmt_dt(u['created_at'])}") \
                                .classes("text-xs sf-muted")
                        chip(t(u["status"]), "eligible" if u["status"] == "active" else "not_eligible")
                        if u["flag"]:
                            chip(f"⚑ {round(u['flag']['score'] * 100)}%", "not_eligible", "flag")
                        new = "suspended" if u["status"] == "active" else "active"
                        ui.button(t("suspend") if new == "suspended" else t("activate"),
                                  on_click=lambda u=u, s=new: set_status(u, s)).props("flat dense no-caps")
                        if u["flag"]:
                            ui.button(t("clear_flag"), on_click=lambda u=u: clear(u)).props("flat dense no-caps")
                        ui.button(t("delete_account"), on_click=lambda u=u: remove(u)) \
                            .props("flat dense no-caps color=negative")
                    if u["flag"]:
                        with ui.column().classes("gap-1 p-3 rounded-xl bg-[var(--sf-red-50)] w-full"):
                            ui.label(t("flag_reasons")).classes("font-semibold text-sm")
                            for reason in u["flag"]["reasons"]:
                                ui.label("• " + L(reason)).classes("text-sm")

        def set_status(u: dict[str, Any], s: str) -> None:
            async def go(reason: str) -> None:
                r = await api_client.call("PATCH", f"/api/admin/users/{u['id']}/status", json={"status": s, "reason": reason})
                if r.ok:
                    await listing.refresh()
                else:
                    notify_error(r)
            _reason_dialog(t("suspend") if s == "suspended" else t("activate"), go)

        def clear(u: dict[str, Any]) -> None:
            async def go(reason: str) -> None:
                r = await api_client.call("POST", f"/api/admin/fraud/{u['id']}/clear", json={"reason": reason})
                if r.ok:
                    await listing.refresh()
                else:
                    notify_error(r)
            _reason_dialog(t("clear_flag"), go)

        def remove(u: dict[str, Any]) -> None:
            async def go(reason: str) -> None:
                r = await api_client.call("DELETE", f"/api/admin/users/{u['id']}", params={"reason": reason})
                if r.ok:
                    await listing.refresh()
                else:
                    notify_error(r)
            _reason_dialog(t("delete_account"), go, danger=True)

        async def scan() -> None:
            r = await api_client.call("POST", "/api/admin/fraud/scan")
            if r.ok:
                ui.notify(f"{r.data['accounts']} accounts · {r.data['flagged']} flagged · {r.data['model']}", type="info")
                await listing.refresh()
            else:
                notify_error(r)

        with body:
            with ui.row().classes("gap-3 items-center w-full"):
                q = ui.input(placeholder=t("search")).props("outlined dense clearable debounce=400").classes("sf-input w-72")

                async def on_q(e: Any) -> None:
                    f["q"] = e.value or ""
                    await listing.refresh()

                async def on_flag(e: Any) -> None:
                    f["flagged"] = e.value
                    await listing.refresh()

                q.on_value_change(on_q)
                ui.switch(t("flagged_only"), on_change=on_flag)
                ui.button(t("run_scan"), icon="policy", on_click=scan).props("unelevated no-caps").classes("sf-btn ml-auto")
            with ui.column().classes("gap-3 w-full"):
                await listing()
    return None

# ============================================================================ database


@ui.page("/admin/database")
async def admin_database() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    from app.services.admin_console import COLLECTIONS

    with admin_frame("adm_database") as body:
        st: dict[str, Any] = {"c": "users", "q": "", "skip": 0}

        @ui.refreshable
        async def rows() -> None:
            r = await api_client.call("GET", f"/api/admin/database/{st['c']}", params={"q": st["q"] or None,
                                                                                    "skip": st["skip"], "limit": 15})
            if not r.ok:
                notify_error(r)
                return
            d = r.data
            ui.label(f"{d['total']} · {st['skip'] + 1}–{min(st['skip'] + 15, d['total'])}").classes("text-sm sf-muted")
            for row in d["rows"]:
                label = row.get("slug") or row.get("login_id") or row.get("action") or row.get("status") or row["_id"]
                with ui.expansion(f"{row['_id'][:12]}… · {label}").classes("w-full rounded-xl border border-[var(--sf-line)]"):
                    ui.code(json.dumps(row, ensure_ascii=False, indent=2, default=str), language="json").classes("w-full text-xs")
                    ui.button(t("reveal"), icon="visibility", on_click=lambda row=row: reveal(row["_id"])) \
                        .props("flat dense no-caps")
            with ui.row().classes("gap-2"):
                async def page(delta: int) -> None:
                    st["skip"] = max(0, st["skip"] + delta)
                    await rows.refresh()
                if st["skip"] > 0:
                    ui.button(t("prev"), on_click=lambda: page(-15)).props("flat no-caps")
                if st["skip"] + 15 < d["total"]:
                    ui.button(t("next"), on_click=lambda: page(15)).props("flat no-caps")

        def reveal(doc_id: str) -> None:
            async def go(reason: str) -> None:
                r = await api_client.call("POST", f"/api/admin/database/{st['c']}/{doc_id}/reveal", json={"reason": reason})
                if not r.ok:
                    notify_error(r)
                    return
                with ui.dialog() as dlg, ui.card().classes("sf-card p-5 gap-2 w-[min(820px,96vw)] max-h-[88vh] overflow-auto"):
                    ui.label(f"{st['c']} / {doc_id}").classes("font-bold")
                    ui.code(json.dumps(r.data, ensure_ascii=False, indent=2, default=str), language="json").classes("w-full text-xs")
                    ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
                dlg.open()
            _reason_dialog(t("reveal"), go, label=t("reveal_reason"))

        with body:
            with ui.row().classes("items-start gap-2 p-3 rounded-xl bg-[var(--sf-amber-50)] w-full no-wrap"):
                ui.icon("shield", size="20px").classes("text-[var(--sf-amber)]")
                ui.label(t("masked_note")).classes("text-sm")
            with ui.row().classes("gap-3 items-center"):
                async def on_c(e: Any) -> None:
                    st.update(c=e.value, skip=0)
                    await rows.refresh()

                async def on_q(e: Any) -> None:
                    st.update(q=e.value or "", skip=0)
                    await rows.refresh()

                ui.select(list(COLLECTIONS), value="users", label=t("collection"), on_change=on_c) \
                    .props("outlined dense").classes("sf-input w-56")
                ui.input(placeholder="id / slug / user_id / status").props("outlined dense clearable debounce=400") \
                    .classes("sf-input w-72").on_value_change(on_q)
            with ui.column().classes("gap-2 w-full"):
                await rows()
    return None

# ============================================================================ audit


@ui.page("/admin/audit")
async def admin_audit() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    with admin_frame("adm_audit") as body:
        st: dict[str, Any] = {"action": "", "role": None, "skip": 0}

        @ui.refreshable
        async def rows() -> None:
            r = await api_client.call("GET", "/api/admin/audit", params={
                "action": st["action"] or None, "role": st["role"], "skip": st["skip"], "limit": 50})
            if not r.ok:
                notify_error(r)
                return
            d = r.data
            chip(t("chain_ok") if d["chain_ok"] else f"{t('chain_bad')} #{d['first_bad_seq']}",
                 "eligible" if d["chain_ok"] else "not_eligible", "verified")
            table = _table(["#", "When / कब", "Who / कौन", t("action"), "Target", t("reason"), ""])
            with table:
                for e in d["entries"]:
                    with ui.element("tr"):
                        _td(e["seq"])
                        _td(fmt_dt(e["ts"]))
                        _td(f"{e['actor'].get('login_id')} ({e['actor'].get('role')})")
                        _td(e["action"])
                        _td(f"{e['target'].get('type', '')}:{str(e['target'].get('id', ''))[:12]}" if e.get("target") else "—")
                        _td(e.get("reason") or "—")
                        with _td():
                            if e.get("before") is not None or e.get("after") is not None:
                                with ui.button(icon="compare_arrows").props("flat round dense"):
                                    with ui.menu().classes("p-3 max-w-[520px]"):
                                        ui.code(json.dumps({"before": e.get("before"), "after": e.get("after")},
                                                           ensure_ascii=False, indent=2, default=str), language="json")
            with ui.row().classes("gap-2"):
                async def page(delta: int) -> None:
                    st["skip"] = max(0, st["skip"] + delta)
                    await rows.refresh()
                if st["skip"] > 0:
                    ui.button(t("prev"), on_click=lambda: page(-50)).props("flat no-caps")
                if st["skip"] + 50 < d["total"]:
                    ui.button(t("next"), on_click=lambda: page(50)).props("flat no-caps")

        with body:
            with ui.row().classes("gap-3 items-center"):
                async def on_a(e: Any) -> None:
                    st.update(action=e.value or "", skip=0)
                    await rows.refresh()

                async def on_r(e: Any) -> None:
                    st.update(role=None if e.value == "all" else e.value, skip=0)
                    await rows.refresh()

                ui.input(placeholder="officer. / scheme. / data.reveal / application.").props(
                    "outlined dense clearable debounce=400").classes("sf-input w-80").on_value_change(on_a)
                ui.select({"all": t("all"), "admin": "admin", "officer": "officer", "user": "user", "system": "system"},
                          value="all", label=t("actor_role"), on_change=on_r).props("outlined dense").classes("sf-input w-44")
            with ui.column().classes("sf-card p-4 gap-3 w-full"):
                await rows()
    return None

# ============================================================================ analytics


@ui.page("/admin/analytics")
async def admin_analytics() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    r = await api_client.call("GET", "/api/admin/analytics")
    with admin_frame("adm_analytics"):
        if not r.ok:
            notify_error(r)
            return None
        a = r.data
        pct = lambda v: "—" if v is None else f"{round(v * 100)}%"  # noqa: E731
        with ui.element("div").classes("grid grid-cols-2 xl:grid-cols-4 gap-4 w-full"):
            for label, value in ((t("ai_agreement"), pct(a["ai"]["agreement_rate"])),
                                 (t("ai_accuracy"), pct(a["ai"]["definite_suggestion_accuracy"])),
                                 (t("override_rate"), pct(a["ai"]["override_rate"])),
                                 (t("assistant_stats") + " · " + t("not_sure"), pct(a["assistant"]["abstention_rate"]))):
                with ui.column().classes("sf-card p-5 gap-1"):
                    ui.label(value).classes("sf-title")
                    ui.label(label).classes("sf-muted text-sm")
        with ui.column().classes("sf-card p-5 gap-3 w-full"):
            ui.label(t("apps_title")).classes("font-bold text-lg")
            with ui.row().classes("gap-2"):
                for k, v in a["status_counts"].items():
                    if v:
                        chip(f"{k}: {v}", "neutral")
            table = _table([t("scheme"), "Total", t("approved"), t("rejected"), "Open", t("approval_rate")])
            with table:
                for row in a["applications_by_scheme"]:
                    with ui.element("tr"):
                        _td(L(row["name"]))
                        _td(row["total"])
                        _td(row["approved"])
                        _td(row["rejected"])
                        _td(row["open"])
                        with _td():
                            if row["approval_rate"] is not None:
                                ui.linear_progress(row["approval_rate"], show_value=False).props("rounded size=10px").classes("w-28")
                            ui.label(pct(row["approval_rate"])).classes("text-xs")
        with ui.element("div").classes("grid md:grid-cols-2 gap-5 w-full"):
            with ui.column().classes("sf-card p-5 gap-2"):
                ui.label(t("adm_officers")).classes("font-bold text-lg")
                table = _table([t("full_name"), t("decision"), t("override_rate")])
                with table:
                    for o in a["officers"]:
                        with ui.element("tr"):
                            _td(o["name"])
                            _td(o["decisions"])
                            _td(pct(o["override_rate"]))
            with ui.column().classes("sf-card p-5 gap-2"):
                ui.label(t("assistant_stats")).classes("font-bold text-lg")
                s = a["assistant"]
                for label, v in (("Questions", s["questions"]), (t("not_sure"), s["abstained"]), ("👍", s["thumbs_up"]),
                                 ("👎", s["thumbs_down"]), (t("users"), a["users"]["citizens"]),
                                 (t("flagged_only"), a["users"]["flagged"])):
                    with ui.row().classes("justify-between w-full"):
                        ui.label(str(label)).classes("sf-muted")
                        ui.label(str(v)).classes("font-semibold")
    return None
