FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

USER appuser

CMD ["/app/start.sh"]

