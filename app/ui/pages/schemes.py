"""/explore (browse + filter) and /scheme/{slug} (detail with rule checklist)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import app, ui

from app.ui import api_client
from app.ui.i18n import BENEFIT_TYPE, CATEGORY, GROUPS, L, lang, t
from app.ui.theme import (
    CATEGORY_STYLE, category_badge, chip, fmt_date, frame, guard, link_button, notify_error, page_header,
    render_rule_tree, scheme_card, status_chip,
)

SKIP = "__all__"


@ui.page("/explore")
async def explore_page(q: str = "", category: str = "", level: str = "", group: str = "",
                       benefit_type: str = "") -> RedirectResponse | None:
    if (redirect := guard("user", "officer", "admin")):
        return redirect
    filters: dict[str, Any] = {"q": q[:100],
                               "category": category if category in CATEGORY else None,
                               "level": level if level in ("central", "state") else None,
                               "benefit_type": benefit_type if benefit_type in BENEFIT_TYPE else None,
                               "group": group if group in GROUPS else None}
    with frame(t("nav_explore"), active="/explore"):
        page_header(t("explore_title"), t("semantic_note"))
        with ui.column().classes("sf-card p-4 md:p-5 gap-4 w-full"):
            search = ui.input(placeholder=t("explore_search"), value=filters["q"])                 .props("outlined clearable debounce=350").classes("sf-search w-full")
            with search.add_slot("prepend"):
                ui.icon("search").classes("text-[var(--sf-green)]")
            # category quick-picks with icons
            with ui.row().classes("gap-2 w-full"):
                @ui.refreshable
                def cat_chips() -> None:
                    for key, (icon, color, tint) in [(None, ("apps", "#0f4c35", "#e7f1eb")),
                                                     *CATEGORY_STYLE.items()]:
                        active = filters["category"] == key
                        label = t("all") if key is None else L(CATEGORY[key])

                        async def pick(k: str | None = key) -> None:
                            filters["category"] = k
                            cat_chips.refresh()
                            await grid.refresh()

                        css = (f"background:{color if active else tint}!important;"
                               f"color:{'#fff' if active else color}!important;"
                               + ("" if active else f"border:1px solid {color}33;"))
                        with ui.button(on_click=pick, color=None).props("unelevated no-caps dense rounded") \
                                .classes("px-3 py-1").style(css):
                            with ui.row().classes("items-center gap-1 no-wrap"):
                                ui.icon(icon, size="18px")
                                ui.label(label).classes("text-sm font-semibold")
                cat_chips()
            with ui.row().classes("gap-3 w-full"):
                def sel(options: dict[str, str], key: str, label: str) -> None:
                    async def _on(e: Any) -> None:
                        filters[key] = None if e.value == SKIP else e.value
                        await grid.refresh()
                    ui.select({SKIP: t("all"), **options}, value=filters[key] or SKIP, label=label, on_change=_on)                         .props("outlined dense").classes("sf-input min-w-[170px] flex-grow sm:flex-grow-0")
                sel({"central": t("central"), "state": t("state")}, "level", t("level"))
                sel({k: L(v) for k, v in BENEFIT_TYPE.items()}, "benefit_type", t("benefit_type"))
                sel({k: L(v) for k, v in GROUPS.items()}, "group", t("group"))

        async def _q(e: Any) -> None:
            filters["q"] = e.value or ""
            await grid.refresh()
        search.on_value_change(_q)

        @ui.refreshable
        async def grid() -> None:
            q_text = (filters.get("q") or "").strip()
            res = await api_client.call("GET", "/api/schemes", params={**filters, "q": None})
            rows = res.data if res.ok else []
            if len(q_text) >= 2:
                found = await api_client.call("GET", "/api/search", params={"q": q_text}, auth=False)
                order = [r["slug"] for r in found.data["results"]] if found.ok else []
                by_slug = {r["slug"]: r for r in rows}
                rows = [by_slug[s] for s in order if s in by_slug]
            ui.label(f"{len(rows)} " + t("schemes").lower()).classes("sf-muted font-semibold")
            if not rows:
                with ui.column().classes("sf-card p-10 items-center text-center gap-3 w-full"):
                    ui.icon("search_off", size="48px").classes("text-[var(--sf-orange)]")
                    ui.label(t("no_results")).classes("sf-h3")
                return
            with ui.element("div").classes("grid sm:grid-cols-2 lg:grid-cols-3 gap-5 w-full"):
                for r in rows:
                    scheme_card(r, f"/scheme/{r['slug']}")

        await grid()
    return None


@ui.page("/scheme/{slug}")
async def scheme_page(slug: str) -> RedirectResponse | None:
    if (redirect := guard("user", "officer", "admin")):
        return redirect
    res = await api_client.call("GET", f"/api/schemes/{slug}")
    with frame(L(res.data["name"]) if res.ok else t("err_scheme_not_found"), active=f"/scheme/{slug}"):
        if not res.ok:
            ui.label(t("err_scheme_not_found")).classes("text-xl")
            ui.link(t("back"), "/explore").classes("sf-link")
            return None
        s = res.data
        other = "hi" if lang() == "en" else "en"
        ui.link(t("back"), "/explore").classes("sf-link")
        with ui.element("div").classes("grid lg:grid-cols-[1fr_340px] gap-6 w-full items-start"):
            with ui.column().classes("gap-6 min-w-0"):
                with ui.row().classes("gap-4 items-start no-wrap sf-rise"):
                    category_badge(s["category"], 64)
                    with ui.column().classes("gap-2 min-w-0"):
                        with ui.row().classes("gap-2 items-center"):
                            chip(L(CATEGORY.get(s["category"])), "neutral")
                            chip(t("central") if s["level"] == "central" else f"{t('state')}: {s['state']}",
                                 "neutral", "account_balance" if s["level"] == "central" else "map")
                            chip(L(BENEFIT_TYPE.get(s["benefit_type"])), "neutral")
                        ui.label(L(s["name"])).classes("sf-title")
                        ui.label(s["name"][other]).classes("sf-muted text-lg")
                with ui.column().classes("sf-card p-6 gap-3 w-full"):
                    ui.label(t("summary")).classes("sf-h3")
                    ui.label(L(s["summary"])).classes("text-lg leading-relaxed")
                    with ui.row().classes("items-center gap-2 p-3 rounded-xl bg-[var(--sf-orange-50)] w-full no-wrap"):
                        ui.icon("payments", size="22px").classes("text-[var(--sf-orange)]")
                        ui.label(L(s["benefit"])).classes("font-semibold")

                # rules checklist
                rules_box = ui.column().classes("sf-card p-6 gap-3 w-full")
                with rules_box:
                    ui.label(t("rules_title")).classes("sf-h3")
                    ui.label(t("rules_unchecked")).classes("sf-muted")
                    for rule in s["rule_list"]:
                        with ui.row().classes("items-start gap-2 no-wrap"):
                            ui.icon("radio_button_unchecked", size="20px").classes("sf-muted mt-[2px]")
                            with ui.column().classes("gap-0"):
                                ui.label(L(rule["text"]))
                                if rule.get("note"):
                                    ui.label(L(rule["note"])).classes("text-sm sf-muted italic")
                    if api_client.role() == "user":
                        link_button(t("nav_check"), "/check", icon="checklist", outline=True)

                with ui.column().classes("sf-card p-6 gap-4 w-full"):
                    ui.label(t("docs_title")).classes("sf-h3")
                    for d in s["documents_detail"]:
                        with ui.row().classes("items-start gap-3 no-wrap"):
                            ui.icon("description", size="22px").classes("text-[var(--sf-orange)] mt-[2px]")
                            with ui.column().classes("gap-0"):
                                ui.label(L(d["name"])).classes("font-semibold")
                                if d.get("where"):
                                    ui.label(L(d["where"])).classes("text-sm sf-muted")

                with ui.column().classes("sf-card p-6 gap-4 w-full"):
                    ui.label(t("steps_title")).classes("sf-h3")
                    for i, step in enumerate(s["steps"], 1):
                        with ui.row().classes("items-start gap-3 no-wrap"):
                            ui.label(str(i)).classes("sf-num text-2xl w-8 shrink-0")
                            ui.label(L(step)).classes("leading-relaxed")
                    link_button(t("official_portal") + " ↗", s["official_url"], new_tab=True)

            # side column
            with ui.column().classes("gap-4 lg:sticky lg:top-24"):
                if api_client.role() == "user":
                    prof = await api_client.call("GET", "/api/me/profile")
                    saved = {"on": prof.ok and slug in prof.data.get("saved_schemes", [])}

                    async def toggle_save() -> None:
                        res = await api_client.call("DELETE" if saved["on"] else "POST", f"/api/me/saved-schemes/{slug}")
                        if not res.ok:
                            notify_error(res)
                            return
                        saved["on"] = slug in res.data["saved_schemes"]
                        save_btn.text = t("unsave_scheme") if saved["on"] else t("save_scheme")
                        save_btn.props(f"icon={'bookmark' if saved['on'] else 'bookmark_border'}")

                    link_button(t("apply"), f"/apply/{slug}", icon="send").classes("w-full")
                    link_button(t("ask_about"), f"/assistant?scheme={slug}", icon="forum", outline=True).classes("w-full")
                    save_btn = ui.button(t("unsave_scheme") if saved["on"] else t("save_scheme"),
                                         icon="bookmark" if saved["on"] else "bookmark_border", on_click=toggle_save) \
                        .props("outline no-caps").classes("sf-btn sf-btn-outline w-full")
                with ui.column().classes("sf-card p-5 gap-3 w-full"):
                    for label, value in ((t("ministry"), L(s["ministry"])),
                                         (t("verified_on"), fmt_date(s["last_verified"])),
                                         (t("version"), f"v{s['version']}"),
                                         (t("deadline"), fmt_date(s["deadline"]) if s.get("deadline") else "—")):
                        with ui.column().classes("gap-0"):
                            ui.label(label).classes("sf-kicker")
                            ui.label(value).classes("font-medium")
                    if s.get("data_status") == "sample":
                        with ui.row().classes("items-start gap-2 p-3 rounded-xl bg-[var(--sf-amber-50)] no-wrap"):
                            ui.icon("warning_amber", size="20px").classes("text-[var(--sf-amber)]")
                            ui.label(t("sample_data")).classes("text-sm")
                    ui.link(f"{t('source')} ↗", s["source_url"], new_tab=True).classes("sf-link")
                if s.get("exclusive_with_detail"):
                    with ui.column().classes("sf-card p-5 gap-2 w-full"):
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("block", size="20px").classes("text-[var(--sf-red)]")
                            ui.label(t("exclusive_title")).classes("font-semibold")
                        for e in s["exclusive_with_detail"]:
                            ui.link(L(e["name"]), f"/scheme/{e['slug']}").classes("sf-link")

        await _rules_with_answers(slug, rules_box)
        if api_client.role() == "user":
            tab_profile = app.storage.tab.get("quick_profile")
            wi = await api_client.call("POST", "/api/whatif", json={"scheme": slug, **(
                {"profile": tab_profile} if tab_profile else {})})
            if wi.ok and not wi.data.get("already_eligible"):
                with rules_box:
                    with ui.column().classes("gap-1 p-3 rounded-xl bg-[var(--sf-orange-50)] w-full"):
                        ui.label(t("whatif_title")).classes("font-semibold")
                        if not wi.data["changes"]:
                            ui.label(t("whatif_none")).classes("text-sm")
                        for c in wi.data["changes"]:
                            icon = {"wait": "schedule", "action": "task_alt", "change": "tune", "unknown": "help_outline",
                                    "fixed": "block"}[c["kind"]]
                            with ui.row().classes("items-center gap-2 no-wrap"):
                                ui.icon(icon, size="18px").classes("text-[var(--sf-orange)]")
                                ui.label(L(c["text"])).classes("text-sm")
                        if wi.data["changes"] and not wi.data["feasible"]:
                            ui.label(t("whatif_fixed")).classes("text-xs sf-muted")
    return None


async def _rules_with_answers(slug: str, rules_box: ui.column) -> None:
    """Re-render the rule checklist against this tab's answers (if any), via the API."""
    if api_client.role() != "user":
        return
    await ui.context.client.connected()
    profile = app.storage.tab.get("quick_profile")
    if not profile:
        return
    chk = await api_client.call("POST", f"/api/schemes/{slug}/check", json=profile)
    if chk.ok:
        rules_box.clear()
        with rules_box:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(t("rules_title")).classes("sf-h3")
                status_chip(chk.data["status"])
            ui.label(t("rules_checked")).classes("sf-muted text-sm")
            render_rule_tree(chk.data["tree"])
            link_button(t("nav_check"), "/check", icon="edit", outline=True)
