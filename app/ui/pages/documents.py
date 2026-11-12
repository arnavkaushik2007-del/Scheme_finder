"""/documents — upload, view, replace, delete; review extracted fields with confidence; confirm into profile."""

from __future__ import annotations

import base64
from typing import Any

from fastapi.responses import RedirectResponse
from nicegui import events, ui

from app.ui import api_client
from app.ui.i18n import L, error_text, lang, t
from app.ui.pages.check import load_vocab
from app.ui.theme import chip, fmt_date, frame, guard, notify_error

DOC_TYPES = ["aadhaar", "income_certificate", "caste_certificate", "ration_card", "bpl_card",
             "disability_certificate", "marksheet", "bank_passbook", "land_record", "birth_certificate",
             "age_proof", "domicile_certificate"]
EXTRA_LABELS = {
    "full_name": {"en": "Full name", "hi": "पूरा नाम"}, "date_of_birth": {"en": "Date of birth", "hi": "जन्म तिथि"},
    "aadhaar_last4": {"en": "Aadhaar (last 4)", "hi": "आधार (अंतिम 4)"}, "district": {"en": "District", "hi": "ज़िला"},
    "bank_account_last4": {"en": "Bank account (last 4)", "hi": "बैंक खाता (अंतिम 4)"},
    "issue_date": {"en": "Date of issue", "hi": "जारी करने की तिथि"},
}
STATUS_KIND = {"needs_review": "maybe", "confirmed": "eligible", "no_fields": "not_eligible"}


async def _doc_type_names() -> dict[str, str]:
    res = await api_client.call("GET", "/api/schemes/vocabulary", auth=False)
    _ = res
    from app.services.schemes import load_seed_files  # catalogue names (static data)

    _, catalog = load_seed_files()
    names = {d: L(catalog[d]["name"]) if d in catalog else d.replace("_", " ").title() for d in DOC_TYPES}
    names["other"] = "अन्य" if lang() == "hi" else "Other"
    return names


def _field_label(vocab: dict[str, Any], name: str) -> str:
    if name in vocab["fields"]:
        return L(vocab["fields"][name]["label"])
    return L(EXTRA_LABELS.get(name)) or name


def _display_value(vocab: dict[str, Any], name: str, value: Any) -> str:
    if name in vocab["enums"] and value in vocab["enums"][name]:
        return L(vocab["enums"][name][value])
    if isinstance(value, bool):
        return t("yes") if value else t("no")
    if name == "aadhaar_last4":
        return f"XXXX XXXX {value}"
    if name == "bank_account_last4":
        return f"XXXXXX{value}"
    return str(value)


