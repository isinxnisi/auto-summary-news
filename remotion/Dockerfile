FROM node:20-bullseye

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_ENV=development \
    PUPPETEER_SKIP_DOWNLOAD=1 \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    CHROME_PATH=/usr/bin/chromium \
    TZ=Asia/Tokyo

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ffmpeg \
      chromium \
      ca-certificates \
      fonts-noto \
      fonts-noto-cjk \
      fonts-noto-color-emoji \
      libxss1 \
      libnss3 \
      libatk-bridge2.0-0 \
      libgtk-3-0 \
      libasound2 \
      libgbm1 \
      wget \
      git \
      dumb-init && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app && chown -R node:node /app
WORKDIR /app

# install startup script as root, then drop privileges
COPY start.sh /usr/local/bin/start-remotion
RUN chmod +x /usr/local/bin/start-remotion

USER node

EXPOSE 3000

ENTRYPOINT ["dumb-init", "--"]
CMD ["start-remotion"]
