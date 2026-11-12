"""Step 8 pages: /interview, /planner, /scam-check, /updates, /admin/ai."""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import app, ui

from app.ui import api_client
from app.ui.i18n import L, lang, t
from app.ui.theme import chip, fmt_dt, frame, guard, link_button, notify_error, status_chip
from app.ui.voice import mic_button

# ============================================================================ adaptive interview


@ui.page("/interview")
async def interview_page() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    with frame(t("nav_interview"), active="/interview"):
        await ui.context.client.connected()
        st: dict[str, Any] = app.storage.tab.setdefault("interview", {"answers": {}, "skipped": []})
        with ui.column().classes("gap-1"):
            ui.label(t("int_title")).classes("sf-title")
            ui.label(t("int_sub")).classes("sf-muted text-lg max-w-3xl")
        with ui.element("div").classes("grid lg:grid-cols-[1fr_320px] gap-6 w-full items-start"):
            card = ui.column().classes("sf-card p-6 gap-4 w-full min-h-[320px]")
            side = ui.column().classes("sf-card p-5 gap-3 w-full lg:sticky lg:top-24")

        async def submit(field: str, value: Any) -> None:
            if value is None:
                st["skipped"] = sorted(set(st["skipped"]) | {field})
            else:
                st["answers"][field] = value
            app.storage.tab["interview"] = st
            await step()

        async def step() -> None:
            r = await api_client.call("POST", "/api/interview/next", json={"answers": st["answers"], "skipped": st["skipped"]})
            if not r.ok:
                notify_error(r)
                return
            d = r.data
            side.clear()
            with side:
                ui.label(str(d["possible"])).classes("sf-serif text-5xl font-extrabold text-[var(--sf-green)]")
                ui.label(f"{t('still_possible')} / {d['total']}").classes("sf-muted")
                ui.linear_progress(1 - d["possible"] / max(1, d["total"]), show_value=False) \
                    .props("rounded size=10px color=secondary")
                chip(f"{d['eligible']} {t('likely_so_far')}", "eligible", "check_circle")
                ui.link("📄 " + t("fill_from_doc") + " →", "/documents").classes("sf-link text-sm")

                def restart() -> None:
                    app.storage.tab["interview"] = {"answers": {}, "skipped": []}
                    ui.navigate.reload()

                ui.button(t("start_over"), icon="restart_alt", on_click=restart).props("flat no-caps")
            card.clear()
            with card:
                if d["done"]:
                    ui.label(t("int_done")).classes("sf-serif text-3xl font-bold text-[var(--sf-green)]")
                    ui.label(f"{d['asked']} {t('question_n').lower()} · {d['eligible']} {t('likely_so_far')}").classes("sf-muted")

                    async def results() -> None:
                        prof = await api_client.call("GET", "/api/me/profile")
                        if prof.ok and prof.data["consent"].get("mode") == "save_profile":
                            await api_client.call("PUT", "/api/me/profile", json={
                                "eligibility": {**prof.data["eligibility"], **d["answers"]},
                                "personal": prof.data["personal"]})
                            ui.navigate.to("/recommendations")
                        else:  # "use once": show results without saving
                            app.storage.tab["quick_profile"] = d["answers"]
                            ui.navigate.to("/check")

                    ui.button(t("see_results") + "  →", on_click=results).props("unelevated no-caps").classes("sf-btn self-start")
                    return
                q = d["question"]
                ui.label(f"{t('question_n')} {d['asked'] + 1}").classes("sf-kicker")
                ui.label(L(q["text"])).classes("sf-serif text-2xl md:text-3xl font-bold text-[var(--sf-ink)]")
                if q["kind"] in ("enum", "bool") or q["field"] == "state":
                    if q["field"] == "state":
                        sel = ui.select({k: L(v) for k, v in q["options"].items()}, with_input=True,
                                        label=L(q["text"])).props("outlined").classes("sf-input w-full md:w-96")

                        async def pick_state() -> None:
                            if sel.value:
                                await submit("state", sel.value)

                        ui.button(t("next_q"), on_click=pick_state).props("unelevated no-caps").classes("sf-btn self-start")
                    else:
                        with ui.row().classes("gap-2"):
                            for k, label in q["options"].items():
                                val: Any = (k == "yes") if q["kind"] == "bool" else k
                                ui.button(L(label), on_click=lambda v=val: submit(q["field"], v)) \
                                    .props("outline no-caps").classes("sf-btn sf-btn-outline")
                else:
                    num = ui.number(L(q["text"]), min=q["min"], max=q["max"]).props("outlined autofocus") \
                        .classes("sf-input w-full md:w-72")

                    async def send_num() -> None:
                        if num.value is None:
                            return
                        v = int(num.value) if q["kind"] == "int" else float(num.value)
                        await submit(q["field"], v)

                    num.on("keydown.enter", send_num)
                    ui.button(t("next_q"), on_click=send_num).props("unelevated no-caps").classes("sf-btn self-start")

                    async def heard(text: str) -> None:
                        import re

                        m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
                        if m:
                            num.value = float(m.group(0).replace(",", ""))
                    await mic_button(heard)
                ui.button(t("i_dont_know"), icon="help_outline", on_click=lambda: submit(q["field"], None)) \
                    .props("flat no-caps")

        await step()
    return None

