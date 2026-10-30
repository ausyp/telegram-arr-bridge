FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y gcc libc6-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY bridge ./bridge
COPY docker-entrypoint.sh /usr/local/bin/

ENV DATA_DIR=/data \
    COMPLETE_DIR=/downloads/complete \
    PORT=8765 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8765
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "bridge.main"]
