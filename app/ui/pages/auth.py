"""Login pages for the three roles, registration with consent, and password reset."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi.responses import RedirectResponse
from nicegui import ui

from app.ui import api_client
from app.ui.i18n import error_text, lang, t
from app.ui.theme import frame, hero_src, home_for_role

PORTAL_HOME = {"user": "/dashboard", "officer": "/officer/dashboard", "admin": "/admin"}


def _safe_next(next_: str | None, portal: str) -> str:
    """Only allow same-site relative redirects that belong to the portal."""
    if not next_ or not next_.startswith("/") or next_.startswith("//") or urlparse(next_).netloc:
        return PORTAL_HOME[portal]
    if portal == "user" and (next_.startswith("/admin") or next_.startswith("/officer")):
        return PORTAL_HOME[portal]
    return next_


def _side_panel(portal: str) -> None:
    dark = portal == "admin"
    with ui.column().classes(("sf-admin-side" if dark else "sf-auth-side") +
                             " p-8 lg:p-10 gap-6 justify-between max-md:hidden"):
        with ui.column().classes("gap-4"):
            ui.html('<div class="sf-logo" style="background:#fff;color:var(--sf-green)">यो</div>', sanitize=False)
            ui.label({"user": t("login_title_user"), "officer": t("login_title_officer"),
                      "admin": t("login_title_admin")}[portal]).classes("sf-serif text-3xl font-extrabold leading-tight")
            ui.label({"user": t("login_sub_user"), "officer": t("login_sub_officer"),
                      "admin": t("login_sub_admin")}[portal]).classes("opacity-90 text-lg")
        points = {
            "user": [("rule", "Rule-by-rule reasons", "हर नियम के साथ कारण"),
                     ("translate", "Hindi and English", "हिंदी और अंग्रेज़ी"),
                     ("lock", "Aadhaar never stored in full", "पूरा आधार कभी नहीं रखा जाता")],
            "officer": [("fact_check", "AI advises, you decide", "AI सलाह देता है, निर्णय आपका"),
                        ("history", "Every decision recorded", "हर निर्णय दर्ज होता है"),
                        ("visibility_off", "Sensitive fields masked", "संवेदनशील जानकारी छिपी रहती है")],
            "admin": [("policy", "Hash-chained audit log", "हैश-शृंखला वाला ऑडिट लॉग"),
                      ("history_edu", "Versioned scheme rules", "संस्करण वाले योजना नियम"),
                      ("shield", "Masked by default", "डिफ़ॉल्ट रूप से छिपा डेटा")],
        }[portal]
        with ui.column().classes("gap-3"):
            for icon, en, hi in points:
                with ui.row().classes("items-center gap-3 no-wrap"):
                    ui.icon(icon, size="22px").classes("opacity-90")
                    ui.label(hi if lang() == "hi" else en).classes("font-medium")
        if portal == "user":
            ui.image(hero_src()).classes("rounded-2xl opacity-95")


def _login_page(portal: str, next_: str | None) -> RedirectResponse | None:
    current = api_client.role()
    if current == portal:
        return RedirectResponse(_safe_next(next_, portal), status_code=303)

    with frame({"user": t("login"), "officer": t("login_title_officer"), "admin": t("login_title_admin")}[portal]):
        with ui.element("div").classes("sf-card overflow-hidden grid md:grid-cols-2 w-full max-w-4xl mx-auto my-6"):
            _side_panel(portal)
            with ui.column().classes("p-8 lg:p-10 gap-4 justify-center"):
                if portal != "user":
                    ui.label({"officer": "OFFICER PORTAL · अधिकारी", "admin": "ADMIN · एडमिन"}[portal]).classes("sf-kicker")
                ui.label({"user": t("login_title_user"), "officer": t("login_title_officer"),
                          "admin": t("login_title_admin")}[portal]).classes("sf-title md:hidden")
                ui.label(t("login")).classes("sf-serif text-2xl font-bold max-md:hidden")
                if current:
                    ui.label(("You are signed in as another role. Logging in here will replace that session."
                              if lang() == "en" else "आप किसी दूसरी भूमिका से लॉग इन हैं। यहाँ लॉग इन करने से वह सत्र बदल जाएगा।")
                             ).classes("text-sm sf-muted")
                login_id = ui.input(t("login_id")).props("outlined autocomplete=username").classes("sf-input w-full")
                password = ui.input(t("password"), password=True, password_toggle_button=True) \
                    .props("outlined autocomplete=current-password").classes("sf-input w-full")
                otp = ui.input(t("twofa_code")).props("outlined maxlength=6 inputmode=numeric autocomplete=one-time-code") \
                    .classes("sf-input w-full")
                otp.set_visibility(False)
                error = ui.label("").classes("text-[var(--sf-red)] text-sm font-medium")
                error.set_visibility(False)

                async def submit() -> None:
                    error.set_visibility(False)
                    if not login_id.value or not password.value:
                        error.text = error_text("invalid_credentials")
                        error.set_visibility(True)
                        return
                    btn.props("loading")
                    body = {"login_id": login_id.value.strip(), "password": password.value, "portal": portal}
                    if otp.visible and otp.value:
                        body["otp"] = otp.value.strip()
                    res = await api_client.call("POST", "/api/auth/login", auth=False, json=body)
                    btn.props(remove="loading")
                    if not res.ok:
                        error.text = error_text(res.code, res.detail)
                        error.set_visibility(True)
                        if res.code == "otp_required":
                            otp.set_visibility(True)
                        else:
                            password.value = "" if res.code != "invalid_credentials" or not otp.visible else password.value
                        return
                    api_client.clear_session()
                    api_client.store_tokens(res.data)
                    me = await api_client.call("GET", "/api/auth/me")
                    if me.ok:
                        api_client.session()["name"] = me.data["full_name"]
                        if portal == "user" and "lang" not in api_client.session():
                            api_client.session()["lang"] = me.data.get("preferred_language", "en")
                    ui.navigate.to(_safe_next(next_, portal))

                password.on("keydown.enter", submit)
                btn = ui.button(t("login"), on_click=submit).props("unelevated no-caps").classes("sf-btn w-full")
                if portal == "user":
                    with ui.row().classes("w-full justify-between text-sm"):
                        ui.link(t("forgot"), "/forgot-password").classes("sf-link")
                        with ui.row().classes("gap-1"):
                            ui.label(t("no_account")).classes("sf-muted")
                            ui.link(t("register"), "/register").classes("sf-link")
                else:
                    ui.label(t("reset_staff_note")).classes("text-sm sf-muted")
    return None


@ui.page("/login")
async def login_user(next: str | None = None) -> RedirectResponse | None:  # noqa: A002 - query param name
    return _login_page("user", next)


@ui.page("/officer/login")
async def login_officer(next: str | None = None) -> RedirectResponse | None:  # noqa: A002
    return _login_page("officer", next)


@ui.page("/admin/login")
async def login_admin(next: str | None = None) -> RedirectResponse | None:  # noqa: A002
    return _login_page("admin", next)


@ui.page("/register")
async def register_page() -> RedirectResponse | None:
    if api_client.role():
        return RedirectResponse(home_for_role(api_client.role()), status_code=303)
    with frame(t("register")):
        with ui.element("div").classes("sf-card overflow-hidden grid md:grid-cols-2 w-full max-w-5xl mx-auto my-6"):
            with ui.column().classes("p-8 lg:p-10 gap-4"):
                ui.label(t("cta_register")).classes("sf-title")
                name = ui.input(t("full_name")).props("outlined autocomplete=name").classes("sf-input w-full")
                login_id = ui.input(t("login_id")).props("outlined autocomplete=username").classes("sf-input w-full")
                pw = ui.input(t("password"), password=True, password_toggle_button=True) \
                    .props("outlined autocomplete=new-password").classes("sf-input w-full")
                ui.label(t("pw_rules")).classes("text-sm sf-muted -mt-2")
                pref = ui.toggle({"hi": "हिंदी", "en": "English"}, value=lang()).props("no-caps unelevated rounded") \
                    .classes("border border-[var(--sf-line)] rounded-xl")
                ui.label(t("pref_lang")).classes("text-sm sf-muted -mt-2")
            with ui.column().classes("p-8 lg:p-10 gap-4 bg-[var(--sf-cream-2)]"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("privacy_tip", size="26px").classes("text-[var(--sf-green)]")
                    ui.label(t("consent_title")).classes("sf-h3 text-[1.45rem]")
                ui.label(t("consent_body")).classes("leading-relaxed")
                mode = ui.radio({"save_profile": t("consent_save"), "use_once": t("consent_once")},
                                value="save_profile").props("inline")
                accept = ui.checkbox(t("consent_accept"))
                error = ui.label("").classes("text-[var(--sf-red)] text-sm font-medium")
                error.set_visibility(False)

                async def submit() -> None:
                    error.set_visibility(False)
                    if not accept.value:
                        error.text = error_text("consent_required")
                        error.set_visibility(True)
                        return
                    res = await api_client.call("POST", "/api/auth/register", auth=False, json={
                        "login_id": (login_id.value or "").strip(), "full_name": (name.value or "").strip(),
                        "password": pw.value or "", "preferred_language": pref.value,
                        "consent_accepted": True, "consent_mode": mode.value})
                    if not res.ok:
                        msg = error_text(res.code, res.detail)
                        if res.code == "validation_error":
                            fields = {str(e["loc"][-1]) for e in res.detail.get("errors", []) if e.get("loc")}
                            msg += " (" + ", ".join(sorted(fields)) + ")"
                        error.text = msg
                        error.set_visibility(True)
                        return
                    api_client.session()["lang"] = pref.value
                    ui.notify(t("registered"), type="positive", position="top")
                    ui.navigate.to("/login")

                ui.button(t("create_account"), on_click=submit).props("unelevated no-caps").classes("sf-btn w-full")
                with ui.row().classes("gap-1 text-sm"):
                    ui.label(t("have_account")).classes("sf-muted")
                    ui.link(t("login"), "/login").classes("sf-link")
    return None


@ui.page("/forgot-password")
async def forgot_page() -> None:
    with frame(t("forgot")):
        with ui.column().classes("sf-card p-8 gap-4 w-full max-w-lg mx-auto my-6"):
            ui.label(t("reset_title")).classes("sf-title")
            login_id = ui.input(t("login_id")).props("outlined").classes("sf-input w-full")
            out = ui.column().classes("gap-2 w-full")

            async def send() -> None:
                out.clear()
                res = await api_client.call("POST", "/api/auth/password-reset/request", auth=False,
                                            json={"login_id": (login_id.value or "").strip()})
                with out:
                    if not res.ok:
                        ui.label(error_text(res.code, res.detail)).classes("text-[var(--sf-red)]")
                        return
                    ui.label(res.data["message"]).classes("sf-muted")
                    token = res.data.get("dev_reset_token")
                    if token:
                        with ui.column().classes("p-3 rounded-xl bg-[var(--sf-amber-50)] gap-1 w-full"):
                            ui.label(t("reset_dev_note")).classes("text-sm")
                            ui.link(t("reset_do") + " →", f"/reset-password?token={token}").classes("sf-link")

            ui.button(t("reset_send"), on_click=send).props("unelevated no-caps").classes("sf-btn")
            ui.label(t("reset_staff_note")).classes("text-sm sf-muted")


@ui.page("/reset-password")
async def reset_page(token: str = "") -> None:
    with frame(t("reset_title")):
        with ui.column().classes("sf-card p-8 gap-4 w-full max-w-lg mx-auto my-6"):
            ui.label(t("reset_title")).classes("sf-title")
            code = ui.input(t("reset_code"), value=token).props("outlined").classes("sf-input w-full")
            pw = ui.input(t("new_password"), password=True, password_toggle_button=True) \
                .props("outlined autocomplete=new-password").classes("sf-input w-full")
            ui.label(t("pw_rules")).classes("text-sm sf-muted -mt-2")

            async def submit() -> None:
                res = await api_client.call("POST", "/api/auth/password-reset/confirm", auth=False,
                                            json={"token": (code.value or "").strip(), "new_password": pw.value or ""})
                if not res.ok:
                    ui.notify(error_text(res.code, res.detail), type="negative", position="top")
                    return
                ui.notify(t("reset_done"), type="positive", position="top")
                ui.navigate.to("/login")

            ui.button(t("reset_do"), on_click=submit).props("unelevated no-caps").classes("sf-btn")
