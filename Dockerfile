FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y gcc libc6-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY bridge ./bridge

ENV DATA_DIR=/data \
    COMPLETE_DIR=/downloads/complete \
    PORT=8765 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8765
CMD ["python", "-m", "bridge.main"]
