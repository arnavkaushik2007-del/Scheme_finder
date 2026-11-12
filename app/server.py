"""ASGI entry point: ``uvicorn app.server:app``. Kept separate so tests can build the API
without mounting the NiceGUI front-end."""

from app.main import create_app

app = create_app()
