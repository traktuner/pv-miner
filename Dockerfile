FROM alpine:3.23

ENV CONFIG_PATH=/data/config.json \
    WEB_PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache python3 py3-flask py3-requests \
    && addgroup -S pv-miner \
    && adduser -S -G pv-miner pv-miner \
    && mkdir -p /data \
    && chown -R pv-miner:pv-miner /data /app

COPY --chown=pv-miner:pv-miner pv_miner.py .

USER pv-miner
VOLUME ["/data"]
EXPOSE 8080

CMD ["python", "/app/pv_miner.py"]
