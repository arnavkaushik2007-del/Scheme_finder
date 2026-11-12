"""Look and feel: fonts, colours, the prototype banner, header, footer and small UI helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import app, ui

from app.ui import api_client
from app.ui.i18n import L, lang, t

HEAD = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700;9..144,800&family=Hind:wght@400;500;600;700&family=Tiro+Devanagari+Hindi&display=swap" rel="stylesheet">
<style>
:root{
  --sf-green:#0f4c35; --sf-green-700:#0a3a28; --sf-green-50:#e7f1eb; --sf-green-100:#d3e6da;
  --sf-cream:#fbf6ec; --sf-cream-2:#f4ecdc; --sf-card:#fffdf8; --sf-line:#eadfca;
  --sf-orange:#e0761b; --sf-orange-50:#fdf0e3; --sf-ink:#1c2a22; --sf-muted:#5b6a61;
  --sf-red:#b3412e; --sf-red-50:#fbeae6; --sf-amber:#a86a00; --sf-amber-50:#fff4dc;
  --sf-shadow:0 1px 0 rgba(0,0,0,.02),0 10px 28px -18px rgba(60,40,10,.35);
}
html{font-size:17px;}
html[data-size="lg"]{font-size:19px;} html[data-size="xl"]{font-size:21px;}
html,body{background:var(--sf-cream);color:var(--sf-ink);font-family:'Hind','Tiro Devanagari Hindi',system-ui,sans-serif;overflow-x:hidden;}
body{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;}
.nicegui-content{padding:0!important;gap:0!important;}
.sf-serif{font-family:'Fraunces','Tiro Devanagari Hindi',Georgia,serif;letter-spacing:-0.01em;}
.sf-wrap{width:100%;max-width:1200px;margin:0 auto;padding-left:16px;padding-right:16px;}
@media (min-width:768px){.sf-wrap{padding-left:24px;padding-right:24px;}}

/* typography scale */
.sf-display{font-family:'Fraunces','Tiro Devanagari Hindi',serif;font-weight:800;color:var(--sf-green);line-height:1.04;letter-spacing:-.02em;font-size:clamp(2.35rem,6.2vw,4.3rem);}
html[lang="hi"] .sf-display{line-height:1.3;font-size:clamp(2rem,5vw,3.5rem);}
.sf-title{font-family:'Fraunces','Tiro Devanagari Hindi',serif;font-weight:800;color:var(--sf-green);line-height:1.12;letter-spacing:-.015em;font-size:clamp(1.85rem,3.6vw,2.6rem);}
html[lang="hi"] .sf-title{line-height:1.35;}
.sf-h3{font-family:'Fraunces','Tiro Devanagari Hindi',serif;font-weight:700;color:var(--sf-green);font-size:1.3rem;line-height:1.25;}
.sf-lead{font-size:1.15rem;line-height:1.65;color:var(--sf-muted);}

/* chrome */
.sf-banner{background:var(--sf-green);color:#fff;font-weight:600;font-size:13.5px;}
.sf-header{background:rgba(251,246,236,.9);backdrop-filter:saturate(1.4) blur(8px);border-bottom:1px solid var(--sf-line);position:sticky;top:0;z-index:50;}
.sf-logo{width:44px;height:44px;border-radius:13px;background:linear-gradient(150deg,#14613f,var(--sf-green-700));color:#fff;display:flex;align-items:center;justify-content:center;font-family:'Tiro Devanagari Hindi',serif;font-size:22px;box-shadow:inset 0 -2px 0 rgba(0,0,0,.15);flex-shrink:0;}
.sf-brand{white-space:nowrap;font-family:'Fraunces','Tiro Devanagari Hindi',serif;font-weight:800;font-size:23px;color:var(--sf-green);}
.sf-nav a,.sf-navlink{color:var(--sf-ink);font-weight:500;font-size:15px;white-space:nowrap;text-decoration:none;padding:7px 2px;border-bottom:2px solid transparent;transition:color .15s,border-color .15s;}
.sf-nav a:hover,.sf-navlink:hover{color:var(--sf-green);border-bottom-color:var(--sf-orange);}
.sf-nav a.active{color:var(--sf-green);border-bottom-color:var(--sf-green);font-weight:600;}
.sf-avatar{width:38px;height:38px;border-radius:999px;background:var(--sf-orange-50);color:var(--sf-orange);display:flex;align-items:center;justify-content:center;font-weight:700;border:1px solid #f3d9bd;}
.sf-footer{background:linear-gradient(180deg,var(--sf-cream-2),#efe4cf);border-top:1px solid var(--sf-line);}
.sf-footer a{color:var(--sf-ink);text-decoration:none;} .sf-footer a:hover{color:var(--sf-green);text-decoration:underline;}

/* surfaces */
.sf-card{background:var(--sf-card);border:1px solid var(--sf-line);border-radius:18px;box-shadow:var(--sf-shadow);}
.sf-card-hover{transition:transform .18s ease, box-shadow .18s ease, border-color .18s;}
.sf-card-hover:hover{transform:translateY(-3px);box-shadow:0 18px 34px -20px rgba(60,40,10,.45);border-color:#dccfb3;}
.sf-band{background:linear-gradient(135deg,var(--sf-green) 0%,var(--sf-green-700) 100%);color:#fff;border-radius:24px;}
.sf-pill{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--sf-line);background:#fffaf0;border-radius:999px;padding:6px 14px;font-weight:600;color:var(--sf-green);font-size:14px;}
.sf-muted{color:var(--sf-muted);}
.sf-kicker{font-size:12.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--sf-muted);font-weight:700;}
.sf-dots{background-image:radial-gradient(rgba(15,76,53,.08) 1px,transparent 1px);background-size:22px 22px;}
.sf-ico{display:flex;align-items:center;justify-content:center;border-radius:14px;flex-shrink:0;}

/* controls */
.sf-btn{border-radius:12px!important;font-weight:600!important;text-transform:none!important;padding:10px 22px!important;font-size:1rem!important;min-height:46px;}
.sf-btn-outline{background:#fffdf8!important;color:var(--sf-ink)!important;border:1px solid var(--sf-line)!important;}
.sf-input .q-field__control{border-radius:12px!important;background:#fffdf8;}
.sf-search .q-field__control{border-radius:16px!important;background:#fff;height:58px;box-shadow:var(--sf-shadow);}
.sf-chip{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:3px 12px;font-size:13px;font-weight:700;white-space:nowrap;}
.sf-chip-eligible{background:var(--sf-green-50);color:var(--sf-green);}
.sf-chip-maybe{background:var(--sf-amber-50);color:var(--sf-amber);}
.sf-chip-not_eligible{background:var(--sf-red-50);color:var(--sf-red);}
.sf-chip-neutral{background:var(--sf-cream-2);color:var(--sf-ink);}
.sf-rule-pass{color:var(--sf-green);} .sf-rule-fail{color:var(--sf-red);} .sf-rule-unknown{color:var(--sf-amber);}
.sf-num{font-family:'Fraunces',serif;font-weight:800;color:var(--sf-orange);font-size:40px;line-height:1;}
.sf-stat{font-family:'Fraunces',serif;font-weight:800;color:var(--sf-green);font-size:clamp(2rem,4vw,2.8rem);line-height:1;}
.sf-auth-side{background:linear-gradient(160deg,var(--sf-green) 0%,var(--sf-green-700) 100%);color:#fff;}
.sf-admin-side{background:linear-gradient(160deg,#2a2f2c 0%,#141816 100%);color:#fff;}
.sf-table th{font-size:12.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--sf-muted);text-align:left;padding:10px 12px;border-bottom:1px solid var(--sf-line);}
.sf-table td{padding:10px 12px;border-bottom:1px solid var(--sf-line);vertical-align:top;}
.sf-table tr:hover td{background:#fffaf0;}
a.sf-link{color:var(--sf-green);font-weight:600;text-decoration:none;} a.sf-link:hover{text-decoration:underline;}
.sf-cite{color:var(--sf-green);font-weight:700;margin-left:1px;}
.sf-fab{position:fixed;right:22px;bottom:22px;z-index:60;border-radius:999px!important;box-shadow:0 12px 28px -10px rgba(15,76,53,.6);}
:focus-visible{outline:3px solid var(--sf-orange)!important;outline-offset:2px;}

/* file drop zone (styled Quasar uploader) */
.sf-drop.q-uploader{width:100%;max-height:none;border:2px dashed #d8c7a3;border-radius:18px;background:#fffaf0;box-shadow:none;transition:border-color .15s,background .15s;}
.sf-drop.q-uploader:hover{border-color:var(--sf-green);background:#f6fbf7;}
.sf-drop .q-uploader__header{background:transparent!important;color:var(--sf-green)!important;padding:26px 16px;}
.sf-drop .q-uploader__header-content{justify-content:center;align-items:center;gap:6px;}
.sf-drop .q-uploader__title{font-weight:700;font-size:1.02rem;white-space:normal;text-align:center;}
.sf-drop .q-uploader__subtitle{display:none;}
.sf-drop .q-uploader__list{min-height:0;padding:0 12px;}
.sf-drop .q-uploader__header .q-btn{background:var(--sf-green);color:#fff;}

/* gentle entrance */
@keyframes sfUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.sf-card,.sf-band,.sf-rise{animation:sfUp .38s ease both;}
@media (prefers-reduced-motion:reduce){.sf-card,.sf-band,.sf-rise{animation:none}.sf-card-hover:hover{transform:none}}

@media (max-width:640px){
  html{font-size:16px;} html[data-size="lg"]{font-size:18px;} html[data-size="xl"]{font-size:20px;}
  .sf-num{font-size:32px;} .sf-brand{font-size:20px;} .sf-logo{width:38px;height:38px;font-size:19px;border-radius:11px;}
  .sf-banner{font-size:12px;} .sf-fab{right:14px;bottom:14px;}
  .sf-btn{padding:10px 18px!important;}
}
</style>
"""

