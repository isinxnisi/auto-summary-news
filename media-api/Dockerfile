FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=off \
    TZ=Asia/Tokyo

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates tzdata ffmpeg wget curl nodejs npm docker.io \
      libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
      libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libpango-1.0-0 libasound2 \
 && rm -rf /var/lib/apt/lists/*

ARG DOCKER_CLI_VERSION=24.0.9
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz \
  | tar -xz --strip-components=1 -C /usr/local/bin docker/docker

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /app/requirements.txt

COPY app /app/app

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
