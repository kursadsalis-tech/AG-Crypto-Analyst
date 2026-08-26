FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AGCA_PROJECT_DIR=/app \
    AGCA_STATE_DIR=/var/lib/agca AGCA_EXECUTION_MODE=PAPER_ONLY
WORKDIR /app
COPY requirements-runtime.txt requirements-forecast-v3.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt -r requirements-forecast-v3.txt
COPY . .
RUN mkdir -p /var/lib/agca
VOLUME ["/var/lib/agca"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import json,time; p=json.load(open('/var/lib/agca/worker-health.json')); assert p['status']=='HEALTHY' and time.time()-__import__('datetime').datetime.fromisoformat(p['at']).timestamp()<600"
CMD ["python", "-m", "runtime.cloud_worker"]
