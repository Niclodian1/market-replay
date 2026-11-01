FROM python:3.12-slim

WORKDIR /app

# Install system deps (fonts for CJK chart rendering), then Python deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        && rm -rf /var/lib/apt/lists/*

# 构建上下文是仓库根：先装依赖（缓存层），再拷代码
COPY apps/platform-api/requirements.txt apps/platform-api/requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY apps/platform-api/ ./
# 统一指标层是独立包；与 apps/platform-api/app/indicator_core（兼容 shim）一起工作
COPY packages/ ./packages/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
