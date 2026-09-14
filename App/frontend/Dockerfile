# Streamlit frontend.
#
# Build context is the REPOSITORY ROOT (see docker-compose.yml), not this
# directory, because the entry point is the root streamlit_app.py shim which
# imports App/frontend/streamlit_app.py.
#
# This image installs App/frontend/requirements.txt, not the root
# requirements.txt. The root file pins TA-Lib, a C library with no reliable
# prebuilt wheel, which the UI does not use and which made this build fail.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# No build-essential needed: every dependency here ships a wheel.
COPY App/frontend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Only what the frontend actually needs, so an unrelated change does not
# invalidate this layer.
COPY streamlit_app.py ./streamlit_app.py
COPY App/frontend ./App/frontend

ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_ENABLECORS=false \
    STREAMLIT_BROWSER_GATHERUSAGESTATS=false

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4).status==200 else 1)"

CMD ["streamlit", "run", "streamlit_app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
