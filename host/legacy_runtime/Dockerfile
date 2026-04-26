# LEGACY: Docker image for msg_relay webview runtime.
# Replaced by direct Python execution via openrdk package (host/pi/comms/src/openrdk/).
# This file is kept for reference only — do not build or deploy.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src

# Old entrypoint — package renamed from msg_relay to openrdk.
CMD ["python", "-m", "msg_relay.run"]
