FROM python:3.11-slim

WORKDIR /app

# stdlib-only — no pip install. Runtime receipts and ledgers are deliberately
# excluded from the image; production state lives only on the mounted volume.
COPY server/ /app/server/
COPY web/ /app/web/
COPY scripts/ /app/scripts/

# Non-root user — defense in depth for any future RCE class bug.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 orpho \
 && chown -R orpho:orpho /app
USER orpho

ENV HOST=0.0.0.0
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV ORPHO_DATA_DIR=/app/data

EXPOSE 8080

CMD ["bash", "scripts/init_volume.sh"]
