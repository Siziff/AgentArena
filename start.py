#!/usr/bin/env python3
"""
AgentArena — friendly setup wizard.

Guides a non-programmer from a fresh clone to a ready-to-run battle:

  1. installs Python dependencies (into a local .venv),
  2. asks which mode you want:
       * Local — run models on your own GPUs (you pick how many),
       * API   — use a hosted OpenAI-compatible model API (you paste a key),
  3. writes a battle config and prints the exact command to start.

It uses only the Python standard library, so it runs before anything is
installed.  Run it with:  python start.py   (or:  python3 start.py)
"""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import venv

REPO = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(REPO, ".venv")
CONFIG_OUT = os.path.join(REPO, "config", "battle.generated.toml")
MODELS_ENV = os.path.join(REPO, "scripts", "models.env")

# ---- pretty output ---------------------------------------------------------

def _c(code: str, s: str) -> str:
    return s if not sys.stdout.isatty() else f"\033[{code}m{s}\033[0m"

def ok(s: str) -> None:    print(_c("32", "  ✓ ") + s)
def info(s: str) -> None:  print("    " + s)
def warn(s: str) -> None:  print(_c("33", "  ! ") + s)
def err(s: str) -> None:   print(_c("31", "  ✗ ") + s)
def step(s: str) -> None:  print("\n" + _c("1;36", "== " + s + " =="))

# ---- input helpers ---------------------------------------------------------

def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return val or (default or "")

