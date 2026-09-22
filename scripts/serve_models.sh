#!/usr/bin/env bash
# Launch two local vLLM OpenAI-compatible servers (detached), one per GPU.
# Selection (GPU indices / model ids) can come from scripts/models.env (written
# by start.py) or from environment variables; sensible defaults are provided.
#   Alpha: Qwen/Qwen2.5-7B-Instruct  -> http://127.0.0.1:9001
#   Bravo: Qwen/Qwen2.5-3B-Instruct  -> http://127.0.0.1:9002
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"

# Optional selections written by start.py.
[ -f "$HERE/models.env" ] && . "$HERE/models.env"

GPU_ALPHA=${GPU_ALPHA:-6}
GPU_BRAVO=${GPU_BRAVO:-7}
PORT_ALPHA=${PORT_ALPHA:-9001}
PORT_BRAVO=${PORT_BRAVO:-9002}
MODEL_ALPHA=${MODEL_ALPHA:-Qwen/Qwen2.5-7B-Instruct}
MODEL_BRAVO=${MODEL_BRAVO:-Qwen/Qwen2.5-3B-Instruct}
MAXLEN=${MAXLEN:-16384}

setsid "$HERE/vllm_serve.sh" "$GPU_ALPHA" "$PORT_ALPHA" "$MODEL_ALPHA" "$MAXLEN" >/tmp/vllm_alpha.log 2>&1 &
echo "launched $MODEL_ALPHA on GPU $GPU_ALPHA -> http://127.0.0.1:$PORT_ALPHA (log: /tmp/vllm_alpha.log)"
setsid "$HERE/vllm_serve.sh" "$GPU_BRAVO" "$PORT_BRAVO" "$MODEL_BRAVO" "$MAXLEN" >/tmp/vllm_bravo.log 2>&1 &
echo "launched $MODEL_BRAVO on GPU $GPU_BRAVO -> http://127.0.0.1:$PORT_BRAVO (log: /tmp/vllm_bravo.log)"
echo "poll readiness:  curl -sf http://127.0.0.1:$PORT_ALPHA/health ; curl -sf http://127.0.0.1:$PORT_BRAVO/health"