@ui.page("/documents")
async def documents_page() -> RedirectResponse | None:
    if (redirect := guard("user")):
        return redirect
    vocab = await load_vocab()
    names = await _doc_type_names()
    prof = await api_client.call("GET", "/api/me/profile")
    once = not prof.ok or prof.data["consent"].get("mode") != "save_profile" \
        or bool(prof.data["consent"].get("withdrawn_at"))

    with frame(t("nav_documents"), active="/documents"):
        with ui.column().classes("gap-1"):
            ui.label(t("mydocs_title")).classes("sf-title")
            ui.label(t("docs_sub")).classes("sf-muted text-lg")
        if once:
            with ui.row().classes("items-center gap-3 p-4 rounded-2xl bg-[var(--sf-amber-50)] w-full no-wrap"):
                ui.icon("info", size="22px").classes("text-[var(--sf-amber)]")
                ui.label(t("once_note"))
                ui.link(t("change_choice") + " →", "/start").classes("sf-link ml-auto")

        # ------------------------------------------------------------ upload card
        with ui.element("div").classes("sf-card p-5 md:p-6 grid md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-6 w-full"):
            with ui.column().classes("gap-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("1").classes("sf-num text-2xl")
                    ui.label(t("doc_type")).classes("font-bold text-lg")
                dtype = ui.select(names, value="aadhaar", with_input=True).props("outlined") \
                    .classes("sf-input w-full")
                with ui.row().classes("items-start gap-2 no-wrap"):
                    ui.icon("lock", size="18px").classes("text-[var(--sf-green)] mt-[2px]")
                    ui.label(t("upload_hint")).classes("text-sm sf-muted")
            upload_col = ui.column().classes("gap-3 w-full")
            with upload_col:
                with ui.row().classes("items-center gap-2"):
                    ui.label("2").classes("sf-num text-2xl")
                    ui.label(t("upload_doc")).classes("font-bold text-lg")
        once_result = ui.column().classes("w-full")

        async def handle(e: events.UploadEventArguments) -> None:
            f = e.file
            content = await f.read()
            files = {"file": (f.name, content, f.content_type or "application/octet-stream")}
            path = "/api/extract" if once else "/api/documents"
            res = await api_client.call("POST", path, data={"doc_type": dtype.value}, files=files)
            uploader.reset()
            if not res.ok:
                ui.notify(error_text(res.code, res.detail), type="negative", position="top")
                return
            if once:
                once_result.clear()
                with once_result:
                    _fields_view(vocab, res.data)
                return
            ui.notify("✓", type="positive")
            await doc_list.refresh()

        with upload_col:
            uploader = ui.upload(on_upload=handle, auto_upload=True, max_file_size=5 * 1024 * 1024,
                                 label=t("drop_here"),
                                 on_rejected=lambda: ui.notify(t("err_file_too_large", max_mb=5), type="negative")) \
                .props('accept=".pdf,.png,.jpg,.jpeg,.webp" flat') \
                .classes("sf-drop w-full")

        # ------------------------------------------------------------ list
        @ui.refreshable
        async def doc_list() -> None:
            res = await api_client.call("GET", "/api/documents")
            if not res.ok:
                if res.code != "forbidden_role":
                    notify_error(res)
                return
            if not res.data:
                ui.label(t("no_docs")).classes("sf-muted")
                return
            with ui.element("div").classes("grid lg:grid-cols-2 gap-5 w-full"):
                for d in res.data:
                    _doc_card(d, vocab, names, doc_list)

        if not once:
            await doc_list()
    return None


def _fields_view(vocab: dict[str, Any], ex: dict[str, Any]) -> None:
    if not ex["ocr_available"] and ex["method"] == "none":
        with ui.row().classes("items-start gap-2 p-3 rounded-xl bg-[var(--sf-amber-50)] no-wrap w-full"):
            ui.icon("warning_amber", size="20px").classes("text-[var(--sf-amber)]")
            ui.label(t("ocr_missing")).classes("text-sm")
    if "aadhaar_masked" in ex.get("warnings", []):
        with ui.row().classes("items-center gap-2 text-sm"):
            ui.icon("lock", size="18px").classes("text-[var(--sf-green)]")
            ui.label(t("aadhaar_masked_note"))
    if ex.get("llm_is_mock"):
        ui.label(t("llm_mock_note")).classes("text-xs sf-muted")
    if not ex["fields"]:
        ui.label(t("status_no_fields")).classes("sf-muted")
        return
    ui.label(t("extracted")).classes("font-semibold")
    for name, f in ex["fields"].items():
        conf = float(f["confidence"])
        with ui.row().classes("items-center w-full gap-3 no-wrap"):
            ui.label(_field_label(vocab, name)).classes("sf-muted w-40 shrink-0")
            ui.label(_display_value(vocab, name, f["value"])).classes("font-semibold flex-grow")
            ui.linear_progress(conf, show_value=False).props(
                f"rounded size=8px color={'positive' if conf >= 0.8 else 'warning'}").classes("w-20")
            ui.label(f"{round(conf * 100)}%").classes("text-xs sf-muted w-10")


def _doc_card(d: dict[str, Any], vocab: dict[str, Any], names: dict[str, str], refresh: Any) -> None:
    with ui.column().classes("sf-card p-5 gap-3"):
        with ui.row().classes("w-full items-start justify-between no-wrap"):
            with ui.column().classes("gap-0"):
                ui.label(names.get(d["doc_type"], d["doc_type"])).classes("text-lg font-bold")
                ui.label(f"{d['filename']} · {round((d['size'] or 0) / 1024)} KB · {fmt_date(str(d['created_at']))}"
                         + (f" · v{d['version']}" if d.get("version", 1) > 1 else "")).classes("text-xs sf-muted")
            chip(t(f"status_{d['status']}"), STATUS_KIND.get(d["status"], "neutral"))
        if d.get("extracted"):
            _fields_view(vocab, d["extracted"])

        with ui.row().classes("gap-2"):
            if d.get("extracted", {}).get("fields"):
                ui.button(t("review_confirm"), icon="fact_check", on_click=lambda d=d: _review(d, vocab, refresh)) \
                    .props("unelevated no-caps dense").classes("sf-btn")
            ui.button(t("view"), icon="visibility", on_click=lambda d=d: _view(d)).props("flat no-caps dense")

            async def on_replace(e: events.UploadEventArguments, d: dict[str, Any] = d) -> None:
                content = await e.file.read()
                res = await api_client.call("PUT", f"/api/documents/{d['id']}/file",
                                            files={"file": (e.file.name, content, e.file.content_type or "")})
                if not res.ok:
                    notify_error(res)
                    return
                await refresh.refresh()

            with ui.button(t("replace"), icon="swap_horiz").props("flat no-caps dense"):
                with ui.menu().classes("p-3"):
                    ui.upload(on_upload=on_replace, auto_upload=True, max_file_size=5 * 1024 * 1024) \
                        .props('accept=".pdf,.png,.jpg,.jpeg,.webp" flat bordered')

            async def on_delete(d: dict[str, Any] = d) -> None:
                res = await api_client.call("DELETE", f"/api/documents/{d['id']}")
                if not res.ok:
                    notify_error(res)
                    return
                await refresh.refresh()

            ui.button(t("delete"), icon="delete", on_click=on_delete).props("flat no-caps dense color=negative")


async def _view(d: dict[str, Any]) -> None:
    res = await api_client.call("GET", f"/api/documents/{d['id']}/file")
    if not res.ok:
        notify_error(res)
        return
    data: bytes = res.data["_bytes"]
    pages: list[str] = []
    if d["content_type"] == "application/pdf":
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            for page in list(pdf)[:5]:
                pages.append("data:image/png;base64," + base64.b64encode(page.get_pixmap(dpi=110).tobytes("png")).decode())
    else:
        pages.append(f"data:{d['content_type']};base64," + base64.b64encode(data).decode())
    with ui.dialog() as dlg, ui.card().classes("sf-card p-4 gap-3 w-[min(900px,95vw)] max-h-[90vh] overflow-auto"):
        with ui.row().classes("w-full justify-between items-center"):
            ui.label(d["filename"]).classes("font-semibold")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")
        for src in pages:
            ui.image(src).classes("w-full rounded-lg border border-[var(--sf-line)]")
    dlg.open()


def _review(d: dict[str, Any], vocab: dict[str, Any], refresh: Any) -> None:
    fields = d["extracted"]["fields"]
    widgets: dict[str, Any] = {}
    with ui.dialog() as dlg, ui.card().classes("sf-card p-6 gap-3 w-[min(560px,95vw)]"):
        ui.label(t("review_confirm")).classes("sf-h3 text-[1.45rem]")
        for name, f in fields.items():
            label = f"{_field_label(vocab, name)} · {round(float(f['confidence']) * 100)}% {t('confidence')}"
            value = f["value"]
            if name in vocab["enums"]:
                widgets[name] = ui.select({k: L(v) for k, v in vocab["enums"][name].items()}, value=value, label=label)
            elif name == "state":
                widgets[name] = ui.select(vocab["states"], value=value, label=label, with_input=True)
            elif isinstance(value, bool):
                widgets[name] = ui.select({True: t("yes"), False: t("no")}, value=value, label=label)
            elif isinstance(value, (int, float)):
                widgets[name] = ui.number(label, value=value)
            else:
                widgets[name] = ui.input(label, value=str(value))
            widgets[name].props("outlined dense").classes("sf-input w-full")
            if f.get("alternative") is not None:
                ui.label(f"AI: {f['alternative']}").classes("text-xs sf-muted -mt-2")

        async def confirm() -> None:
            out: dict[str, Any] = {}
            for name, w in widgets.items():
                v = w.value
                if isinstance(fields[name]["value"], int) and not isinstance(fields[name]["value"], bool) and v is not None:
                    v = int(v)
                out[name] = v
            res = await api_client.call("POST", f"/api/documents/{d['id']}/confirm", json={"fields": out})
            if not res.ok:
                notify_error(res)
                return
            dlg.close()
            ui.notify(t("saved_ok"), type="positive", position="top")
            await refresh.refresh()

        with ui.row().classes("justify-end w-full"):
            ui.button(t("cancel"), on_click=dlg.close).props("flat no-caps")
            ui.button(t("confirm_use"), on_click=confirm).props("unelevated no-caps").classes("sf-btn")
    dlg.open()
