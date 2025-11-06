# ベース：軽量でビルド事故が少ない slim 系
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=off \
    TZ=Asia/Tokyo

# ランタイム最低限（wget: healthcheck用/調査用）
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates tzdata wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存を先に入れてレイヤー安定化
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /app/requirements.txt

# Sudachi の辞書は pip で同梱済み（sudachidict-core）
# 追加設定は不要だが、固定したい場合は環境変数を宣言してもよい:
# ENV SUDACHI_DICT=/usr/local/lib/python3.11/site-packages/sudachidict_core/resources

# アプリ本体
COPY app.py /app/app.py
COPY routes /app/routes

EXPOSE 8080

# 低リソース前提でワーカー1本から開始（必要に応じて調整）
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
