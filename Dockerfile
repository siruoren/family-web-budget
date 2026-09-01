FROM python:3.12-slim

LABEL maintainer="family-budget-app"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

    
WORKDIR /app

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

# 创建数据目录
RUN mkdir -p instance uploads exports

EXPOSE 5050

# 默认使用 gunicorn 生产配置
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "4", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
