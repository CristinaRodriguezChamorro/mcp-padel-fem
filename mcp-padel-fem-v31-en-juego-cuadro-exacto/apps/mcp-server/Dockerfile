FROM python:3.12-slim

WORKDIR /app

# Install Chromium in a shared path, not in /root/.cache.
# Railway runs the app as appuser, so this avoids:
# "Executable doesn't exist at /home/appuser/.cache/ms-playwright/..."
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /ms-playwright \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY . .

RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "server.py"]
