#!/usr/bin/env bash
# Wrapper that sets the environment and execs a vLLM server in the FOREGROUND.
# Run it detached with setsid, e.g.:
#   setsid scripts/vllm_serve.sh 6 9001 Qwen/Qwen2.5-7B-Instruct >/tmp/vllm_alpha.log 2>&1 &
# Args: <gpu_index> <port> <hf_model_id> [max_model_len]
set -u
GPU=$1; PORT=$2; MODEL=$3; MAXLEN=${4:-16384}

# Self-consistent venv (python3.12 + vllm 0.30 + torch 2.13 + CUDA 13), created
# by start.py. No LD_LIBRARY_PATH hacks needed — vllm wheels bundle their libs.
VENV=/workspace-SR008.fs2/mikheev-kandy/AgentArena/.venv
export HF_HOME=/workspace-SR008.fs2/mikheev-kandy/hf_cache
export CUDA_VISIBLE_DEVICES="$GPU"
export VLLM_LOGGING_LEVEL=WARNING

exec "$VENV/bin/vllm" serve "$MODEL" --port "$PORT" \
  --gpu-memory-utilization 0.85 --max-model-len "$MAXLEN"
