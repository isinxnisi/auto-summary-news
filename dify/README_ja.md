# Dify（VPS）セットアップ手順（Ollama直結 + Geminiフォールバック）

1. 前提
- このスタックはVPS上で起動します。`edge` ネットワークに参加し、Ollamaは別ホスト（ローカルPC）で稼働中。
- Cloudflare Access は `ollama.<domain>` を作成し、VPS固定IPだけ Bypass（ヘッダ不要）にしておきます。

2. 起動
- `.env` を作成:
  - `cp n8n-auto/dify/.env.example n8n-auto/dify/.env`
  - 各値（特にパスワード/SECRET_KEY）を変更
- `docker network create edge`（未作成の場合）
- `docker compose -f n8n-auto/dify/docker-compose.yml --env-file n8n-auto/dify/.env up -d`
- UI: `http://<VPS_IP>:3002`、API: `http://<VPS_IP>:5001`

3. Dify → Ollama 接続
- Dify 管理画面 → Providers → Add “Ollama”
  - Base URL: `https://ollama.<domain>`（VPSはBypassで認証不要）
  - API Key: 空でOK
  - Models（登録例）:
    - `gen_qwen7b` → `qwen2.5:7b-instruct`（既定に）
    - `jp_elyza8b` → `elyza-jp-8b`（和文仕上げ用途）
  - Embeddings: `bge-m3`

4. Gemini 追加（フォールバック）
- Providers → Add “Google Gemini” → API Key を入力。
- Workflow で「Ollama ノード失敗時 → Gemini ノード」に分岐。

5. 運用メモ（8GB VRAM）
- Qwen 7B: `num_ctx` 2048 目安。汎用・英語/事務用途に安定。
- ELYZA 8B: `num_ctx` 1024 から。日本語の文体/整形用（仕上げ）。
- Embedding: `bge-m3` を共通で使用（RAG向け）。

6. トラブルシュート
- `ollama.<domain>` がヘッダ不要で 200 になるか VPS から `curl -i https://.../api/tags` で確認。
- Web が 3002, API が 5001 で LISTEN しているか `docker compose ps` で確認。
- DB や MinIO のパスワード不一致 → `.env` の値を見直し。

7. 注意
- 本composeは最小構成の雛形です。公式の compose（langgenius/dify）に合わせて拡張可能です。
- 本番用途ではバックアップ/監視/HTTPS終端（プロキシ）などを用意してください。

