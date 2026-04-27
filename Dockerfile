FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# System deps:
# - tesseract-ocr: required for pytesseract
# - libgl1/libglib2.0-0: common runtime deps for opencv-python on Debian slim
# - curl/ca-certificates: useful for debugging + HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr \
      libgl1 \
      libglib2.0-0 \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install -r /app/requirements.txt

# Playwright browser install (Chromium).
RUN python -m playwright install --with-deps chromium

COPY . /app

CMD ["bash"]

