"""/check — the fixed-form eligibility check (spec 3.3 "fixed form fallback") and ranked results (3.5)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import app, ui

from app.ui import api_client
from app.ui.i18n import BENEFIT_TYPE, CATEGORY, L, lang, t
from app.ui.theme import chip, fmt_date, frame, guard, notify_error, render_rule_tree, status_chip

BASIC = ["state", "residence", "age", "gender", "category", "annual_income", "occupation", "family_size"]
MORE_CHOICE = ["marital_status", "education_level"]
MORE_NUMBER = ["marks_percent", "disability_percent", "land_holding_hectares", "monthly_income"]
MORE_BOOL = ["is_bpl", "has_bank_account", "is_income_tax_payer", "is_govt_employee", "has_pucca_house",
             "has_lpg_connection", "is_pregnant_or_lactating", "has_girl_child_under_10", "has_epf_esic_nps",
             "is_minority"]
SKIP = "__skip__"


async def load_vocab() -> dict[str, Any]:
    res = await api_client.call("GET", "/api/schemes/vocabulary", auth=False)
    return res.data


def _label(vocab: dict[str, Any], field: str) -> str:
    spec = vocab["fields"][field]
    unit = {"inr": " (₹)", "percent": " (%)", "hectares": " (ha)", "years": ""}.get(spec["unit"], "")
    return L(spec["label"]) + unit


def _options(vocab: dict[str, Any], field: str) -> dict[str, str]:
    opts = {SKIP: t("dont_know")}
    if field == "state":
        opts.update({s: s for s in vocab["states"]})
    else:
        opts.update({k: L(v) for k, v in vocab["enums"][field].items()})
    return opts


def profile_from_inputs(inputs: dict[str, Any], vocab: dict[str, Any]) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    for field, widget in inputs.items():
        v = widget.value
        kind = vocab["fields"][field]["kind"]
        if v in (None, "", SKIP):
            continue
        if kind == "bool":
            profile[field] = v == "yes"
        elif kind == "int":
            profile[field] = int(v)
        elif kind == "number":
            profile[field] = float(v)
        else:
            profile[field] = v
    return profile


def profile_inputs(vocab: dict[str, Any], saved: dict[str, Any], sources: dict[str, str] | None = None,
                   *, expanded: bool = False) -> dict[str, Any]:
    """Build the eligibility form in the current UI context; returns {field: widget}."""
    inputs: dict[str, Any] = {}
    sources = sources or {}

    def mark(f: str, w: Any) -> Any:
        if str(sources.get(f, "")).startswith("document:"):
            w.props('hint="📄 ' + t("from_document") + '"')
        return w

    def one(f: str) -> None:
        spec = vocab["fields"][f]
        kind = spec["kind"]
        if kind in ("enum", "state"):
            inputs[f] = ui.select(_options(vocab, f), label=_label(vocab, f), value=saved.get(f, SKIP),
                                  with_input=(f == "state")).props("outlined dense options-dense")
        elif kind == "bool":
            cur = saved.get(f)
            inputs[f] = ui.select({SKIP: t("dont_know"), "yes": t("yes"), "no": t("no")}, label=_label(vocab, f),
                                  value=SKIP if cur is None else ("yes" if cur else "no")).props("outlined dense options-dense")
        elif kind == "int":
            inputs[f] = ui.number(_label(vocab, f), value=saved.get(f), min=spec["min"], max=spec["max"], step=1,
                                  format="%d").props("outlined dense clearable")
        else:
            inputs[f] = ui.number(_label(vocab, f), value=saved.get(f), min=spec["min"], max=spec["max"])                 .props("outlined dense clearable")
        mark(f, inputs[f].classes("sf-input w-full"))

    for f in BASIC:
        one(f)
    with ui.expansion(t("more_details"), icon="tune", value=expanded)             .classes("w-full rounded-xl border border-[var(--sf-line)]"):
        with ui.column().classes("gap-3 w-full pt-2"):
            for f in MORE_CHOICE + MORE_NUMBER + MORE_BOOL:
                one(f)
    return inputs


@ui.page("/check")
async def check_page() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    vocab = await load_vocab()
    with frame(t("nav_check"), active="/check"):
        await ui.context.client.connected()
        saved: dict[str, Any] = app.storage.tab.get("quick_profile", {})
        prof = await api_client.call("GET", "/api/me/profile")
        can_save = prof.ok and prof.data["consent"].get("mode") == "save_profile" and not prof.data["consent"].get("withdrawn_at")
        if not saved and prof.ok and prof.data["eligibility"]:
            saved = prof.data["eligibility"]
        inputs: dict[str, Any] = {}

        with ui.column().classes("gap-1"):
            ui.label(t("check_title")).classes("sf-title")
            ui.label(t("check_sub")).classes("sf-muted text-lg max-w-3xl")

        with ui.element("div").classes("grid lg:grid-cols-[380px_1fr] gap-6 w-full items-start"):
            # ------------------------------------------------------------ form
            with ui.column().classes("sf-card p-6 gap-3 lg:sticky lg:top-24"):
                ui.label(t("basics")).classes("sf-h3")
                inputs.update(profile_inputs(vocab, saved))
                ui.link("📄 " + t("fill_from_doc") + " →", "/documents").classes("sf-link text-sm")

                async def run() -> None:
                    profile = profile_from_inputs(inputs, vocab)
                    btn.props("loading")
                    res = await api_client.call("POST", "/api/match", json={"profile": profile})
                    btn.props(remove="loading")
                    if not res.ok:
                        notify_error(res)
                        return
                    app.storage.tab["quick_profile"] = profile
                    state["data"] = res.data
                    results.refresh()

                def clear() -> None:
                    for w in inputs.values():
                        w.value = SKIP if isinstance(w, ui.select) else None
                    app.storage.tab.pop("quick_profile", None)
                    state["data"] = None
                    results.refresh()

                btn = ui.button(t("run_check"), icon="search", on_click=run).props("unelevated no-caps") \
                    .classes("sf-btn w-full")
                ui.button(t("clear"), on_click=clear).props("flat no-caps").classes("w-full")

                async def save_answers() -> None:
                    cur = await api_client.call("GET", "/api/me/profile")
                    personal = cur.data["personal"] if cur.ok else {}
                    res = await api_client.call("PUT", "/api/me/profile", json={
                        "eligibility": profile_from_inputs(inputs, vocab), "personal": personal})
                    if res.ok:
                        ui.notify(t("saved_ok"), type="positive", position="top")
                    else:
                        notify_error(res)

                if can_save:
                    ui.button(t("save_answers"), icon="save", on_click=save_answers)                         .props("outline no-caps").classes("sf-btn sf-btn-outline w-full")

            # ------------------------------------------------------------ results
            state: dict[str, Any] = {"data": None, "category": None, "level": None, "benefit_type": None,
                                     "hide": False}

            @ui.refreshable
            def results() -> None:
                data = state["data"]
                if not data:
                    with ui.column().classes("sf-card p-10 items-center text-center gap-3 sf-dots w-full"):
                        ui.icon("checklist", size="56px").classes("text-[var(--sf-orange)]")
                        ui.label(t("results_title")).classes("sf-h3 text-[1.45rem]")
                        ui.label(t("check_sub")).classes("sf-muted max-w-lg")
                    return
                c = data["counts"]
                with ui.column().classes("gap-4 w-full"):
                    with ui.row().classes("items-center justify-between w-full"):
                        ui.label(t("results_title")).classes("sf-title")
                        with ui.row().classes("gap-2"):
                            for k in ("eligible", "maybe", "not_eligible"):
                                chip(f"{t('status_' + k)}: {c[k]}", k)
                    with ui.row().classes("items-start gap-2 p-3 rounded-xl bg-[var(--sf-green-50)] w-full no-wrap"):
                        ui.icon("gavel", size="20px").classes("text-[var(--sf-green)] mt-[2px]")
                        ui.label(t("results_engine")).classes("text-sm")
                    with ui.row().classes("gap-3 items-center w-full"):
                        def setter(key: str):
                            def _set(e: Any) -> None:
                                state[key] = None if e.value == SKIP else e.value
                                results.refresh()
                            return _set
                        all_ = {SKIP: t("all")}
                        ui.select({**all_, **{k: L(v) for k, v in CATEGORY.items()}}, label=t("category"),
                                  value=state["category"] or SKIP, on_change=setter("category")) \
                            .props("outlined dense").classes("sf-input w-44")
                        ui.select({**all_, "central": t("central"), "state": t("state")}, label=t("level"),
                                  value=state["level"] or SKIP, on_change=setter("level")) \
                            .props("outlined dense").classes("sf-input w-40")
                        ui.select({**all_, **{k: L(v) for k, v in BENEFIT_TYPE.items()}}, label=t("benefit_type"),
                                  value=state["benefit_type"] or SKIP, on_change=setter("benefit_type")) \
                            .props("outlined dense").classes("sf-input w-44")

                        def _hide(e: Any) -> None:
                            state["hide"] = e.value
                            results.refresh()
                        ui.switch(t("hide_not_eligible"), value=state["hide"], on_change=_hide)
                    items = [i for i in data["items"]
                             if (not state["category"] or i["scheme"]["category"] == state["category"])
                             and (not state["level"] or i["scheme"]["level"] == state["level"])
                             and (not state["benefit_type"] or i["scheme"]["benefit_type"] == state["benefit_type"])
                             and not (state["hide"] and i["result"]["status"] == "not_eligible")]
                    if not items:
                        ui.label(t("no_results")).classes("sf-muted")
                    for item in items:
                        _result_card(item)

            with ui.column().classes("w-full"):
                results()
            if saved:
                await run()
    return None


def _result_card(item: dict[str, Any]) -> None:
    s, r = item["scheme"], item["result"]
    with ui.column().classes("sf-card p-5 gap-3 w-full"):
        with ui.row().classes("items-start justify-between w-full gap-3"):
            with ui.column().classes("gap-1"):
                with ui.row().classes("gap-2 items-center"):
                    ui.label(L(CATEGORY.get(s["category"]))).classes("sf-kicker")
                    ui.label("·").classes("sf-muted")
                    ui.label(t("central") if s["level"] == "central" else f"{t('state')}: {s['state']}").classes("sf-kicker")
                ui.label(L(s["name"])).classes("text-xl font-bold leading-snug")
                ui.label(L(s["benefit"])).classes("sf-muted")
            status_chip(r["status"])
        with ui.expansion(f"{len(r['passed'])} ✓ · {len(r['failed'])} ✗ · {len(r['unknown'])} ?",
                          icon="rule", value=r["status"] != "not_eligible" and len(r["failed"]) + len(r["unknown"]) <= 3) \
                .classes("w-full rounded-xl bg-[var(--sf-cream)]"):
            render_rule_tree(r["tree"])
        with ui.row().classes("gap-4 items-center"):
            ui.link(t("view_scheme") + " →", f"/scheme/{s['slug']}").classes("sf-link")
            ui.link(t("official_portal") + " ↗", s["official_url"], new_tab=True).classes("sf-link")
            ui.label(f"{t('verified_on')}: {fmt_date(s['last_verified'])}").classes("text-xs sf-muted")
