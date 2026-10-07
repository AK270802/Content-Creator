FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libmagic1 libgomp1 curl && apt-get clean && apt-get autoremove -y

WORKDIR /app

COPY requirements.txt .
# CPU-only torch first (avoids ~4 GB of CUDA/triton wheels); requirements.txt's
# torch==2.5.1 is then already satisfied by 2.5.1+cpu
RUN pip install --no-cache-dir --upgrade pip setuptools wheel  && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1  && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