SIZES = ("md", "lg", "xl")


def setup_page(title: str = "") -> None:
    ui.add_head_html(HEAD)
    ui.colors(primary="#0f4c35", secondary="#e0761b", accent="#e0761b", positive="#2f7d4f",
              negative="#b3412e", warning="#a86a00", info="#3f8a87")
    ui.page_title(f"{title} · {t('brand')}" if title else f"{t('brand')} — find government schemes")
    size = app.storage.user.get("size", "md")
    ui.query("html").props(f'lang="{lang()}" data-size="{size}"')


def toggle_language() -> None:
    app.storage.user["lang"] = "hi" if lang() == "en" else "en"
    ui.navigate.reload()


def cycle_text_size() -> None:
    """Larger text for people who need it (spec: readable with large text)."""
    cur = app.storage.user.get("size", "md")
    nxt = SIZES[(SIZES.index(cur) + 1) % len(SIZES)] if cur in SIZES else "lg"
    app.storage.user["size"] = nxt
    ui.run_javascript(f"document.documentElement.setAttribute('data-size','{nxt}')")


async def do_logout() -> None:
    r = api_client.role()
    await api_client.logout()
    ui.navigate.to({"officer": "/officer/login", "admin": "/admin/login"}.get(r or "", "/login"))


NAV_ICONS = {"/dashboard": "space_dashboard", "/recommendations": "recommend", "/documents": "description",
             "/applications": "assignment", "/assistant": "forum", "/planner": "savings", "/scam-check": "shield",
             "/me": "person", "/officer/dashboard": "inbox", "/officer/history": "history", "/officer/stats": "insights",
             "/explore": "travel_explore", "/admin": "admin_panel_settings", "/admin/users": "groups",
             "/admin/audit": "receipt_long", "/": "home", "/updates": "campaign", "/about": "info",
             "/interview": "quiz"}


