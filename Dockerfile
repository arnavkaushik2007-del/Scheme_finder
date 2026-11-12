FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN apt-get update     && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin     && rm -rf /var/lib/apt/lists/*     && useradd --create-home --uid 10001 appuser     && mkdir -p /app/storage/documents && chown -R appuser /app

COPY requirements.txt requirements-ai.txt ./
ARG INSTALL_AI=true
# CPU-only torch keeps the image ~1 GB smaller than the default CUDA build.
RUN if [ "$INSTALL_AI" = "true" ]; then       pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-ai.txt;     else pip install -r requirements.txt; fi
ENV HF_HOME=/app/storage/hf

COPY app ./app
COPY scripts ./scripts

USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
