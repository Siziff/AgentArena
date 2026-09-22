"""Match configuration loading (TOML) and provider construction.

TOML is parsed with the standard-library `tomllib` (Python 3.11+), so no extra
dependency is needed. See config/match.example.toml for a full example.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .agent.prompts import DefensePolicy
from .agent.provider import (
    LLMProvider,
    MockProvider,
    OpenAICompatibleProvider,
    TextActionProvider,
)
from .core.treasure import DEFAULT_ALPHABET, DEFAULT_LENGTH
from .core.types import Side


@dataclass
class MatchSettings:
    fortify_seconds: int = 300
    max_match_seconds: int = 0
    rate_limit_per_minute: int = 10
    treasure_length: int = DEFAULT_LENGTH
    alphabet: str = DEFAULT_ALPHABET
    max_agent_iterations: int = 50


@dataclass
class LLMSettings:
    # provider: "mock" | "openai_compatible" (native tool-calling) |
    #           "openai_text" (text action protocol, for weak tool-calling models)
    provider: str = "mock"
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "agent-model"
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 1024

    def merged(self, override: dict) -> "LLMSettings":
        """Return a copy with the given per-side overrides applied."""
        out = LLMSettings(**self.__dict__)
        for key in ("provider", "base_url", "model", "api_key", "temperature", "max_tokens"):
            if key in override and override[key] is not None:
                setattr(out, key, override[key])
        return out


@dataclass
class IsolationSettings:
    backend: str = "local"  # "local" | "docker"
    docker_image: str = "agentarena-side:latest"
    docker_network: str = "agentarena"
    cpu: str = "1.0"
    memory: str = "1g"


@dataclass
class AppConfig:
    match: MatchSettings = field(default_factory=MatchSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    isolation: IsolationSettings = field(default_factory=IsolationSettings)
    policy: DefensePolicy = field(default_factory=DefensePolicy)
    # Optional per-side LLM overrides (fall back to `llm` when absent).
    llm_alpha: LLMSettings | None = None
    llm_bravo: LLMSettings | None = None

    def llm_for(self, side: Side) -> LLMSettings:
        override = self.llm_alpha if side is Side.ALPHA else self.llm_bravo
        return override if override is not None else self.llm


def _llm_from_table(tbl: dict) -> LLMSettings:
    return LLMSettings(
        provider=str(tbl.get("provider", "mock")),
        base_url=str(tbl.get("base_url", "http://127.0.0.1:8000/v1")),
        model=str(tbl.get("model", "agent-model")),
        api_key=str(tbl.get("api_key", "")),
        temperature=float(tbl.get("temperature", 0.2)),
        max_tokens=int(tbl.get("max_tokens", 1024)),
    )


def load_config(path: str | Path) -> AppConfig:
    """Load an AppConfig from a TOML file, applying defaults for missing keys.

    Supports optional per-side LLM overrides via [llm.alpha] and [llm.bravo]
    sub-tables; values not set there fall back to the shared [llm] table.
    """
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    m = data.get("match", {})
    llm = data.get("llm", {})
    iso = data.get("isolation", {})
    pol = data.get("defense_policy", {})

    shared = _llm_from_table({k: v for k, v in llm.items() if k not in ("alpha", "bravo")})
    llm_alpha = shared.merged(llm["alpha"]) if "alpha" in llm else None
    llm_bravo = shared.merged(llm["bravo"]) if "bravo" in llm else None

    return AppConfig(
        match=MatchSettings(
            fortify_seconds=int(m.get("fortify_seconds", 300)),
            max_match_seconds=int(m.get("max_match_seconds", 0)),
            rate_limit_per_minute=int(m.get("rate_limit_per_minute", 10)),
            treasure_length=int(m.get("treasure_length", DEFAULT_LENGTH)),
            alphabet=str(m.get("alphabet", DEFAULT_ALPHABET)),
            max_agent_iterations=int(m.get("max_agent_iterations", 50)),
        ),
        llm=shared,
        llm_alpha=llm_alpha,
        llm_bravo=llm_bravo,
        isolation=IsolationSettings(
            backend=str(iso.get("backend", "local")),
            docker_image=str(iso.get("docker_image", "agentarena-side:latest")),
            docker_network=str(iso.get("docker_network", "agentarena")),
            cpu=str(iso.get("cpu", "1.0")),
            memory=str(iso.get("memory", "1g")),
        ),
        policy=DefensePolicy(
            max_password_length=int(pol.get("max_password_length", 12)),
            max_password_alphabet=str(pol.get("max_password_alphabet", "lowercase letters and digits")),
            max_kdf_iterations=int(pol.get("max_kdf_iterations", 1000)),
            allowed_cipher_suites=str(
                pol.get("allowed_cipher_suites", "openssl AES-256-CBC, zip/7z with password, gpg --symmetric")
            ),
            max_layers=int(pol.get("max_layers", 3)),
        ),
    )


def build_provider_factory(app: AppConfig):
    """Return a (side, phase) -> LLMProvider factory based on the AppConfig.

    Resolves per-side LLM settings (alpha/bravo overrides, falling back to the
    shared [llm] table). Provider kinds:
      - "mock": scripted offline provider (tests/demos).
      - "openai_compatible": native OpenAI tool-calling.
      - "openai_text": OpenAI-compatible endpoint driven via the text action
        protocol (for models with weak/no native tool-calling).
    """

    def factory(side: Side, phase: str) -> LLMProvider:  # noqa: ARG001 - phase not needed yet
        llm = app.llm_for(side)
        if llm.provider in ("openai_compatible", "openai_text"):
            base = OpenAICompatibleProvider(
                base_url=llm.base_url,
                model=llm.model,
                api_key=llm.api_key,
                temperature=llm.temperature,
                max_tokens=llm.max_tokens,
            )
            return base if llm.provider == "openai_compatible" else TextActionProvider(base)
        return MockProvider()

    return factory