def _nav_items() -> list[tuple[str, str]]:
    r = api_client.role()
    if r == "user":
        return [(t("nav_dashboard"), "/dashboard"), (t("nav_reco"), "/recommendations"),
                (t("nav_documents"), "/documents"), (t("nav_apps"), "/applications"),
                (t("nav_assistant"), "/assistant"), (t("nav_planner"), "/planner"), (t("nav_scam"), "/scam-check")]
    if r == "officer":
        return [(t("off_queue"), "/officer/dashboard"), (t("off_history"), "/officer/history"),
                (t("off_stats"), "/officer/stats"), (t("nav_assistant"), "/assistant"), (t("nav_explore"), "/explore")]
    if r == "admin":
        return [(t("nav_admin"), "/admin"), (t("adm_users"), "/admin/users"), (t("adm_audit"), "/admin/audit"),
                (t("nav_assistant"), "/assistant"), (t("nav_explore"), "/explore")]
    return [(t("nav_home"), "/"), (t("nav_scam"), "/scam-check"), (t("nav_updates"), "/updates"), (t("nav_about"), "/about")]


def _banner() -> None:
    with ui.element("div").classes("sf-banner w-full"):
        with ui.row().classes("sf-wrap items-center justify-center gap-2 py-2 text-center no-wrap"):
            ui.icon("info", size="17px").classes("text-amber-300 shrink-0")
            ui.label(t("banner")).classes("leading-snug")


