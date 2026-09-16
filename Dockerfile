FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY prompts ./prompts
COPY rules.yaml ./
COPY sample_data ./sample_data

# Dry run by default: the image cannot page anyone unless ORCH_LIVE is set at run time.
CMD ["python", "-m", "orchestrator.cli", "run", "sample_data"]
