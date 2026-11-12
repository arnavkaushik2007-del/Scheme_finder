"""/recommendations and /assistant (RAG chat with citations, confidence, abstention, thumbs up/down)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import ui

from app.ui import api_client
from app.ui.i18n import BENEFIT_TYPE, CATEGORY, L, lang, t
from app.ui.theme import chip, frame, guard, link_button, notify_error, render_rule_tree, status_chip

SKIP = "__all__"


def _inr(n: int) -> str:
    from app.services.eligibility import format_value

    return format_value("annual_income", n, lang())


@ui.page("/recommendations")
async def recommendations_page() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    res = await api_client.call("GET", "/api/recommendations")
    with frame(t("nav_reco"), active="/recommendations"):
        if not res.ok:
            notify_error(res)
            return None
        data = res.data
        from app.ui.theme import page_header

        actions = page_header(t("reco_title"), t("reco_sub"))
        with actions:
            link_button(t("edit_answers"), "/me", icon="edit", outline=True)
            link_button(t("nav_planner"), "/planner", icon="savings", outline=True)
        if not data["profile_used"]:
            with ui.row().classes("items-center gap-3 p-4 rounded-2xl bg-[var(--sf-amber-50)] w-full no-wrap"):
                ui.icon("info", size="22px").classes("text-[var(--sf-amber)]")
                ui.label(t("reco_empty"))
                ui.link(t("fill_from_doc") + " →", "/documents").classes("sf-link ml-auto")

        c = data["counts"]
        with ui.element("div").classes("sf-band p-6 md:p-7 grid md:grid-cols-[1.2fr_1fr] gap-6 items-center w-full"):
            with ui.row().classes("items-center gap-4 no-wrap"):
                ui.html('<div class="sf-ico" style="width:60px;height:60px;background:rgba(255,255,255,.12)">'
                        '<span class="material-icons" style="color:#fbd38d;font-size:32px">savings</span></div>',
                        sanitize=False)
                with ui.column().classes("gap-0"):
                    ui.label(t("benefit_estimate")).classes("opacity-85 text-sm")
                    ui.label(_inr(data["benefit_estimate"]["total_annual_inr"])) \
                        .classes("sf-serif text-4xl font-extrabold leading-tight")
                    ui.label(t("benefit_estimate_note")).classes("text-xs opacity-75")
            with ui.element("div").classes("grid grid-cols-3 gap-3"):
                for k, color in (("eligible", "#86efac"), ("maybe", "#fcd34d"), ("not_eligible", "#fca5a5")):
                    with ui.column().classes("items-center gap-0 rounded-2xl py-3 px-2 text-center").style(
                            "background:rgba(255,255,255,.08)"):
                        ui.label(str(c[k])).classes("sf-serif text-3xl font-extrabold").style(f"color:{color}")
                        ui.label(t("status_" + k)).classes("text-xs opacity-90 leading-tight")

        state: dict[str, Any] = {"category": None, "level": None, "benefit_type": None, "status": "open"}

        @ui.refreshable
        def cards() -> None:
            st = state["status"]
            items = [i for i in data["items"]
                     if (not state["category"] or i["scheme"]["category"] == state["category"])
                     and (not state["level"] or i["scheme"]["level"] == state["level"])
                     and (not state["benefit_type"] or i["scheme"]["benefit_type"] == state["benefit_type"])
                     and (st == "all" or (st == "open" and i["result"]["status"] != "not_eligible")
                          or i["result"]["status"] == st)]
            if not items:
                with ui.column().classes("sf-card p-10 items-center text-center gap-3 w-full"):
                    ui.icon("filter_alt_off", size="44px").classes("text-[var(--sf-orange)]")
                    ui.label(t("no_results")).classes("sf-h3")
            for item in items:
                _reco_card(item)

        def _status(e: Any) -> None:
            state["status"] = e.value
            cards.refresh()

        ui.toggle({"open": f"{t('status_eligible')} + {t('status_maybe')} · {c['eligible'] + c['maybe']}",
                   "eligible": f"{t('status_eligible')} · {c['eligible']}",
                   "maybe": f"{t('status_maybe')} · {c['maybe']}",
                   "not_eligible": f"{t('status_not_eligible')} · {c['not_eligible']}",
                   "all": f"{t('all')} · {sum(c.values())}"}, value="open", on_change=_status) \
            .props("no-caps unelevated rounded toggle-color=primary").classes("border border-[var(--sf-line)] rounded-xl")
        with ui.row().classes("gap-3 items-center w-full"):
            def setter(key: str):
                def _set(e: Any) -> None:
                    state[key] = None if e.value == SKIP else e.value
                    cards.refresh()
                return _set
            ui.select({SKIP: t("all"), **{k: L(v) for k, v in CATEGORY.items()}}, value=SKIP, label=t("category"),
                      on_change=setter("category")).props("outlined dense").classes("sf-input w-44")
            ui.select({SKIP: t("all"), "central": t("central"), "state": t("state")}, value=SKIP, label=t("level"),
                      on_change=setter("level")).props("outlined dense").classes("sf-input w-40")
            ui.select({SKIP: t("all"), **{k: L(v) for k, v in BENEFIT_TYPE.items()}}, value=SKIP,
                      label=t("benefit_type"), on_change=setter("benefit_type")).props("outlined dense") \
                .classes("sf-input w-44")
        with ui.column().classes("gap-4 w-full"):
            cards()
        close = await api_client.call("POST", "/api/whatif", json={})
        if close.ok and close.data["closest"]:
            with ui.column().classes("sf-card p-5 gap-3 w-full"):
                ui.label(t("closest")).classes("sf-h3 text-[1.45rem]")
                for c in close.data["closest"]:
                    with ui.column().classes("gap-1 border-b border-[var(--sf-line)] pb-2 w-full"):
                        ui.link(L(c["name"]), f"/scheme/{c['scheme']}").classes("sf-link font-semibold")
                        for ch in c["changes"]:
                            ui.label("→ " + L(ch["text"])).classes("text-sm")
    return None


def _reco_card(item: dict[str, Any]) -> None:
    s, r = item["scheme"], item["result"]
    with ui.column().classes("sf-card p-5 gap-3 w-full"):
        with ui.row().classes("items-start justify-between w-full gap-3 no-wrap"):
            from app.ui.theme import category_badge

            category_badge(s["category"], 48)
            with ui.column().classes("gap-1 flex-grow min-w-0"):
                ui.label(f"{L(CATEGORY.get(s['category']))} · "
                         + (t("central") if s["level"] == "central" else s["state"])).classes("sf-kicker")
                ui.label(L(s["name"])).classes("text-xl font-bold leading-snug")
                ui.label(L(s["benefit"])).classes("sf-muted")
            with ui.column().classes("items-end gap-1 shrink-0"):
                status_chip(r["status"])
                ui.label(f"{t('relevance')}: {round(item['relevance'] * 100)}%").classes("text-xs sf-muted")
        if item["whats_missing"]:
            with ui.column().classes("gap-1 p-3 rounded-xl bg-[var(--sf-orange-50)] w-full"):
                ui.label(t("whats_missing")).classes("font-semibold text-sm")
                any_shown = False
                for m in item["whats_missing"]:
                    icon = {"unknown": "help_outline", "wait": "schedule", "hard": "block"}[m["kind"]]
                    with ui.row().classes("items-start gap-2 no-wrap"):
                        ui.icon(icon, size="18px").classes("mt-[2px] text-[var(--sf-orange)]")
                        prefix = f"({t('one_of')}) " if m["option_of_group"] and not any_shown else ""
                        ui.label(prefix + L(m)).classes("text-sm")
                    any_shown = any_shown or m["option_of_group"]
        with ui.expansion(f"{len(r['passed'])} ✓ · {len(r['failed'])} ✗ · {len(r['unknown'])} ?", icon="rule") \
                .classes("w-full rounded-xl bg-[var(--sf-cream)]"):
            render_rule_tree(r["tree"])
        with ui.row().classes("gap-4 items-center"):
            if r["status"] != "not_eligible":
                link_button(t("apply"), f"/apply/{s['slug']}", icon="send")
            ui.link(t("view_scheme") + " →", f"/scheme/{s['slug']}").classes("sf-link")
            ui.link(t("ask_about") + " →", f"/assistant?scheme={s['slug']}").classes("sf-link")
            ui.link(t("official_portal") + " ↗", s["official_url"], new_tab=True).classes("sf-link")


SUGGESTIONS = {
    "en": ["What documents do I need for old age pension?", "Am I eligible for Atal Pension Yojana?",
           "How do I apply for PM-KISAN?", "Which schemes help with hospital costs?"],
    "hi": ["वृद्धावस्था पेंशन के लिए कौन से दस्तावेज़ चाहिए?", "क्या मैं अटल पेंशन योजना के लिए पात्र हूँ?",
           "पीएम किसान के लिए आवेदन कैसे करें?", "अस्पताल के खर्च में कौन सी योजना मदद करती है?"],
}


@ui.page("/assistant")
async def assistant_page(scheme: str | None = None) -> RedirectResponse | None:
    if (redirect := guard("user", "officer", "admin")):
        return redirect
    all_schemes = await api_client.call("GET", "/api/schemes", auth=False)
    status = await api_client.call("GET", "/api/assistant/status")
    options = {SKIP: t("asst_all"), **{s["slug"]: L(s["name"]) for s in (all_schemes.data or [])}}
    with frame(t("nav_assistant"), active="/assistant"):
        with ui.column().classes("gap-1"):
            ui.label(t("asst_title")).classes("sf-title")
            ui.label(t("asst_sub")).classes("sf-muted max-w-3xl")
        if status.ok and not status.data["index"].get("ready"):
            ui.label(t("index_building")).classes("text-sm text-[var(--sf-amber)]")

        with ui.element("div").classes("grid lg:grid-cols-[1fr_360px] gap-6 w-full items-start"):
            with ui.column().classes("sf-card p-0 w-full overflow-hidden"):
                with ui.row().classes("w-full items-center gap-3 px-5 py-3 border-b border-[var(--sf-line)]"):
                    scope = ui.select(options, value=scheme if scheme in options else SKIP, label=t("asst_scope"),
                                      with_input=True).props("outlined dense").classes("sf-input min-w-[260px]")
                    if status.ok:
                        ui.label(t("mode_extractive") if status.data["mode"] == "extractive" else t("mode_generative")) \
                            .classes("text-xs sf-muted ml-auto")
                log = ui.column().classes("w-full gap-4 p-5 min-h-[360px] max-h-[62vh] overflow-y-auto")
                with log:
                    with ui.column().classes("gap-2 items-start") as intro:
                        ui.label(t("suggested")).classes("sf-kicker")
                        for q in SUGGESTIONS[lang()]:
                            ui.button(q, on_click=lambda q=q: send(q)).props("outline no-caps rounded dense") \
                                .classes("text-[var(--sf-green)] bg-[#fffdf8] text-left")
                with ui.row().classes("w-full gap-2 p-4 border-t border-[var(--sf-line)] no-wrap"):
                    box = ui.input(placeholder=t("asst_placeholder")).props("outlined dense autogrow") \
                        .classes("sf-input flex-grow")
                    ask_btn = ui.button(t("send"), icon="send", on_click=lambda: send(box.value or "")) \
                        .props("unelevated no-caps").classes("sf-btn")
                    box.on("keydown.enter", lambda: send(box.value or ""))
                    from app.ui.voice import mic_button

                    await mic_button(lambda text: send(text))
            side = ui.column().classes("sf-card p-5 gap-3 w-full lg:sticky lg:top-24")
            with side:
                ui.label(t("sources")).classes("sf-h3")
                ui.label("—").classes("sf-muted")

        async def send(question: str) -> None:
            question = question.strip()
            if len(question) < 2:
                return
            intro.set_visibility(False)
            box.value = ""
            with log:
                with ui.row().classes("w-full justify-end"):
                    ui.label(question).classes("bg-[var(--sf-green)] text-white px-4 py-2 rounded-2xl rounded-br-sm max-w-[80%]")
                pending = ui.spinner("dots", size="lg", color="primary")
            ask_btn.props("loading")
            res = await api_client.call("POST", "/api/ask", json={
                "question": question, "scheme": None if scope.value == SKIP else scope.value})
            ask_btn.props(remove="loading")
            pending.delete()
            if not res.ok:
                notify_error(res)
                return
            with log:
                _answer_bubble(res.data, side)
            ui.run_javascript(f"const el=document.getElementById('c{log.id}'); if (el) el.scrollTop = el.scrollHeight;")
    return None


def _render_answer_text(text: str) -> str:
    import html
    import re

    safe = html.escape(text)
    return re.sub(r"\[S(\d+)\]", r'<sup class="sf-cite">[\1]</sup>', safe)


def _answer_bubble(d: dict[str, Any], side: ui.column) -> None:
    with ui.column().classes("w-full items-start gap-2"):
        bubble_cls = "bg-[var(--sf-amber-50)]" if d["abstained"] else "bg-[#fffdf8] border border-[var(--sf-line)]"
        with ui.column().classes(f"{bubble_cls} px-4 py-3 rounded-2xl rounded-bl-sm max-w-[92%] gap-2"):
            if d["abstained"]:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("help_outline", size="20px").classes("text-[var(--sf-amber)]")
                    ui.label(t("not_sure")).classes("font-semibold text-sm")
                ui.label(d["answer"])
                ui.link(t("official_portal") + " ↗", d["official_url"], new_tab=True).classes("sf-link text-sm")
            else:
                ui.html(_render_answer_text(d["answer"]), sanitize=False).classes("leading-relaxed")
        with ui.row().classes("items-center gap-3 text-xs sf-muted"):
            if not d["abstained"]:
                conf = d["confidence"]
                chip(f"{t('confidence_label')}: {round(conf * 100)}%", "eligible" if conf >= 0.8 else "maybe")
            ui.label(t("mode_extractive") if d["mode"] == "extractive" else t("mode_generative"))
            ui.label(f"{d['latency_ms']} ms")
            import re as _re

            from app.ui.voice import speak_button

            speak_button(_re.sub(r"\[S\d+\]", "", d["answer"]), d["language"])
            ui.label(t("helpful"))

            async def rate(r: str) -> None:
                res = await api_client.call("POST", f"/api/ask/{d['id']}/feedback", json={"rating": r})
                if res.ok:
                    ui.notify(t("thanks_feedback"), type="positive", position="top")

            ui.button(icon="thumb_up", on_click=lambda: rate("up")).props("flat round dense size=sm")
            ui.button(icon="thumb_down", on_click=lambda: rate("down")).props("flat round dense size=sm")
            if d["sources"]:
                ui.button(t("sources"), icon="menu_book", on_click=lambda: _show_sources(d, side)) \
                    .props("flat dense no-caps size=sm")
    _show_sources(d, side)


def _show_sources(d: dict[str, Any], side: ui.column) -> None:
    side.clear()
    with side:
        ui.label(t("sources")).classes("sf-h3")
        if not d["sources"]:
            ui.label("—").classes("sf-muted")
        for s in d["sources"]:
            title = t("rules_engine_src") if s["section"] == "rules_engine" else L(s["title"])
            with ui.column().classes("gap-1 p-3 rounded-xl w-full " + (
                    "bg-[var(--sf-green-50)]" if s["cited"] else "bg-[var(--sf-cream)] opacity-70")):
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.label(f"[{s['n']}]").classes("font-bold text-[var(--sf-green)]")
                    ui.label(title).classes("font-semibold text-sm")
                ui.label(s["text"][:420] + ("…" if len(s["text"]) > 420 else "")).classes("text-xs leading-relaxed")
                with ui.row().classes("gap-3 text-xs"):
                    if s.get("slug"):
                        ui.link(t("view_scheme") + " →", f"/scheme/{s['slug']}").classes("sf-link")
                    if str(s.get("url", "")).startswith("http"):
                        ui.link(t("official_portal") + " ↗", s["url"], new_tab=True).classes("sf-link")