def ask_yes_no(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    val = ask(f"{prompt} ({d})").lower()
    if not val:
        return default
    return val.startswith("y")

def ask_choice(prompt: str, options: list[tuple[str, str]]) -> str:
    """options: list of (key, description). Returns the chosen key."""
    print(prompt)
    for i, (_, desc) in enumerate(options, 1):
        print(f"  {i}) {desc}")
    while True:
        val = ask("Choose", "1")
        if val.isdigit() and 1 <= int(val) <= len(options):
            return options[int(val) - 1][0]
        # allow typing the key directly
        for key, _ in options:
            if val == key:
                return key
        warn("Please enter a number from the list.")

def ask_secret(prompt: str) -> str:
    try:
        return getpass.getpass(f"{prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)

# ---- system helpers --------------------------------------------------------

def venv_python() -> str:
    return os.path.join(VENV, "bin", "python")

def detect_gpus() -> list[dict]:
    """Return a list of GPUs via nvidia-smi, or [] if none."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader"],
            text=True, timeout=15,
        )
    except Exception:
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            gpus.append({"index": parts[0], "name": parts[1], "memory": parts[2]})
    return gpus

def write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)

# ---- dependency install ----------------------------------------------------

def ensure_dependencies() -> str:
    """Create .venv and install runtime deps. Returns the venv python path."""
    py = venv_python()
    if not os.path.exists(py):
        step("Creating a virtual environment (.venv)")
        venv.create(VENV, with_pip=True)
        ok("virtual environment created")
    else:
        ok("virtual environment already exists")
    step("Installing dependencies (fastapi, uvicorn, pydantic)")
    subprocess.check_call([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "-q", "fastapi", "uvicorn", "pydantic"])
    ok("dependencies installed")
    return py

# ---- config generation -----------------------------------------------------

LOCAL_PAIRS = [
    ("qwen7b_vs_3b", "Qwen2.5-7B vs Qwen2.5-3B   (recommended, ~21 GB download)"),
    ("qwen3b_vs_15b", "Qwen2.5-3B vs Qwen2.5-1.5B (lighter, ~9 GB download)"),
    ("custom", "Choose the two models myself"),
]
PAIR_MODELS = {
    "qwen7b_vs_3b": ("Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen2.5-3B-Instruct"),
    "qwen3b_vs_15b": ("Qwen/Qwen2.5-3B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct"),
}

def config_toml(
    *, fortify: int, provider: str, api_key: str,
    alpha_url: str, alpha_model: str, bravo_url: str, bravo_model: str,
) -> str:
    return f"""# Generated by start.py — edit freely.
[match]
fortify_seconds = {fortify}
max_match_seconds = 1800
rate_limit_per_minute = 10
max_agent_iterations = 40
# treasures hidden by EACH side; the winner is the first to steal ALL of them
treasures_per_side = 1

[llm]
provider = "{provider}"
api_key = "{api_key}"
temperature = 0.3
max_tokens = 1024

[llm.alpha]
base_url = "{alpha_url}"
model = "{alpha_model}"

[llm.bravo]
base_url = "{bravo_url}"
model = "{bravo_model}"

[isolation]
backend = "local"
"""

def setup_local() -> dict:
    step("Local mode — run models on your GPUs")
    gpus = detect_gpus()
    if gpus:
        ok(f"found {len(gpus)} GPU(s):")
        for g in gpus:
            info(f"GPU {g['index']}: {g['name']} ({g['memory']})")
    else:
        warn("no NVIDIA GPUs detected (nvidia-smi not found).")

    n = ask("How many GPUs should the battle use? (1 model per GPU is best)", "2")
    info(f"will use {n} GPU(s).")
    gpu_alpha = ask("  GPU index for Alpha", "0")
    gpu_bravo = ask("  GPU index for Bravo", gpu_alpha if n == "1" else "1")

    pair = ask_choice("Which two models should fight?", LOCAL_PAIRS)
    if pair == "custom":
        alpha_model = ask("  Alpha model (HF id, e.g. Qwen/Qwen2.5-7B-Instruct)", "Qwen/Qwen2.5-7B-Instruct")
        bravo_model = ask("  Bravo model (HF id)", "Qwen/Qwen2.5-3B-Instruct")
    else:
        alpha_model, bravo_model = PAIR_MODELS[pair]
    info(f"Alpha = {alpha_model}")
    info(f"Bravo = {bravo_model}")

    cfg = config_toml(
        fortify=60, provider="openai_text", api_key="EMPTY",
        alpha_url="http://127.0.0.1:9001/v1", alpha_model=alpha_model,
        bravo_url="http://127.0.0.1:9002/v1", bravo_model=bravo_model,
    )
    write_file(CONFIG_OUT, cfg)
    ok(f"wrote {os.path.relpath(CONFIG_OUT, REPO)}")

    # record the model/gpu selection for scripts/serve_models.sh
    env = (
        f"GPU_ALPHA={gpu_alpha}\nGPU_BRAVO={gpu_bravo}\n"
        f"PORT_ALPHA=9001\nPORT_BRAVO=9002\n"
        f"MODEL_ALPHA={alpha_model}\nMODEL_BRAVO={bravo_model}\n"
    )
    write_file(MODELS_ENV, env)
    ok(f"wrote {os.path.relpath(MODELS_ENV, REPO)}")

    if ask_yes_no("Download the model weights now? (large download)", True):
        step("Downloading models (this can take a while)")
        py = venv_python()
        subprocess.check_call([py, "-m", "pip", "install", "-q", "huggingface_hub"])
        code = (
            "import os;os.environ.setdefault('HF_HOME', os.path.expanduser('~/.cache/huggingface'));"
            "from huggingface_hub import snapshot_download;"
            f"print(snapshot_download('{alpha_model}'));"
            f"print(snapshot_download('{bravo_model}'));"
        )
        try:
            subprocess.check_call([py, "-c", code])
            ok("models downloaded")
        except subprocess.CalledProcessError:
            warn("download failed — you can retry later; the battle server also downloads on first run.")

    return {"mode": "local"}

def setup_api() -> dict:
    step("API mode — use a hosted OpenAI-compatible model API")
    base = ask("API base URL", "https://api.openai.com/v1")
    key = ask_secret("API key (input hidden)")
    if not key:
        warn("no key entered — you can add it to the config later (api_key).")
    provider = ask_choice(
        "How should the agents talk to the model?",
        [
            ("openai_text", "Text action protocol (robust — works even if the model has weak tool-calling)"),
            ("openai_compatible", "Native tool-calling (if your model supports OpenAI function calling)"),
        ],
    )
    alpha_model = ask("Model for Alpha (e.g. gpt-4o-mini)", "gpt-4o-mini")
    same = ask_yes_no("Use the same model for Bravo?", True)
    bravo_model = alpha_model if same else ask("Model for Bravo", "gpt-4o-mini")

    cfg = config_toml(
        fortify=60, provider=provider, api_key=key or "PASTE_YOUR_KEY_HERE",
        alpha_url=base, alpha_model=alpha_model,
        bravo_url=base, bravo_model=bravo_model,
    )
    write_file(CONFIG_OUT, cfg)
    ok(f"wrote {os.path.relpath(CONFIG_OUT, REPO)}")
    if not key:
        warn(f"remember to put your key into {os.path.relpath(CONFIG_OUT, REPO)} (api_key).")
    return {"mode": "api"}

# ---- main ------------------------------------------------------------------

def main() -> None:
    print(_c("1;35", "\n  ⚔  AgentArena — setup wizard"))
    info("from a fresh clone to a running battle, in a few questions.\n")

    if sys.version_info < (3, 11):
        err(f"Python 3.11+ is required (you have {sys.version.split()[0]}).")
        sys.exit(1)
    ok(f"Python {sys.version.split()[0]}")

    # Step 1: dependencies
    if ask_yes_no("Install Python dependencies now? (recommended)", True):
        py = ensure_dependencies()
    else:
        py = venv_python() if os.path.exists(venv_python()) else sys.executable
        warn("skipped dependency install — the battle server needs fastapi/uvicorn/pydantic.")

    # Step 2: mode
    step("Choose how to run the models")
    mode = ask_choice(
        "Pick a mode:",
        [
            ("local", "Local — run models on my own GPU(s)"),
            ("api", "API — use a hosted model API with an API key"),
        ],
    )
    result = setup_local() if mode == "local" else setup_api()

    # Step 3: done
    step("All set!")
    pycmd = py if os.path.exists(py) else "python3"
    if result["mode"] == "local":
        info("1) Start the two local model servers (one per GPU):")
        info(f"   bash scripts/serve_models.sh")
        info("2) In another terminal, start the battle UI:")
        info(f"   {pycmd} -m agentarena.cli battle --config {os.path.relpath(CONFIG_OUT, REPO)}")
    else:
        info("Start the battle UI:")
        info(f"   {pycmd} -m agentarena.cli battle --config {os.path.relpath(CONFIG_OUT, REPO)}")
    info("3) Open http://localhost:8001 and click “Quick test battle”.")
    print()
    ok("setup complete — have fun! ⚔")


if __name__ == "__main__":
    main()
