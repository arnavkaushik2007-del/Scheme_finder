"""NiceGUI front-end, mounted on the FastAPI app (Python only, no JS build step)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from nicegui import app as nicegui_app
from nicegui import ui

from app.config import get_settings

STATIC = Path(__file__).parent / "static"


def mount(api: FastAPI) -> None:
    from app.ui import api_client
    from app.ui.pages import (  # noqa: F401 - registers @ui.page routes
        admin, applications, assistant, auth, check, dashboards, documents, features, home, officer, profile,
        schemes,
    )

    api_client.bind(api)
    nicegui_app.add_static_files("/static", STATIC)
    settings = get_settings()
    ui.run_with(
        api,
        title="Scheme Finder",
        favicon=STATIC / "favicon.svg",
        storage_secret=settings.storage_secret,
        language="en-GB",
        show_welcome_message=False,
    )
