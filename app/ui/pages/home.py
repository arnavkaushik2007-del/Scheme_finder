"""Public landing page (/) and About (/about)."""

from __future__ import annotations

from collections import Counter
from urllib.parse import quote

from nicegui import ui

from app.ui import api_client
from app.ui.i18n import CATEGORY, GROUPS, L, lang, t
from app.ui.theme import (
    CATEGORY_STYLE, fmt_date, frame, hero_src, home_for_role, icon_tile, link_button, scheme_card,
)


def _gate(path: str, logged_in: str | None) -> str:
    """Scheme pages need a login; send visitors to log in first and then on to where they clicked."""
    return path if logged_in else "/login?next=" + quote(path, safe="")


@ui.page("/")
async def home_page() -> None:
    stats = await api_client.call("GET", "/api/schemes/stats", auth=False)
    schemes = await api_client.call("GET", "/api/schemes", auth=False)
    s = stats.data if stats.ok else {"schemes": 0, "last_verified": None}
    rows = schemes.data if schemes.ok else []
    logged_in = api_client.role()
    user = logged_in == "user"
    hi = lang() == "hi"
    central = sum(1 for r in rows if r["level"] == "central")
    by_cat = Counter(r["category"] for r in rows)
    by_group = Counter(g for r in rows for g in r.get("beneficiary_groups", []))

    with frame(active="/", full_width=True):
        # ---------------------------------------------------------------- hero
        with ui.element("section").classes("w-full").style(
                "background:radial-gradient(1100px 520px at 88% 12%, #fbe6c4 0%, rgba(251,246,236,0) 62%),"
                "radial-gradient(700px 400px at 0% 100%, #e7f1eb 0%, rgba(251,246,236,0) 60%)"):
            with ui.element("div").classes("sf-wrap grid lg:grid-cols-[1.05fr_1fr] gap-10 lg:gap-14 items-center "
                                           "pt-10 pb-16 lg:pt-16 lg:pb-24"):
                with ui.column().classes("gap-6 sf-rise min-w-0"):
                    with ui.element("span").classes("sf-pill"):
                        ui.icon("auto_awesome", size="18px").classes("text-[var(--sf-orange)]")
                        ui.label(t("hero_pill"))
                    ui.label(t("hero_title")).classes("sf-display")
                    ui.label(t("hero_sub")).classes("sf-lead max-w-xl")

                    async def go_search() -> None:
                        q = (search.value or "").strip()
                        ui.navigate.to(_gate("/explore" + (f"?q={quote(q)}" if q else ""), logged_in))

                    with ui.row().classes("w-full max-w-xl gap-2 no-wrap items-center"):
                        search = ui.input(placeholder=t("explore_search")).props("outlined clearable") \
                            .classes("sf-search flex-grow min-w-0")
                        with search.add_slot("prepend"):
                            ui.icon("search").classes("text-[var(--sf-green)]")
                        search.on("keydown.enter", go_search)
                        ui.button(icon="arrow_forward", on_click=go_search).props(
                            'unelevated round aria-label="Search"').classes("w-[58px] h-[58px] shrink-0")
                    with ui.row().classes("gap-3 items-center"):
                        link_button(t("cta_find") + "  →", "/interview" if user else
                                    (home_for_role(logged_in) if logged_in else "/login?next=/interview"))
                        link_button(t("upload_doc"), "/documents" if user else "/login?next=/documents",
                                    icon="upload_file", outline=True)
                        with ui.link(target=_gate("/assistant", logged_in)) \
                                .classes("sf-link no-underline flex items-center gap-2 px-2"):
                            ui.icon("contact_support", size="22px")
                            ui.label(t("ask_question"))
                    with ui.row().classes("gap-x-8 gap-y-3 items-start pt-4 border-t border-[var(--sf-line)] w-full max-w-xl"):
                        for value, label in ((fmt_date(s["last_verified"]), t("trust_verified")),):
                            with ui.column().classes("gap-0"):
                                ui.label(value).classes("sf-stat")
                                ui.label(label).classes("sf-muted text-sm")
                        with ui.row().classes("gap-2 items-start no-wrap max-w-[250px]"):
                            ui.icon("verified_user", size="22px").classes("text-[var(--sf-green)] shrink-0")
                            ui.label(t("trust_notice")).classes("sf-muted text-sm leading-snug")
                with ui.element("div").classes("relative sf-rise pb-6"):
                    ui.image(hero_src()).classes("w-full rounded-[28px] shadow-2xl").props(
                        'alt="Illustration of a family walking to a village government office"')
                    with ui.link(target="/scam-check").classes(
                            "no-underline text-inherit sf-card sf-card-hover absolute -bottom-2 left-3 right-3 "
                            "sm:left-auto sm:right-6 sm:w-[360px] p-4"):
                        with ui.row().classes("items-center gap-3 no-wrap w-full"):
                            icon_tile("gpp_maybe", "#e0761b", "#fdf0e3", 46)
                            ui.label(t("scam_card")).classes("font-semibold text-[var(--sf-ink)] flex-grow leading-snug")
                            ui.icon("arrow_forward").classes("text-[var(--sf-ink)]")

        # ---------------------------------------------------------------- stats band
        with ui.element("section").classes("w-full"):
            with ui.element("div").classes("sf-wrap grid grid-cols-1 sm:grid-cols-3 gap-4"):
                for value, label, icon, color, tint, href in (
                        (len(rows), "कुल योजनाएँ" if hi else "Total schemes", "inventory_2", "#0f4c35", "#e7f1eb", "/explore"),
                        (central, "केंद्रीय योजनाएँ" if hi else "Central schemes", "account_balance", "#1d4ed8", "#e7eefc",
                         "/explore?level=central"),
                        (len(rows) - central, "राज्य योजनाएँ" if hi else "State schemes", "map", "#b45309", "#fdf1e1",
                         "/explore?level=state")):
                    with ui.link(target=_gate(href, logged_in)).classes("no-underline text-inherit"):
                        with ui.row().classes("sf-card sf-card-hover p-5 items-center gap-4 no-wrap h-full"):
                            icon_tile(icon, color, tint, 52)
                            with ui.column().classes("gap-0"):
                                ui.label(str(value)).classes("sf-stat")
                                ui.label(label).classes("sf-muted")
                            ui.icon("arrow_forward").classes("ml-auto text-[var(--sf-muted)]")

        # ---------------------------------------------------------------- categories
        with ui.element("section").classes("w-full pt-16"):
            with ui.column().classes("sf-wrap gap-7"):
                with ui.column().classes("gap-1"):
                    ui.label("श्रेणी के अनुसार खोजें" if hi else "Browse by category").classes("sf-title")
                    ui.label("अपनी ज़रूरत की श्रेणी चुनें।" if hi else "Pick the kind of help you need.").classes("sf-lead")
                with ui.element("div").classes("grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4 w-full"):
                    for cat, (icon, color, tint) in CATEGORY_STYLE.items():
                        if not by_cat.get(cat):
                            continue
                        with ui.link(target=_gate(f"/explore?category={cat}", logged_in)).classes("no-underline text-inherit"):
                            with ui.column().classes("sf-card sf-card-hover p-5 gap-3 items-center text-center h-full"):
                                icon_tile(icon, color, tint, 60)
                                ui.label(L(CATEGORY.get(cat))).classes("font-semibold leading-snug")
                                ui.label(f"{by_cat[cat]} " + ("योजनाएँ" if hi else ("scheme" if by_cat[cat] == 1 else "schemes"))) \
                                    .classes("text-sm font-semibold").style(f"color:{color}")

        # ---------------------------------------------------------------- who it's for
        with ui.element("section").classes("w-full pt-14"):
            with ui.column().classes("sf-wrap gap-5"):
                ui.label("यह किसके लिए है" if hi else "Who it's for").classes("sf-h3 text-[1.45rem]")
                with ui.row().classes("gap-2"):
                    for g, label in GROUPS.items():
                        if not by_group.get(g):
                            continue
                        with ui.link(target=_gate(f"/explore?group={g}", logged_in)).classes("no-underline"):
                            with ui.row().classes("sf-pill hover:bg-[var(--sf-green-50)] transition-colors items-center"):
                                ui.label(L(label))
                                ui.label(str(by_group[g])).classes(
                                    "text-xs bg-[var(--sf-green)] text-white rounded-full px-2 py-[1px]")

        # ---------------------------------------------------------------- how it works
        with ui.element("section").classes("w-full pt-16"):
            with ui.column().classes("sf-wrap gap-7"):
                ui.label(t("how_title")).classes("sf-title")
                with ui.element("div").classes("grid sm:grid-cols-2 lg:grid-cols-4 gap-5 w-full"):
                    for n, icon in zip(range(1, 5), ("how_to_reg", "edit_note", "fact_check", "track_changes"), strict=True):
                        with ui.column().classes("sf-card p-6 gap-3 relative overflow-hidden"):
                            ui.label(str(n)).classes("sf-num absolute right-5 top-4 opacity-20 text-[64px]")
                            icon_tile(icon, "#e0761b", "#fdf0e3", 48)
                            ui.label(t(f"how{n}_t")).classes("sf-h3")
                            ui.label(t(f"how{n}_d")).classes("sf-muted")

        # ---------------------------------------------------------------- popular schemes
        with ui.element("section").classes("w-full mt-16 py-16 sf-dots border-y border-[var(--sf-line)]"):
            with ui.column().classes("sf-wrap gap-7"):
                with ui.row().classes("w-full items-end justify-between gap-3"):
                    ui.label(t("db_title")).classes("sf-title")
                    ui.link(t("see_all"), _gate("/explore", logged_in)).classes("sf-link text-lg")
                with ui.element("div").classes("grid sm:grid-cols-2 lg:grid-cols-4 gap-5 w-full"):
                    for row in sorted(rows, key=lambda r: -r["annual_benefit_inr"])[:8]:
                        scheme_card(row, _gate(f"/scheme/{row['slug']}", logged_in))

        # ---------------------------------------------------------------- trust band
        with ui.element("section").classes("w-full pt-16"):
            with ui.element("div").classes("sf-wrap"):
                with ui.element("div").classes("sf-band p-8 md:p-12 grid md:grid-cols-3 gap-8"):
                    for icon, en_t, hi_t, en_d, hi_d in (
                            ("rule", "Rules you can check", "नियम जिन्हें आप जाँच सकें",
                             "Every result shows the exact rule that matched, failed or is unknown. AI never decides.",
                             "हर नतीजे में वह नियम दिखता है जो मिला, नहीं मिला या अज्ञात है। AI निर्णय नहीं लेता।"),
                            ("record_voice_over", "Hindi, English and voice", "हिंदी, अंग्रेज़ी और आवाज़",
                             "Switch language on any page, speak your answers and hear replies read aloud.",
                             "किसी भी पेज पर भाषा बदलें, बोलकर जवाब दें और जवाब सुनें।"),
                            ("lock", "Private by design", "शुरू से ही निजी",
                             "Only the last 4 digits of Aadhaar are kept, files are encrypted, and you can delete everything.",
                             "आधार के केवल अंतिम 4 अंक रखे जाते हैं, फ़ाइलें एन्क्रिप्ट रहती हैं, और आप सब कुछ मिटा सकते हैं।")):
                        with ui.column().classes("gap-3"):
                            ui.html(f'<div class="sf-ico" style="width:52px;height:52px;background:rgba(255,255,255,.12)">'
                                    f'<span class="material-icons" style="color:#fbd38d;font-size:28px">{icon}</span></div>',
                                    sanitize=False)
                            ui.label(hi_t if hi else en_t).classes("sf-serif text-2xl font-bold")
                            ui.label(hi_d if hi else en_d).classes("opacity-85 leading-relaxed")

        # ---------------------------------------------------------------- closing CTA
        with ui.element("section").classes("w-full pt-16"):
            with ui.element("div").classes("sf-wrap"):
                with ui.element("div").classes("sf-card p-8 md:p-10 grid md:grid-cols-[1fr_auto] gap-6 items-center"):
                    with ui.column().classes("gap-2"):
                        ui.label("दो मिनट में अपनी योजनाएँ जानें" if hi else "Find your schemes in two minutes").classes("sf-title")
                        ui.label("कुछ आसान सवाल — कोई भी जवाब छोड़ सकते हैं।" if hi
                                 else "A few simple questions — skip any you don't know.").classes("sf-lead")
                    with ui.row().classes("gap-3"):
                        link_button(t("cta_find") + "  →", "/interview" if user else
                                    (home_for_role(logged_in) if logged_in else "/login?next=/interview"))
                        if not logged_in:
                            link_button(t("cta_register"), "/register", outline=True)


