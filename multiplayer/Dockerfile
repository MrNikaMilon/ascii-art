FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py game.py ./
COPY static ./static
USER nobody
EXPOSE 8080
# Rooms live in memory, so run exactly one worker process.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --ws-max-size 4096 --proxy-headers --forwarded-allow-ips '*'"]