def _header(active: str) -> None:
    items = _nav_items()
    logged_in = api_client.is_logged_in()
    name = (api_client.session().get("name") or "").strip() if logged_in else ""
    with ui.element("header").classes("sf-header w-full"):
        with ui.row().classes("sf-wrap items-center justify-between py-3 no-wrap gap-3"):
            with ui.link(target="/" if not api_client.role() else items[0][1]).classes("no-underline min-w-0"):
                with ui.row().classes("items-center gap-3 no-wrap"):
                    ui.html('<div class="sf-logo">यो</div>', sanitize=False)
                    ui.label(t("brand")).classes("sf-brand")
            with ui.row().classes("sf-nav items-center gap-5 no-wrap max-xl:hidden"):
                for label, href in items:
                    ui.link(label, href).classes("active" if href == active else "")
            with ui.row().classes("items-center gap-1 sm:gap-2 no-wrap shrink-0"):
                ui.button(icon="format_size", on_click=cycle_text_size).props('flat round dense aria-label="Text size"') \
                    .classes("text-[var(--sf-ink)] max-sm:hidden").tooltip("A / A+ / A++")
                ui.button(t("lang_switch"), icon="translate", on_click=toggle_language) \
                    .props("flat no-caps dense").classes("text-[var(--sf-ink)] font-semibold px-2") \
                    .tooltip("Switch language / भाषा बदलें")
                if api_client.role() == "user":
                    _bell()
                if logged_in:
                    with ui.button().props("flat round dense").classes("max-xl:hidden"):
                        ui.html(f'<div class="sf-avatar">{(name[:1] or "?").upper()}</div>', sanitize=False)
                        with ui.menu().classes("min-w-[220px]"):
                            ui.label(name or "—").classes("px-4 pt-3 font-semibold")
                            ui.label({"user": "", "officer": "OFFICER", "admin": "ADMIN"}.get(api_client.role() or "", "")) \
                                .classes("px-4 sf-kicker")
                            ui.separator()
                            if api_client.role() == "user":
                                ui.menu_item(t("nav_profile"), on_click=lambda: ui.navigate.to("/me"))
                                ui.menu_item(t("nav_explore"), on_click=lambda: ui.navigate.to("/explore"))
                            ui.menu_item("A / A+ / A++", on_click=cycle_text_size)
                            ui.menu_item(t("logout"), on_click=do_logout)
                else:
                    ui.link(t("login"), "/login").classes("sf-navlink font-semibold px-2 max-sm:hidden")
                    ui.button(t("register"), on_click=lambda: ui.navigate.to("/register")) \
                        .props("unelevated no-caps").classes("sf-btn max-sm:hidden")
                with ui.button(icon="menu").props('flat round aria-label="Menu"').classes("xl:hidden"):
                    with ui.menu().classes("min-w-[240px]"):
                        if name:
                            ui.label(name).classes("px-4 pt-3 pb-1 font-semibold")
                        for label, href in items + ([(t("nav_profile"), "/me"), (t("nav_explore"), "/explore")]
                                                    if api_client.role() == "user" else []):
                            with ui.menu_item(on_click=lambda h=href: ui.navigate.to(h)):
                                with ui.row().classes("items-center gap-3 no-wrap"):
                                    ui.icon(NAV_ICONS.get(href, "chevron_right"), size="20px").classes(
                                        "text-[var(--sf-green)]" if href == active else "sf-muted")
                                    ui.label(label).classes("font-semibold" if href == active else "")
                        ui.separator()
                        with ui.menu_item(on_click=cycle_text_size):
                            with ui.row().classes("items-center gap-3"):
                                ui.icon("format_size", size="20px").classes("sf-muted")
                                ui.label("A / A+ / A++")
                        if logged_in:
                            with ui.menu_item(on_click=do_logout):
                                with ui.row().classes("items-center gap-3"):
                                    ui.icon("logout", size="20px").classes("sf-muted")
                                    ui.label(t("logout"))
                        else:
                            ui.menu_item(t("login"), on_click=lambda: ui.navigate.to("/login"))
                            ui.menu_item(t("register"), on_click=lambda: ui.navigate.to("/register"))


