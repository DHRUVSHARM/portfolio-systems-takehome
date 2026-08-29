FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY deploy/tools-requirements.txt /app/deploy/tools-requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/deploy/tools-requirements.txt

COPY . /app
