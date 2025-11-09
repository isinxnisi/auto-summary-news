AITuberKit (local) → Dify
=========================

This folder runs AITuberKit locally and connects it to your Dify App.

Quick start
- Copy env: `cp .env.example .env` and set `DIFY_URL` and `DIFY_API_KEY` (from Dify → App → API Access).
- Start: `docker compose up -d` (this also launches VOICEVOX CPU TTS on :50021)
- Open: http://localhost:3000

Notes
- The container builds from the upstream repo (tegnike/aituber-kit) in dev mode to keep runtime env flexible.
- If you prefer your fork, build with `--build-arg AITUBER_REPO=...`.
- If you already run VOICEVOX on the host, stop the bundled `voicevox` service or set `VOICEVOX_API_URL=http://localhost:50021`.
- For GPU VOICEVOX, replace the image with `voicevox/voicevox_engine:nvidia` and add `--gpus all` via compose extensions as needed.

Troubleshooting
- 401/403 from Dify: make sure the App is Published and you’re using the App API Key.
- Connection URL: use the exact base URL shown in Dify’s API Access page.
- If port 3000 is occupied, change the host mapping in compose.

