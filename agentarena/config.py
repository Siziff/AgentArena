"""Match configuration loading (TOML) and provider construction.

TOML is parsed with the standard-library `tomllib` (Python 3.11+), so no extra
dependency is needed. See config/match.example.toml for a full example.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .agent.prompts import DefensePolicy
from .agent.provider import LLMProvider, MockProvider, OpenAICompatibleProvider
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
    provider: str = "mock"  # "mock" | "openai_compatible"
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "agent-model"
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 1024


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


def load_config(path: str | Path) -> AppConfig:
    """Load an AppConfig from a TOML file, applying defaults for missing keys."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    m = data.get("match", {})
    llm = data.get("llm", {})
    iso = data.get("isolation", {})
    pol = data.get("defense_policy", {})
    return AppConfig(
        match=MatchSettings(
            fortify_seconds=int(m.get("fortify_seconds", 300)),
            max_match_seconds=int(m.get("max_match_seconds", 0)),
            rate_limit_per_minute=int(m.get("rate_limit_per_minute", 10)),
            treasure_length=int(m.get("treasure_length", DEFAULT_LENGTH)),
            alphabet=str(m.get("alphabet", DEFAULT_ALPHABET)),
            max_agent_iterations=int(m.get("max_agent_iterations", 50)),
        ),
        llm=LLMSettings(
            provider=str(llm.get("provider", "mock")),
            base_url=str(llm.get("base_url", "http://127.0.0.1:8000/v1")),
            model=str(llm.get("model", "agent-model")),
            api_key=str(llm.get("api_key", "")),
            temperature=float(llm.get("temperature", 0.2)),
            max_tokens=int(llm.get("max_tokens", 1024)),
        ),
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


def build_provider_factory(llm: LLMSettings):
    """Return a (side, phase) -> LLMProvider factory based on LLM settings.

    Both sides receive identically-configured providers to keep the match fair.
    """

    def factory(side: Side, phase: str) -> LLMProvider:  # noqa: ARG001 - same for all sides/phases
        if llm.provider == "openai_compatible":
            return OpenAICompatibleProvider(
                base_url=llm.base_url,
                model=llm.model,
                api_key=llm.api_key,
                temperature=llm.temperature,
                max_tokens=llm.max_tokens,
            )
        return MockProvider()

    return factory