@ui.page("/about")
async def about_page() -> None:
    res = await api_client.call("GET", "/api/schemes", auth=False)
    rows = res.data if res.ok else []
    hi = lang() == "hi"
    with frame(t("nav_about"), active="/about"):
        ui.label(t("about_title")).classes("sf-title")
        sections = [
            ("Disclaimer", "अस्वीकरण",
             "Scheme Finder is a student prototype. It is not an official government website, it is not legal advice, and it never promises approval. Eligibility shown here is an estimate from simplified rules — always confirm on the official portal or at the government office.",
             "योजना खोजक एक छात्र प्रोटोटाइप है। यह आधिकारिक सरकारी वेबसाइट नहीं है, कानूनी सलाह नहीं है, और मंज़ूरी का वादा नहीं करता। यहाँ दिखाई गई पात्रता सरल नियमों पर आधारित अनुमान है — हमेशा आधिकारिक पोर्टल या सरकारी कार्यालय में पुष्टि करें।"),
            ("Privacy, in plain words", "गोपनीयता — सरल शब्दों में",
             "We store only what you choose to save. We never store a full Aadhaar number (only the last four digits). Uploaded files are encrypted. Officers see only applications assigned to them, and every officer and admin action is written to a tamper-evident audit log. You can delete your data at any time.",
             "हम केवल वही रखते हैं जिसे आप सहेजना चुनें। पूरा आधार नंबर कभी नहीं रखा जाता (केवल अंतिम चार अंक)। अपलोड की गई फ़ाइलें एन्क्रिप्ट रहती हैं। अधिकारी केवल उन्हें सौंपे गए आवेदन देखते हैं, और हर अधिकारी व एडमिन की कार्रवाई छेड़छाड़-रोधी ऑडिट लॉग में दर्ज होती है। आप कभी भी अपना डेटा मिटा सकते हैं।"),
            ("How eligibility is decided", "पात्रता कैसे तय होती है",
             "A fixed rules engine checks each rule (age, income, category …) and shows which rule matched, failed or is unknown. AI is used only to read documents, explain and chat — never to make the decision. Final approval is always by a human officer.",
             "एक तय नियम-इंजन हर नियम (आयु, आय, वर्ग …) जाँचता है और दिखाता है कि कौन-सा नियम मिला, नहीं मिला या अज्ञात है। AI का उपयोग केवल दस्तावेज़ पढ़ने, समझाने और बातचीत के लिए है — निर्णय के लिए कभी नहीं। अंतिम मंज़ूरी हमेशा अधिकारी देते हैं।"),
            ("Help", "मदद",
             "1. Register and log in.  2. Open “Check eligibility” and answer what you know.  3. Read the reasons on each card.  4. Open a scheme to see documents and steps, then verify on the official portal.",
             "1. पंजीकरण करें और लॉग इन करें।  2. “पात्रता जाँचें” खोलें और जो पता हो वह भरें।  3. हर कार्ड पर कारण पढ़ें।  4. योजना खोलकर दस्तावेज़ और चरण देखें, फिर आधिकारिक पोर्टल पर जाँचें।"),
        ]
        for en_t, hi_t, en_b, hi_b in sections:
            with ui.column().classes("sf-card p-6 gap-2 w-full"):
                ui.label(hi_t if hi else en_t).classes("sf-h3 text-[1.45rem]")
                ui.label(hi_b if hi else en_b).classes("leading-relaxed")
        with ui.column().classes("sf-card p-6 gap-2 w-full"):
            ui.label("डेटा स्रोत" if hi else "Data sources").classes("sf-h3 text-[1.45rem]")
            ui.label("योजना की जानकारी सार्वजनिक सरकारी पोर्टलों से संकलित नमूना डेटा है (सरकारी सामग्री, संदर्भ हेतु)। हर योजना का स्रोत:" if hi else
                     "Scheme information is sample data compiled from public government portals (government content, used for reference). Source for every scheme:").classes("sf-muted")
            with ui.element("ul").classes("list-disc pl-6 columns-1 md:columns-2 gap-8"):
                for r in rows:
                    with ui.element("li"):
                        ui.link(L(r["name"]), r["official_url"], new_tab=True).classes("sf-link")