def _bell() -> None:
    """Notification bell: unread count badge and a menu of recent status changes."""

    async def load(menu_col: ui.column, badge: ui.badge) -> None:
        res = await api_client.call("GET", "/api/notifications")
        menu_col.clear()
        if not res.ok:
            return
        badge.set_text(str(res.data["unread"]))
        badge.set_visibility(res.data["unread"] > 0)
        with menu_col:
            ui.label(t("notifications")).classes("font-bold px-2")
            if not res.data["items"]:
                ui.label(t("no_notifications")).classes("sf-muted text-sm px-2")
            for n in res.data["items"][:8]:
                unread = "bg-[var(--sf-green-50)]" if not n["read"] else ""
                with ui.column().classes(f"gap-0 px-2 py-1 rounded-lg {unread}"):
                    ui.link(L(n["title"]), n.get("link") or f"/applications#{n['application_id']}") \
                        .classes("text-sm font-semibold no-underline text-[var(--sf-ink)]")
                    if n.get("note") and n["status"] != "precheck_done":
                        ui.label(n["note"]).classes("text-xs sf-muted")
                    ui.label(fmt_dt(n["created_at"])).classes("text-[11px] sf-muted")

            async def read_all() -> None:
                await api_client.call("POST", "/api/notifications/read")
                await load(menu_col, badge)

            if res.data["unread"]:
                ui.button(t("mark_read"), on_click=read_all).props("flat dense no-caps").classes("self-end")

    with ui.button(icon="notifications").props("flat round").classes("text-[var(--sf-ink)]"):
        badge = ui.badge("0", color="orange").props("floating rounded")
        badge.set_visibility(False)
        with ui.menu().classes("w-[320px] max-h-[420px]"):
            col = ui.column().classes("gap-1 p-2 w-full")
    ui.timer(0.1, lambda: load(col, badge), once=True)


def _footer() -> None:
    hi = lang() == "hi"
    with ui.element("footer").classes("sf-footer w-full mt-20"):
        with ui.element("div").classes("sf-wrap grid gap-8 py-12 md:grid-cols-[1.4fr_1fr_1fr_1.2fr]"):
            with ui.column().classes("gap-3"):
                with ui.row().classes("items-center gap-3 no-wrap"):
                    ui.html('<div class="sf-logo" style="width:38px;height:38px;font-size:18px">यो</div>', sanitize=False)
                    ui.label(t("brand")).classes("sf-brand text-xl")
                ui.label(t("footer_note")).classes("sf-muted text-sm leading-relaxed")
            with ui.column().classes("gap-2 text-sm"):
                ui.label("योजनाएँ" if hi else "Schemes").classes("sf-kicker mb-1")
                ui.link(t("nav_explore"), "/explore")
                ui.link(t("nav_interview"), "/interview")
                ui.link(t("nav_updates"), "/updates")
            with ui.column().classes("gap-2 text-sm"):
                ui.label("मदद" if hi else "Help").classes("sf-kicker mb-1")
                ui.link(t("nav_about"), "/about")
                ui.link(t("nav_scam"), "/scam-check")
                ui.link(t("nav_assistant"), "/assistant")
            with ui.column().classes("gap-2 text-sm"):
                ui.label("आधिकारिक संपर्क" if hi else "Official contacts").classes("sf-kicker mb-1")
                ui.link("myScheme — myscheme.gov.in ↗", "https://www.myscheme.gov.in", new_tab=True)
                ui.link(("साइबर धोखाधड़ी: cybercrime.gov.in ↗" if hi else "Cyber fraud: cybercrime.gov.in ↗"),
                        "https://cybercrime.gov.in", new_tab=True)
                with ui.row().classes("items-center gap-2"):
                    ui.icon("call", size="18px").classes("text-[var(--sf-green)]")
                    ui.label(("साइबर हेल्पलाइन 1930" if hi else "Cyber-crime helpline 1930")).classes("font-semibold")
        with ui.element("div").classes("border-t border-[var(--sf-line)]"):
            with ui.row().classes("sf-wrap py-4 justify-between gap-2 text-xs sf-muted"):
                ui.label("© 2026 Scheme Finder · " + ("प्रोटोटाइप — आधिकारिक सरकारी सेवा नहीं" if hi
                                                        else "Prototype — not an official government service"))
                ui.label("हिंदी · English")


# ----------------------------------------------------------------------------- shared visual helpers

