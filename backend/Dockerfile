FROM python:3.12-slim

WORKDIR /app

# Install system deps (fonts for CJK chart rendering), then Python deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cache layer) — lock 文件保证构建可重现
COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