# ============================================================================ planner


@ui.page("/planner")
async def planner_page() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    with frame(t("nav_planner"), active="/planner"):
        with ui.row().classes("w-full items-end justify-between gap-3"):
            with ui.column().classes("gap-1"):
                ui.label(t("plan_title")).classes("sf-title")
                ui.label(t("plan_sub")).classes("sf-muted")
            opts: dict[str, Any] = {"maybe": False}

            async def download() -> None:
                r = await api_client.call("GET", "/api/plan/pdf", params={"include_maybe": str(opts["maybe"]).lower()})
                if r.ok:
                    ui.download(r.data["_bytes"], "scheme-plan.pdf", "application/pdf")
                else:
                    notify_error(r)

            ui.button(t("download_pdf"), icon="picture_as_pdf", on_click=download).props("outline no-caps") \
                .classes("sf-btn sf-btn-outline")

        @ui.refreshable
        async def body() -> None:
            r = await api_client.call("POST", "/api/plan", json={"include_maybe": opts["maybe"]})
            if not r.ok:
                notify_error(r)
                return
            p = r.data
            from app.services.eligibility import format_value

            with ui.row().classes("sf-card p-5 items-center gap-5 w-full"):
                ui.icon("savings", size="36px").classes("text-[var(--sf-orange)]")
                with ui.column().classes("gap-0"):
                    ui.label(format_value("annual_income", p["total_annual_inr"], lang())) \
                        .classes("sf-title")
                    ui.label(L(p["note"])).classes("text-xs sf-muted")
            if not p["timeline"]:
                ui.label(t("plan_empty")).classes("sf-muted")
            for c in p["conflicts"]:
                with ui.row().classes("items-start gap-2 p-3 rounded-xl bg-[var(--sf-amber-50)] w-full no-wrap"):
                    ui.icon("block", size="20px").classes("text-[var(--sf-amber)]")
                    ui.label(L(c)).classes("text-sm")
            for x in p["timeline"]:
                with ui.row().classes("sf-card p-5 gap-4 w-full items-start no-wrap"):
                    ui.label(str(x["order"])).classes("sf-num w-10 shrink-0")
                    with ui.column().classes("gap-1 flex-grow"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(L(x["name"])).classes("text-lg font-bold")
                            status_chip(x["status"])
                            if x["deadline"]:
                                chip(f"{t('deadline')}: {x['deadline']}", "maybe", "event")
                        ui.label(L(x["benefit"])).classes("sf-muted")
                        ready = len(x["documents"]) - x["missing"]
                        ui.label(f"{ready}/{len(x['documents'])} {t('docs_ready')}").classes("text-sm font-semibold")
                        with ui.row().classes("gap-1"):
                            for d in x["documents"]:
                                chip(L(d["name"]), "eligible" if d["have"] else "neutral", "check" if d["have"] else None)
                        with ui.row().classes("gap-3 mt-1"):
                            link_button(t("apply"), f"/apply/{x['slug']}", icon="send")
                            ui.link(t("official_portal") + " ↗", x["official_url"], new_tab=True).classes("sf-link self-center")

        async def toggle(e: Any) -> None:
            opts["maybe"] = e.value
            await body.refresh()

        ui.switch(t("include_maybe"), on_change=toggle)
        await body()
    return None

# ============================================================================ scam checker (public)


VERDICT_KIND = {"likely_scam": "not_eligible", "suspicious": "maybe", "consistent": "eligible"}


@ui.page("/scam-check")
async def scam_page() -> None:
    with frame(t("nav_scam"), active="/scam-check"):
        with ui.column().classes("gap-1"):
            ui.label(t("scam_title")).classes("sf-title")
            ui.label(t("scam_sub")).classes("sf-muted text-lg max-w-3xl")
        with ui.element("div").classes("grid lg:grid-cols-2 gap-6 w-full items-start"):
            with ui.column().classes("sf-card p-5 gap-3 w-full"):
                box = ui.textarea(placeholder=t("scam_placeholder")).props("outlined autogrow").classes("sf-input w-full min-h-[160px]")

                async def check() -> None:
                    if len((box.value or "").strip()) < 5:
                        return
                    btn.props("loading")
                    r = await api_client.call("POST", "/api/scam-check", auth=False, json={"text": box.value})
                    btn.props(remove="loading")
                    if not r.ok:
                        notify_error(r)
                        return
                    show(r.data)

                btn = ui.button(t("check_message"), icon="policy", on_click=check).props("unelevated no-caps").classes("sf-btn self-start")
            out = ui.column().classes("gap-3 w-full")

        def show(d: dict[str, Any]) -> None:
            out.clear()
            with out:
                with ui.column().classes("sf-card p-5 gap-2 w-full"):
                    with ui.row().classes("items-center gap-3"):
                        chip(t(f"v_{d['verdict']}"), VERDICT_KIND[d["verdict"]],
                             {"likely_scam": "dangerous", "suspicious": "warning", "consistent": "verified"}[d["verdict"]])
                        ui.label(f"{round(d['score'] * 100)}%").classes("sf-serif text-2xl font-bold")
                    ui.linear_progress(d["score"], show_value=False).props(
                        f"rounded size=10px color={'negative' if d['score'] >= 0.7 else 'warning' if d['score'] >= 0.4 else 'positive'}")
                    ui.label(f"{d['model']}" + (f" · classifier {round(d['classifier_probability'] * 100)}%"
                                                if d["classifier_probability"] is not None else "")).classes("text-xs sf-muted")
                    if d.get("match_note"):
                        ui.label(L(d["match_note"])).classes("text-sm p-3 rounded-xl bg-[var(--sf-green-50)]")
                if d["flags"]:
                    with ui.column().classes("sf-card p-5 gap-2 w-full"):
                        ui.label(t("red_flags")).classes("font-bold")
                        for f in d["flags"]:
                            with ui.row().classes("items-start gap-2 no-wrap"):
                                ui.icon("flag", size="18px").classes("sf-rule-fail mt-[2px]")
                                ui.label(L(f)).classes("text-sm")
                with ui.column().classes("sf-card p-5 gap-2 w-full"):
                    ui.label(t("what_to_do")).classes("font-bold")
                    for tip in d["tips"][lang()]:
                        ui.label("• " + tip).classes("text-sm")
                    with ui.row().classes("gap-3"):
                        link_button(t("report_fraud") + " ↗", d["report"]["portal"], icon="report", new_tab=True)
                        ui.label(f"☎ {d['report']['helpline']}").classes("sf-serif text-xl font-bold self-center")
    return None

# ============================================================================ updates (public)


@ui.page("/updates")
async def updates_page() -> None:
    r = await api_client.call("GET", "/api/updates", auth=False)
    with frame(t("nav_updates"), active="/updates"):
        ui.label(t("updates_title")).classes("sf-title")
        ui.label(t("updates_sub")).classes("sf-muted")
        rows = r.data if r.ok else []
        if not rows:
            ui.label(t("updates_empty")).classes("sf-muted")
        for u in rows:
            with ui.column().classes("sf-card p-5 gap-2 w-full"):
                with ui.row().classes("items-center gap-3 w-full"):
                    ui.label(L(u["name"])).classes("font-bold text-lg flex-grow")
                    chip(f"v{u['version']}", "neutral")
                    ui.label(fmt_dt(u["at"])).classes("text-xs sf-muted")
                for c in u["changes"]:
                    ui.label("• " + L(c)).classes("text-sm")
                if u.get("note"):
                    ui.label(u["note"]).classes("text-xs sf-muted italic")
                ui.link(t("view_scheme") + " →", f"/scheme/{u['slug']}").classes("sf-link text-sm")

# ============================================================================ admin: AI & evaluation


@ui.page("/admin/ai")
async def admin_ai() -> RedirectResponse | None:
    if (redirect := guard("admin")):
        return redirect
    from app.ui.pages.admin import admin_frame

    with admin_frame("adm_ai") as body:
        @ui.refreshable
        async def content() -> None:
            r = await api_client.call("GET", "/api/admin/ai/evaluations")
            if not r.ok:
                notify_error(r)
                return
            runs, versions = r.data["runs"], r.data["versions"]
            if not runs:
                ui.label(t("no_eval")).classes("sf-muted")
            else:
                m = runs[0]["metrics"]
                pct = lambda v: "—" if v is None else f"{round(v * 100)}%"  # noqa: E731
                cards = [
                    ("Eligibility accuracy", pct(m["eligibility"]["accuracy"]), f"n={m['eligibility']['n']}"),
                    ("Rule extraction F1", pct(m["rule_extraction"]["f1"]), f"n={m['rule_extraction']['n']} · {m['rule_extraction']['method']}"),
                    ("RAG correctness", pct(m["rag"]["correctness"]), f"n={m['rag']['n']} · {m['rag']['mode']}"),
                    ("Citation accuracy", pct(m["rag"]["citation_accuracy"]), ""),
                    ("Off-topic declined", pct(m["rag"]["abstention"]["off_topic_declined"]), "wrong answers avoided"),
                    ("Answerable lost", pct(m["rag"]["abstention"]["answerable_lost"]), "correct answers lost to declining"),
                    ("Document field accuracy", pct(m["documents"]["field_accuracy"]), f"{m['documents']['fields']} fields"),
                    ("Scam precision / recall", f"{pct(m['scam']['precision'])} / {pct(m['scam']['recall'])}", f"n={m['scam']['n']} held-out"),
                    ("Questions asked", f"{m['interview']['avg_questions_adaptive']} vs {m['interview']['questions_full_form']}",
                     f"same result {pct(m['interview']['same_eligible_set'])}"),
                    ("Hindi speech WER", pct(m["speech"]["wer"]) if m["speech"].get("measured") else "not measured",
                     m["speech"].get("reason", "")),
                    ("Median latency", f"{m['eligibility']['median_ms_per_check']} ms / {m['rag']['median_latency_ms']} ms",
                     "matching (per scheme) / chat"),
                ]
                with ui.element("div").classes("grid sm:grid-cols-2 xl:grid-cols-3 gap-4 w-full"):
                    for label, value, sub in cards:
                        with ui.column().classes("sf-card p-4 gap-0"):
                            ui.label(value).classes("sf-serif text-2xl font-extrabold text-[var(--sf-green)]")
                            ui.label(label).classes("font-semibold text-sm")
                            ui.label(sub).classes("text-xs sf-muted")
                ui.label(f"{fmt_dt(runs[0]['at'])} · {runs[0]['seconds']} s").classes("text-xs sf-muted")
                with ui.expansion("Errors / details", icon="bug_report").classes("w-full rounded-xl border border-[var(--sf-line)]"):
                    ui.code(json.dumps({k: v.get("errors", v) for k, v in m.items()}, ensure_ascii=False, indent=2,
                                       default=str), language="json").classes("w-full text-xs")
                with ui.expansion(t("past_runs"), icon="history").classes("w-full rounded-xl border border-[var(--sf-line)]"):
                    for run in runs:
                        mm = run["metrics"]
                        ui.label(f"{fmt_dt(run['at'])} · eligibility {pct(mm['eligibility']['accuracy'])} · "
                                 f"RAG {pct(mm['rag']['correctness'])} · scam P/R {pct(mm['scam']['precision'])}/"
                                 f"{pct(mm['scam']['recall'])} · {run['versions']['embedder']} · {run['versions']['llm']}") \
                            .classes("text-sm")
            with ui.column().classes("sf-card p-4 gap-1 w-full"):
                ui.label(t("model_versions")).classes("font-bold")
                ui.code(json.dumps(versions, indent=2), language="json").classes("w-full text-xs")
            q = await api_client.call("GET", "/api/admin/ai/queue")
            with ui.column().classes("sf-card p-4 gap-2 w-full"):
                ui.label(t("low_conf_queue")).classes("font-bold")
                for item in (q.data if q.ok else [])[:30]:
                    with ui.column().classes("gap-0 border-b border-[var(--sf-line)] pb-2 w-full"):
                        ui.label(item["question"]).classes("text-sm font-semibold")
                        tag = "👎 " + ((item.get("feedback") or {}).get("comment") or "") if (item.get("feedback") or {}).get("rating") == "down" \
                            else f"declined: {item.get('abstain_reason')}"
                        ui.label(f"{fmt_dt(item['created_at'])} · {tag} · {item.get('language')}").classes("text-xs sf-muted")
                if not (q.ok and q.data):
                    ui.label(t("none_found")).classes("sf-muted text-sm")

        async def run_eval() -> None:
            btn.props("loading")
            r = await api_client.call("POST", "/api/admin/ai/evaluate")
            btn.props(remove="loading")
            if not r.ok:
                notify_error(r)
                return
            await content.refresh()

        with body:
            ui.label(t("eval_note")).classes("text-sm sf-muted")
            btn = ui.button(t("run_eval"), icon="science", on_click=run_eval).props("unelevated no-caps").classes("sf-btn self-start")
            with ui.column().classes("gap-4 w-full"):
                await content()
    return None
