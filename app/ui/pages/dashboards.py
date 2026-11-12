"""Role landing pages: /dashboard (citizen), /officer/dashboard, /admin (console, step-1/2 scope)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import ui

from app.ui import api_client
from app.ui.i18n import L, error_text, lang, t
from app.ui.theme import (
    category_badge, chip, fmt_date, frame, guard, icon_tile, link_button, notify_error, status_chip,
)


def _change_password_card() -> None:
    with ui.column().classes("sf-card p-6 gap-3 w-full"):
        ui.label(t("change_pw")).classes("sf-h3")
        cur = ui.input(t("current_password"), password=True).props("outlined dense autocomplete=current-password") \
            .classes("sf-input w-full")
        new = ui.input(t("new_password"), password=True, password_toggle_button=True) \
            .props("outlined dense autocomplete=new-password").classes("sf-input w-full")
        ui.label(t("pw_rules")).classes("text-xs sf-muted")

        async def submit() -> None:
            res = await api_client.call("POST", "/api/auth/change-password",
                                        json={"current_password": cur.value or "", "new_password": new.value or ""})
            if not res.ok:
                notify_error(res)
                return
            cur.value = new.value = ""
            ui.notify(t("pw_changed"), type="positive", position="top")

        ui.button(t("change_pw"), on_click=submit).props("unelevated no-caps").classes("sf-btn self-start")


def _account_card(me: dict[str, Any], extra: list[tuple[str, str]] | None = None) -> None:
    with ui.column().classes("sf-card p-6 gap-3 w-full"):
        ui.label(t("account")).classes("sf-h3")
        rows = [(t("full_name"), me["full_name"]), (t("login_id"), me["login_id"]),
                (t("status"), t(me["status"])), (t("created"), fmt_date(me["created_at"]))] + (extra or [])
        for label, value in rows:
            with ui.row().classes("justify-between w-full gap-4 border-b border-[var(--sf-line)] pb-2"):
                ui.label(label).classes("sf-muted")
                ui.label(value).classes("font-medium text-right")


@ui.page("/dashboard")
async def user_dashboard() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    me = await api_client.call("GET", "/api/auth/me")
    if not me.ok:
        return RedirectResponse("/login", status_code=303)
    m = me.data
    if not m.get("consent"):
        return RedirectResponse("/start", status_code=303)
    prof = await api_client.call("GET", "/api/me/profile")
    docs = await api_client.call("GET", "/api/documents")
    my_apps = await api_client.call("GET", "/api/applications")
    reco = await api_client.call("GET", "/api/recommendations", params={"include_not_eligible": "false"})
    completeness = prof.data["completeness"] if prof.ok else 0
    has_profile = bool(prof.ok and prof.data.get("eligibility"))
    doc_rows = docs.data if docs.ok else []
    app_rows = my_apps.data if my_apps.ok else []
    hi = lang() == "hi"
    first = (m["full_name"].split(" ") or [""])[0]

    steps = [
        (completeness >= 60, "person", t("nav_profile"),
         f"{t('completeness')}: {completeness}%", "/me" if completeness else "/interview"),
        (bool(doc_rows), "upload_file", t("upload_doc"),
         f"{len(doc_rows)} " + ("दस्तावेज़ अपलोड" if hi else "uploaded"), "/documents"),
        (bool(doc_rows) and all(d["status"] == "confirmed" for d in doc_rows), "fact_check", t("review_confirm"),
         ("दस्तावेज़ों से पढ़े गए फ़ील्ड जाँचें" if hi else "Check the fields we read from each document"), "/documents"),
        (bool(app_rows), "send", t("apply"),
         ("किसी सुझाई योजना के लिए आवेदन करें" if hi else "Apply for a recommended scheme"), "/recommendations"),
    ]
    done = sum(1 for s in steps if s[0])

    with frame(t("nav_dashboard"), active="/dashboard"):
        # ---------------------------------------------------------------- welcome band
        with ui.element("div").classes("sf-band p-6 md:p-8 grid md:grid-cols-[1fr_auto] gap-6 items-center w-full"):
            with ui.column().classes("gap-2"):
                ui.label(f"{t('hello')}, {first}").classes("sf-serif text-3xl md:text-4xl font-extrabold")
                ui.label("आपकी प्रोफ़ाइल जितनी पूरी, सुझाव उतने सटीक।" if hi
                         else "The more complete your profile, the more accurate your matches.") \
                    .classes("opacity-85 text-lg")
                with ui.row().classes("gap-3 mt-2"):
                    ui.button(t("cta_find") + "  →", on_click=lambda: ui.navigate.to("/interview")) \
                        .props("unelevated no-caps color=white text-color=primary").classes("sf-btn")
                    ui.button(t("nav_reco"), icon="recommend", on_click=lambda: ui.navigate.to("/recommendations")) \
                        .props("outline no-caps color=white").classes("sf-btn")
            with ui.element("div").classes("relative w-[120px] h-[120px] mx-auto"):
                ui.circular_progress(completeness, min=0, max=100, show_value=False, size="120px", color="orange") \
                    .props("thickness=0.13 track-color=white").classes("absolute inset-0 opacity-95")
                with ui.column().classes("absolute inset-0 items-center justify-center gap-0"):
                    ui.label(f"{completeness}%").classes("sf-serif text-2xl font-extrabold")
                    ui.label("प्रोफ़ाइल" if hi else "profile").classes("text-xs opacity-85")

        with ui.element("div").classes("grid lg:grid-cols-[1.1fr_1fr] gap-6 w-full items-start"):
            # ------------------------------------------------------------ next steps
            with ui.column().classes("sf-card p-6 gap-4 w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("अगले कदम" if hi else "Your next steps").classes("sf-h3 text-[1.45rem]")
                    chip(f"{done}/{len(steps)}", "eligible" if done == len(steps) else "maybe")
                ui.linear_progress(done / len(steps), show_value=False).props("rounded size=8px color=secondary")
                for ok, icon, title, desc, href in steps:
                    with ui.link(target=href).classes("no-underline text-inherit w-full"):
                        with ui.row().classes("items-center gap-3 no-wrap w-full p-2 rounded-xl hover:bg-[var(--sf-cream)]"):
                            if ok:
                                icon_tile("check", "#ffffff", "#2f7d4f", 40)
                            else:
                                icon_tile(icon, "#e0761b", "#fdf0e3", 40)
                            with ui.column().classes("gap-0 flex-grow min-w-0"):
                                ui.label(title).classes("font-semibold" + (" line-through sf-muted" if ok else ""))
                                ui.label(desc).classes("text-sm sf-muted")
                            ui.icon("chevron_right").classes("sf-muted")
            # ------------------------------------------------------------ top matches
            with ui.column().classes("sf-card p-6 gap-3 w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("आपके लिए सबसे अच्छी योजनाएँ" if hi else "Best matches for you").classes("sf-h3 text-[1.45rem]")
                    ui.link(t("see_all"), "/recommendations").classes("sf-link text-sm")
                items = (reco.data["items"] if reco.ok else [])[:4] if has_profile else []
                if not items:
                    with ui.column().classes("items-center text-center gap-3 py-6 w-full"):
                        icon_tile("quiz", "#0f4c35", "#e7f1eb", 56)
                        ui.label(t("reco_empty")).classes("sf-muted text-sm max-w-sm")
                        link_button(t("cta_find"), "/interview")
                for it in items:
                    sc = it["scheme"]
                    with ui.link(target=f"/scheme/{sc['slug']}").classes("no-underline text-inherit w-full"):
                        with ui.row().classes("items-center gap-3 no-wrap w-full p-2 rounded-xl hover:bg-[var(--sf-cream)]"):
                            category_badge(sc["category"], 42)
                            with ui.column().classes("gap-0 flex-grow min-w-0"):
                                ui.label(L(sc["name"])).classes("font-semibold leading-snug")
                                ui.label(L(sc["benefit"])).classes("text-sm sf-muted line-clamp-1")
                            status_chip(it["result"]["status"])

        # ---------------------------------------------------------------- quick actions
        with ui.element("div").classes("grid grid-cols-2 lg:grid-cols-4 gap-4 w-full"):
            for icon, title, href, color, tint in (
                    ("savings", t("nav_planner"), "/planner", "#b45309", "#fdf1e1"),
                    ("forum", t("nav_assistant"), "/assistant", "#1d4ed8", "#e7eefc"),
                    ("travel_explore", t("nav_explore"), "/explore", "#0f4c35", "#e7f1eb"),
                    ("shield", t("nav_scam"), "/scam-check", "#b3412e", "#fbeae6")):
                with ui.link(target=href).classes("no-underline text-inherit"):
                    with ui.row().classes("sf-card sf-card-hover p-4 gap-3 items-center no-wrap h-full"):
                        icon_tile(icon, color, tint, 44)
                        ui.label(title).classes("font-semibold leading-snug")

        # ---------------------------------------------------------------- applications
        if app_rows:
            from app.ui.pages.applications import status_chip_text

            with ui.column().classes("gap-3 w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(t("apps_title")).classes("sf-h3 text-[1.45rem]")
                    ui.link(t("see_all"), "/applications").classes("sf-link text-sm")
                with ui.element("div").classes("grid md:grid-cols-3 gap-4 w-full"):
                    for a in app_rows[:6]:
                        with ui.link(target=f"/applications#{a['id']}").classes("no-underline text-inherit"):
                            with ui.column().classes("sf-card sf-card-hover p-4 gap-2 h-full"):
                                ui.label(L(a["scheme_name"])).classes("font-bold leading-snug")
                                status_chip_text(a)
                                ui.label(fmt_date(str(a["updated_at"]))).classes("text-xs sf-muted")

        # ---------------------------------------------------------------- account (folded away)
        with ui.expansion("खाता और सुरक्षा" if hi else "Account & security", icon="manage_accounts") \
                .classes("sf-card w-full"):
            with ui.element("div").classes("grid md:grid-cols-2 gap-5 w-full p-2"):
                consent = m.get("consent") or {}
                mode = {"save_profile": t("consent_save"), "use_once": t("consent_once")}.get(consent.get("mode"), "—")
                with ui.column().classes("gap-2 w-full"):
                    _account_card(m, [(t("consent_mode"), mode)])
                    ui.link(t("change_choice") + " →", "/start").classes("sf-link text-sm")
                _change_password_card()
    return None
