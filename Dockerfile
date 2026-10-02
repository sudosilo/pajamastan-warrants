FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY pajamastan-warrants-requirements.txt .
RUN pip install --no-cache-dir -r pajamastan-warrants-requirements.txt
COPY pajamastan_warrants.py pajamastan-warrants-justice.html ./
CMD uvicorn pajamastan_warrants:app --host 0.0.0.0 --port ${PORT:-8000}