CATEGORY_STYLE: dict[str, tuple[str, str, str]] = {  # icon, colour, tint
    "health": ("medical_services", "#0e7490", "#e0f2f5"),
    "pension": ("elderly", "#6d28d9", "#efe9fd"),
    "education": ("school", "#b45309", "#fdf1e1"),
    "agriculture": ("agriculture", "#3f7d20", "#eaf4e2"),
    "housing": ("home", "#1d4ed8", "#e7eefc"),
    "women_child": ("pregnant_woman", "#be185d", "#fce7f1"),
    "disability": ("accessible", "#0f766e", "#e1f5f2"),
    "employment": ("work", "#9a3412", "#fbebe2"),
    "financial_inclusion": ("account_balance", "#155e75", "#e2f3f7"),
    "insurance": ("health_and_safety", "#4338ca", "#e9e8fb"),
    "energy": ("local_fire_department", "#c2410c", "#fdeee4"),
    "social_welfare": ("diversity_3", "#0f4c35", "#e7f1eb"),
}


def icon_tile(icon: str, color: str, tint: str, size: int = 44) -> None:
    ui.html(f'<div class="sf-ico" style="width:{size}px;height:{size}px;background:{tint}">'
            f'<span class="material-icons" style="color:{color};font-size:{int(size * 0.55)}px">{icon}</span></div>',
            sanitize=False)


def category_badge(category: str, size: int = 44) -> None:
    icon, color, tint = CATEGORY_STYLE.get(category, ("category", "#0f4c35", "#e7f1eb"))
    icon_tile(icon, color, tint, size)


def scheme_card(row: dict[str, Any], target: str, *, show_summary: bool = True) -> None:
    """The one scheme card used everywhere: category badge, level, name, summary, benefit."""
    from app.ui.i18n import CATEGORY

    _, color, _tint = CATEGORY_STYLE.get(row["category"], ("", "#0f4c35", ""))
    with ui.link(target=target).classes("no-underline text-inherit h-full"):
        with ui.column().classes("sf-card sf-card-hover p-5 gap-3 h-full"):
            with ui.row().classes("w-full items-center justify-between no-wrap gap-2"):
                category_badge(row["category"], 42)
                chip(t("central") if row["level"] == "central" else (row.get("state") or t("state")),
                     "neutral", "account_balance" if row["level"] == "central" else "map")
            with ui.column().classes("gap-1"):
                ui.label(L(CATEGORY.get(row["category"]))).classes("sf-kicker").style(f"color:{color}")
                ui.label(L(row["name"])).classes("text-[1.05rem] font-bold leading-snug text-[var(--sf-ink)]")
                if show_summary and row.get("summary"):
                    ui.label(L(row["summary"])).classes("sf-muted text-[0.92rem] leading-snug line-clamp-2")
            with ui.row().classes("items-start gap-2 no-wrap mt-auto pt-2 border-t border-[var(--sf-line)] w-full"):
                ui.icon("payments", size="18px").classes("text-[var(--sf-orange)] mt-[2px] shrink-0")
                ui.label(L(row["benefit"])).classes("text-sm font-semibold text-[var(--sf-green)] leading-snug")


def page_header(title: str, subtitle: str | None = None, *, kicker: str | None = None) -> ui.row:
    """Consistent page title block; returns the right-hand actions row."""
    with ui.row().classes("w-full items-end justify-between gap-4 sf-rise"):
        with ui.column().classes("gap-1 min-w-0"):
            if kicker:
                ui.label(kicker).classes("sf-kicker")
            ui.label(title).classes("sf-title")
            if subtitle:
                ui.label(subtitle).classes("sf-lead max-w-3xl")
        actions = ui.row().classes("gap-2 items-center")
    return actions


@contextmanager
def frame(title: str = "", active: str = "", *, full_width: bool = False) -> Iterator[ui.element]:
    """Standard page: head, banner, header, main content area, footer."""
    setup_page(title)
    with ui.column().classes("w-full min-h-screen gap-0"):
        _banner()
        _header(active)
        with ui.element("main").classes("w-full flex-grow") as main:
            if full_width:
                yield main
            else:
                with ui.column().classes("sf-wrap py-8 gap-6") as inner:
                    yield inner
        _footer()
        r = api_client.role()
        if r in ("user", "officer") and active != "/assistant":
            target = "/assistant" + (f"?scheme={active.split('/scheme/')[1]}" if active.startswith("/scheme/") else "")
            ui.button(t("nav_assistant"), icon="forum", on_click=lambda: ui.navigate.to(target))                 .props("unelevated no-caps").classes("sf-btn sf-fab")


