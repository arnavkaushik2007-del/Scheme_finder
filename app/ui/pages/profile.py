"""/start (consent and privacy choice) and /me (saved profile, saved schemes, delete my data)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import ui

from app.ui import api_client
from app.ui.i18n import L, t
from app.ui.pages.check import load_vocab, profile_from_inputs, profile_inputs
from app.ui.theme import frame, guard, link_button, notify_error


@ui.page("/start")
async def start_page() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    prof = await api_client.call("GET", "/api/me/profile")
    consent = prof.data["consent"] if prof.ok else {}
    with frame(t("start_title"), active="/start"):
        ui.label(t("start_title")).classes("sf-title")
        with ui.element("div").classes("grid md:grid-cols-3 gap-5 w-full"):
            for icon, title, body in (("inventory_2", t("start_what"), t("start_what_d")),
                                      ("help_outline", t("start_why"), t("start_why_d")),
                                      ("schedule", t("start_how_long"), t("start_how_long_d"))):
                with ui.column().classes("sf-card p-6 gap-2"):
                    ui.icon(icon, size="28px").classes("text-[var(--sf-orange)]")
                    ui.label(title).classes("sf-h3")
                    ui.label(body)
        with ui.row().classes("items-center gap-3 p-4 rounded-2xl bg-[var(--sf-green-50)] w-full no-wrap"):
            ui.icon("lock", size="24px").classes("text-[var(--sf-green)]")
            ui.label(t("start_no_id")).classes("font-medium")

        with ui.column().classes("sf-card p-6 gap-4 w-full"):
            ui.label(t("start_current")).classes("sf-h3")
            mode = ui.radio({"save_profile": t("consent_save"), "use_once": t("consent_once")},
                            value=consent.get("mode") or "save_profile")
            accept = ui.checkbox(t("consent_accept"), value=bool(consent.get("accepted_at")))
            ui.label(t("consent_body")).classes("text-sm sf-muted")

            async def save() -> None:
                if not accept.value:
                    ui.notify(t("err_consent_required"), type="warning", position="top")
                    return
                res = await api_client.call("PUT", "/api/me/consent", json={"mode": mode.value, "accepted": True})
                if not res.ok:
                    notify_error(res)
                    return
                ui.notify(t("start_saved"), type="positive", position="top")
                ui.navigate.to("/dashboard")

            with ui.row().classes("gap-3"):
                ui.button(t("save"), on_click=save).props("unelevated no-caps").classes("sf-btn")

        with ui.column().classes("sf-card p-6 gap-3 w-full border-[var(--sf-red)]"):
            with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3 w-[min(460px,92vw)]"):
                ui.label(t("start_withdraw_confirm"))

                async def withdraw() -> None:
                    res = await api_client.call("POST", "/api/me/consent/withdraw")
                    dlg.close()
                    if res.ok:
                        ui.notify(t("start_saved"), type="positive", position="top")
                        ui.navigate.reload()
                    else:
                        notify_error(res)

                with ui.row().classes("justify-end w-full"):
                    ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
                    ui.button(t("start_withdraw"), on_click=withdraw).props("unelevated no-caps color=negative") \
                        .classes("sf-btn")
            ui.button(t("start_withdraw"), icon="remove_circle_outline", on_click=dlg.open) \
                .props("flat no-caps color=negative")
            ui.link(t("delete_data") + " →", "/me#delete").classes("sf-link text-sm")
    return None


PERSONAL = [("full_name", "full_name"), ("date_of_birth", "dob"), ("aadhaar_last4", "aadhaar_last4"),
            ("district", "district"), ("address", "address")]


@ui.page("/me")
async def me_page() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    prof_res = await api_client.call("GET", "/api/me/profile")
    if not prof_res.ok:
        return RedirectResponse("/login", status_code=303)
    prof = prof_res.data
    vocab = await load_vocab()
    once = prof["consent"].get("mode") != "save_profile" or bool(prof["consent"].get("withdrawn_at"))
    with frame(t("profile_title"), active="/me"):
        with ui.row().classes("w-full items-end justify-between"):
            with ui.column().classes("gap-1"):
                ui.label(t("profile_title")).classes("sf-title")
                ui.label(t("profile_sub")).classes("sf-muted")
            with ui.column().classes("items-end gap-1"):
                ui.label(f"{t('completeness')}: {prof['completeness']}%").classes("font-semibold")
                ui.linear_progress(prof["completeness"] / 100, show_value=False).classes("w-56") \
                    .props("rounded size=10px color=primary track-color=grey-3")
        if once:
            with ui.row().classes("items-center gap-3 p-4 rounded-2xl bg-[var(--sf-amber-50)] w-full no-wrap"):
                ui.icon("info", size="22px").classes("text-[var(--sf-amber)]")
                ui.label(t("once_note"))
                ui.link(t("change_choice") + " →", "/start").classes("sf-link ml-auto")

        with ui.element("div").classes("grid lg:grid-cols-2 gap-6 w-full items-start"):
            with ui.column().classes("sf-card p-6 gap-3"):
                ui.label(t("basics")).classes("sf-h3")
                inputs = profile_inputs(vocab, prof["eligibility"], prof["sources"], expanded=True)
            with ui.column().classes("gap-6"):
                with ui.column().classes("sf-card p-6 gap-3 w-full"):
                    ui.label(t("personal")).classes("sf-h3")
                    pins: dict[str, Any] = {}
                    for key, label in PERSONAL:
                        w = ui.input(t(label), value=prof["personal"].get(key) or "").props("outlined dense") \
                            .classes("sf-input w-full")
                        if key == "aadhaar_last4":
                            w.props("maxlength=4 inputmode=numeric")
                            if prof["personal"].get(key):
                                w.props(f'hint="XXXX XXXX {prof["personal"][key]}"')
                        if str(prof["sources"].get(key, "")).startswith("document:"):
                            w.props('hint="📄 ' + t("from_document") + '"')
                        pins[key] = w

                    async def save() -> None:
                        personal = {k: (w.value or "").strip() or None for k, w in pins.items()}
                        res = await api_client.call("PUT", "/api/me/profile", json={
                            "eligibility": profile_from_inputs(inputs, vocab),
                            "personal": {k: v for k, v in personal.items() if v is not None}})
                        if res.ok:
                            ui.notify(t("saved_ok"), type="positive", position="top")
                            ui.navigate.reload()
                        else:
                            notify_error(res)

                    ui.button(t("save_profile"), icon="save", on_click=save).props("unelevated no-caps") \
                        .classes("sf-btn").set_enabled(not once)
                    link_button(t("fill_from_doc"), "/documents", icon="upload_file", outline=True)

                await _checklist_card()

        with ui.column().classes("sf-card p-6 gap-3 w-full").props('id="delete"'):
            ui.label(t("delete_data")).classes("sf-serif text-xl font-bold text-[var(--sf-red)]")
            ui.label(t("delete_data_d"))
            with ui.row().classes("gap-3 items-start w-full"):
                pw = ui.input(t("password"), password=True).props("outlined dense autocomplete=current-password") \
                    .classes("sf-input w-64")
                word = ui.input(t("type_delete")).props("outlined dense").classes("sf-input w-56")

                async def delete_all() -> None:
                    res = await api_client.call("POST", "/api/me/delete-account",
                                                json={"password": pw.value or "", "confirm": (word.value or "").strip()})
                    if not res.ok:
                        notify_error(res)
                        return
                    api_client.clear_session()
                    ui.notify(t("deleted_bye"), type="positive", position="top")
                    ui.navigate.to("/")

                ui.button(t("delete"), icon="delete_forever", on_click=delete_all) \
                    .props("unelevated no-caps color=negative").classes("sf-btn")
    return None


async def _checklist_card() -> None:
    res = await api_client.call("GET", "/api/me/checklist")
    with ui.column().classes("sf-card p-6 gap-3 w-full"):
        ui.label(t("saved_schemes")).classes("sf-h3")
        if not res.ok or not res.data:
            ui.label(t("no_saved")).classes("sf-muted")
            return
        for item in res.data:
            with ui.expansion(f"{L(item['name'])} — {item['done']}/{item['total']}", icon="bookmark") \
                    .classes("w-full rounded-xl border border-[var(--sf-line)]"):
                for d in item["documents"]:
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("check_circle" if d["have"] else "radio_button_unchecked", size="20px") \
                            .classes("sf-rule-pass" if d["have"] else "sf-muted")
                        ui.label(L(d["name"]))
                ui.link(t("view_scheme") + " →", f"/scheme/{item['slug']}").classes("sf-link text-sm")
