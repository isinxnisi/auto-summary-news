FROM node:20-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone AITuberKit (shallow). You can change repo via build arg if you fork.
ARG AITUBER_REPO=https://github.com/tegnike/aituber-kit.git
RUN git clone --depth 1 ${AITUBER_REPO} /app

# Install deps
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

ENV HOST=0.0.0.0
ENV PORT=3000
ENV NEXT_TELEMETRY_DISABLED=1

# Start script detects app root, injects /api/difyChat, installs deps, and runs dev
EXPOSE 3000

CMD ["npm","run","dev","--","-H","0.0.0.0","-p","3000"]

