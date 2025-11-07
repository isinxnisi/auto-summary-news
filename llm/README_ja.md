# ローカルLLM運用ガイド（Ollama + GPU + Cloudflare）

0. 目次
   1. 概要
   2. 前提条件（インストール確認）
   3. ディレクトリ構成と永続化
   4. 起動・停止（Compose）
   5. GPU利用の確認方法
   6. モデル導入（Qwen/BGE）
   7. ELYZA 8B 導入手順（Modelfile）
   8. リクエスト例（PowerShell/UTF-8）
   9. Cloudflare Tunnel と保護
  10. n8n 連携のポイント
  11. 推奨チューニング（8GB VRAM想定）
  12. トラブルシューティング
  13. 付録：コマンド早見表

1. 概要
- ローカルPCで Ollama を GPU 利用で起動し、必要に応じて Cloudflare Tunnel で外部公開します。
- n8n/Dify から呼び出す想定。大きなモデルファイルは Git 管理から除外しています。

2. 前提条件（インストール確認）
- NVIDIA ドライバ + NVIDIA Container Toolkit を導入。
- 確認（正常なら GPU 情報が表示されます）:
  - `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`

3. ディレクトリ構成と永続化
- データ永続化: `n8n-auto/llm/ollama-data` → コンテナ `/root/.ollama`
- 大型モデル: `n8n-auto/llm/models` → コンテナ `/models`（読み取り専用）
- Compose ファイル: `n8n-auto/llm/docker-compose.yml`

4. 起動・停止（Compose）
- 初回のみネットワーク作成: `docker network create edge`
- 起動: `docker compose -f n8n-auto/llm/docker-compose.yml up -d`
- 停止: `docker compose -f n8n-auto/llm/docker-compose.yml down`
- 動作確認: `curl http://localhost:11434/api/tags`

5. GPU利用の確認方法（Windows PowerShell）
- 監視: `nvidia-smi -l 1`（1秒更新）
- 生成リクエスト送信中に GPU-Util / メモリ使用量が増えれば GPU が使われています。

6. モデル導入（Qwen/BGE）
- Qwen 7B（日本語も実用・推奨）: `docker exec -it ollama ollama pull qwen2.5:7b-instruct`
- 多言語埋め込み bge-m3（RAG 用）: `docker exec -it ollama ollama pull bge-m3`

7. ELYZA 8B 導入手順（Modelfile）
- GGUF を保存（再開ダウンロード例）:
  - `curl.exe -L -C - "https://huggingface.co/elyza/Llama-3-ELYZA-JP-8B-GGUF/resolve/main/Llama-3-ELYZA-JP-8B-q4_k_m.gguf?download=true" -o "n8n-auto/llm/models/llama3-elyza-jp-8b/Llama-3-ELYZA-JP-8B-q4_k_m.gguf"`
- Modelfile（最小・LFで保持）: `n8n-auto/llm/models/llama3-elyza-jp-8b/Modelfile`
  - `FROM /models/llama3-elyza-jp-8b/Llama-3-ELYZA-JP-8B-q4_k_m.gguf`
  - `PARAMETER num_ctx 1024`
  - `PARAMETER temperature 0.7`
- 作成（CRLF→LF 正規化を含む）:
  - `docker cp n8n-auto/llm/models/llama3-elyza-jp-8b/Modelfile ollama:/tmp/ELYZA.Modelfile`
  - `docker exec -it ollama sh -lc 'sed -i "s/\r$//" /tmp/ELYZA.Modelfile'`
  - `docker exec -it ollama ollama create elyza-jp-8b -f /tmp/ELYZA.Modelfile`
- 登録確認: `curl -s http://localhost:11434/api/tags`
- 参考容量: q4_k_m ≈ 4.8–5.2GB（作成後は `ollama-data` にも取り込まれるため一時的に倍近い空きが必要）

8. リクエスト例（PowerShell/UTF-8）
- generate:
  - `$b = @{ model="elyza-jp-8b"; prompt="日本語で自己紹介。ですます調で1段落。"; stream=$false; options=@{ num_ctx=1024; temperature=0.6 } } | ConvertTo-Json`
  - `Invoke-RestMethod -Uri "http://localhost:11434/api/generate" -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($b))`
- chat（Qwen 7B 例）:
  - `$chat = @{ model="qwen2.5:7b-instruct"; stream=$false; options=@{ num_ctx=2048; temperature=0.2 }; messages=@(@{role="system";content="常に日本語で丁寧に回答。英語や中国語は使わない。"}; @{role="user";content="自己紹介を1段落、です・ます調で。"}) } | ConvertTo-Json -Depth 8`
  - `Invoke-RestMethod -Uri "http://localhost:11434/api/chat" -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($chat))`

9. Cloudflare Tunnel と保護
- `.env` に `CF_TUNNEL_TOKEN_OLLAMA` を設定し、`cloudflared-ollama` を起動。
- Zero Trust Access のサービス・トークンで保護（必要に応じて）:
  - `CF-Access-Client-Id: <ID>` / `CF-Access-Client-Secret: <SECRET>` をヘッダ付与。

10. n8n 連携のポイント
- HTTP Request ノード（外部接続）
  - URL: `https://<あなたのFQDN>/api/chat`
  - Headers: `Content-Type: application/json; charset=utf-8`（＋CFヘッダ）
  - Body: §8 の chat 形式。
- 内部接続（同一ホストの Docker ネットワーク）では `http://ollama:11434` で認証不要。

11. 推奨チューニング（8GB VRAM想定）
- `num_ctx`: 1024 から開始（必要に応じ 1536/2048）
- `temperature`: 0.2–0.7（安定性/創造性のバランス）
- 同時実行: `OLLAMA_NUM_PARALLEL=1` から
- ウォーム: `OLLAMA_KEEP_ALIVE=30m`（初回後の再ロード短縮）

12. トラブルシューティング
- ネットワーク `edge` が無い: `docker network create edge`
- GPUが使われない: ドライバ/Toolkit・`runtime: nvidia` を確認し、推論中に `nvidia-smi -l 1` で利用率を確認。
- Modelfile エラー: パスの完全一致（大小文字含む）、`sed -i 's/\r$//'` で LF 化、`temperature` 綴り。
- メモリ不足: `num_ctx` を下げる、軽量モデルへ切替、スワップ追加。
- 文字化け: `Content-Type: ...; charset=utf-8` を付与し、UTF-8 バイトで送信。

13. 付録：コマンド早見表
- 起動: `docker compose -f n8n-auto/llm/docker-compose.yml up -d`
- 停止: `docker compose -f n8n-auto/llm/docker-compose.yml down`
- タグ一覧: `curl http://localhost:11434/api/tags`
- Qwen 7B: `docker exec -it ollama ollama pull qwen2.5:7b-instruct`
- BGE: `docker exec -it ollama ollama pull bge-m3`
- ELYZA 8B create: `docker exec -it ollama ollama create elyza-jp-8b -f /tmp/ELYZA.Modelfile`