def chip(text: str, kind: str = "neutral", icon: str | None = None) -> None:
    with ui.element("span").classes(f"sf-chip sf-chip-{kind}"):
        if icon:
            ui.icon(icon, size="16px")
        ui.label(text)


STATUS_ICON = {"eligible": "check_circle", "maybe": "help", "not_eligible": "cancel"}
OUTCOME_ICON = {"pass": ("check_circle", "sf-rule-pass"), "fail": ("cancel", "sf-rule-fail"),
                "unknown": ("help_outline", "sf-rule-unknown")}


def status_chip(status: str) -> None:
    chip(t(f"status_{status}"), status, STATUS_ICON[status])


def render_rule_tree(node: dict[str, Any], *, depth: int = 0) -> None:
    """Show an evaluated rule tree: each leaf with pass/fail/unknown icon, requirement and evidence."""
    if node["kind"] == "leaf":
        icon, cls = OUTCOME_ICON[node["outcome"]]
        with ui.row().classes("items-start gap-2 no-wrap w-full"):
            ui.icon(icon, size="20px").classes(f"{cls} mt-[2px] shrink-0")
            with ui.column().classes("gap-0"):
                ui.label(L(node["text"])).classes("font-medium")
                ui.label(L(node["evidence"])).classes("text-sm sf-muted")
                if node.get("note"):
                    ui.label(L(node["note"])).classes("text-sm sf-muted italic")
        return
    children = node.get("children", [])
    if node["kind"] == "all" and depth == 0:
        with ui.column().classes("gap-3 w-full"):
            for c in children:
                render_rule_tree(c, depth=depth + 1)
        return
    heading = {"any": t("any_of"), "not": t("none_of"), "all": ""}[node["kind"]]
    with ui.column().classes("gap-2 w-full pl-3 border-l-2 border-[var(--sf-line)]"):
        if heading:
            icon, cls = OUTCOME_ICON[node["outcome"]]
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon, size="18px").classes(cls)
                ui.label(heading).classes("text-sm font-semibold sf-muted")
        for c in children:
            render_rule_tree(c, depth=depth + 1)


def notify_error(res: api_client.ApiResult) -> None:
    from app.ui.i18n import error_text

    ui.notify(error_text(res.code, res.detail), type="negative", position="top")


def guard(*roles: str) -> RedirectResponse | None:
    """Page-level redirect for the wrong role. Usage: ``if (r := guard("user")): return r``.
    This only keeps people on the right pages; the API enforces roles on the server regardless."""
    r = api_client.role()
    if r in roles:
        return None
    if r is None:
        target = {"officer": "/officer/login", "admin": "/admin/login"}.get(roles[0], "/login")
    else:
        target = home_for_role(r)
    return RedirectResponse(target, status_code=303)


def home_for_role(r: str | None) -> str:
    return {"user": "/dashboard", "officer": "/officer/dashboard", "admin": "/admin"}.get(r or "", "/")


def fmt_dt(value: Any) -> str:
    """Timestamp shown in Indian Standard Time, e.g. '07/11/2026 20:37 IST'."""
    from datetime import UTC, datetime, timedelta, timezone

    if not value:
        return "—"
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%d/%m/%Y %H:%M IST")


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    y, m, d = iso[:10].split("-")
    return f"{int(d)}/{int(m)}/{y}"


def link_button(label: str, href: str, *, icon: str | None = None, outline: bool = False,
                new_tab: bool = False) -> ui.button:
    def go() -> None:
        ui.navigate.to(href, new_tab=new_tab)

    b = ui.button(label, icon=icon, on_click=go).props("unelevated no-caps" + (" outline" if outline else ""))
    b.classes("sf-btn" + (" sf-btn-outline" if outline else ""))
    return b


Action = Callable[[], Any]

_STATIC = __import__("pathlib").Path(__file__).parent / "static"


def hero_src() -> str:
    """Use a painted/photo hero (hero.jpg / .png / .webp in app/ui/static) when present,
    otherwise fall back to the bundled SVG illustration."""
    for ext in ("webp", "jpg", "jpeg", "png"):
        if (_STATIC / f"hero.{ext}").is_file():
            return f"/static/hero.{ext}"
    return "/static/hero.svg"
