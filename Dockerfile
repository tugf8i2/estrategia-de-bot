FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIGNAL_DATA_DIR=/tmp/coffee-fly-data \
    DASHBOARD_HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /tmp/coffee-fly-data

CMD ["python", "cloud_app.py"]
