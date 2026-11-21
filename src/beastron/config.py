"""Model profiles loaded from models.toml.

A profile is everything needed to talk to one model on one server. The agent
only ever sees a profile through a ModelClient, so swapping models is purely a
configuration change.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KNOWN_QUIRKS = {
    # Send the model's reasoning back in the assistant message history.
    # gpt-oss needs this or its tool use degrades.
    "send_reasoning_back",
    # Recover a single well-formed function block emitted as raw assistant text.
    "recover_function_markup",
}

CONFIG_LOCATIONS = (
    Path("models.toml"),
    Path("~/.config/beastron/models.toml").expanduser(),
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelProfile:
    name: str
    base_url: str
    model: str
    context: int = 32768
    max_steps: int = 25
    api_key_env: str | None = None
    temperature: float | None = None
    # How hard the model thinks. Sent to the server as `reasoning_effort`.
    # "none" turns thinking off on Ollama's Qwen3; "low"/"medium"/"high" where supported.
    effort: str | None = None
    quirks: frozenset[str] = frozenset()
    extra_body: dict[str, Any] = field(default_factory=dict)

    def api_key(self) -> str:
        if not self.api_key_env:
            return "local"  # local servers ignore the key, but the SDK requires one
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ConfigError(
                f"Model '{self.name}' needs an API key: set the {self.api_key_env} environment variable."
            )
        return key


@dataclass(frozen=True)
class Config:
    path: Path
    default: str
    profiles: dict[str, ModelProfile]

    def get(self, name: str | None) -> ModelProfile:
        name = name or self.default
        if name not in self.profiles:
            available = ", ".join(sorted(self.profiles)) or "(none)"
            raise ConfigError(f"Unknown model '{name}'. Available in {self.path}: {available}")
        return self.profiles[name]


def find_config(explicit: Path | None = None) -> Path:
    if explicit:
        if not explicit.is_file():
            raise ConfigError(f"Config file not found: {explicit}")
        return explicit
    env = os.environ.get("BEASTRON_CONFIG")
    candidates = [Path(env)] if env else []
    candidates += list(CONFIG_LOCATIONS)
    for path in candidates:
        if path.is_file():
            return path
    tried = ", ".join(str(p) for p in candidates)
    raise ConfigError(f"No models.toml found (looked in: {tried}).")


def load_config(explicit: Path | None = None) -> Config:
    path = find_config(explicit)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from e

    raw_models = data.get("models")
    if not isinstance(raw_models, dict) or not raw_models:
        raise ConfigError(f"{path}: needs at least one [models.<name>] section.")

    profiles = {name: _parse_profile(path, name, raw) for name, raw in raw_models.items()}
    default = data.get("default") or next(iter(profiles))
    if default not in profiles:
        raise ConfigError(f"{path}: default model '{default}' has no [models.{default}] section.")
    return Config(path=path, default=default, profiles=profiles)


def _parse_profile(path: Path, name: str, raw: Any) -> ModelProfile:
    where = f"{path} [models.{name}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a table.")
    allowed = {
        "base_url", "model", "context", "api_key_env", "temperature",
        "effort", "quirks", "extra_body", "max_steps",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}. Allowed: {sorted(allowed)}")
    for key in ("base_url", "model"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise ConfigError(f"{where}: '{key}' is required.")
    max_steps = raw.get("max_steps", 25)
    if type(max_steps) is not int or max_steps < 1:
        raise ConfigError(f"{where}: 'max_steps' must be a positive integer.")
    quirks = frozenset(raw.get("quirks", []))
    bad = quirks - KNOWN_QUIRKS
    if bad:
        raise ConfigError(f"{where}: unknown quirks {sorted(bad)}. Known: {sorted(KNOWN_QUIRKS)}")
    return ModelProfile(
        name=name,
        base_url=raw["base_url"],
        model=raw["model"],
        context=int(raw.get("context", 32768)),
        max_steps=max_steps,
        api_key_env=raw.get("api_key_env"),
        temperature=raw.get("temperature"),
        effort=raw.get("effort"),
        quirks=quirks,
        extra_body=dict(raw.get("extra_body", {})),
    )
