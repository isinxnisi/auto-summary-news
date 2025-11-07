Usage (create custom model in Ollama)

- Linux/macOS:
  docker exec -i ollama ollama create llama3-elyza-jp-8b -f - < n8n-auto/llm/models/llama3-elyza-jp-8b/Modelfile

- Windows PowerShell:
  Get-Content -Raw n8n-auto/llm/models/llama3-elyza-jp-8b/Modelfile | docker exec -i ollama ollama create llama3-elyza-jp-8b -f -

Then test:
  curl -H "Content-Type: application/json" http://localhost:11434/api/generate -d '{"model":"llama3-elyza-jp-8b","prompt":"日本語で自己紹介して","stream":false,"options":{"num_ctx":2048}}'

Notes
- If the Modelfile URL 404s, open the model page on Hugging Face and use the latest GGUF file (e.g. Q4_K_M/Q5_K_M).
- Ensure the Ollama container has GPU: check with `nvidia-smi` while generating.

