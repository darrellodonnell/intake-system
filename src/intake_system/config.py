from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from intake_system.knowledge import KNOWLEDGE_BASE_KEYS


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DestinationConfig:
    key: str
    label: str
    path: Path
    private: bool


@dataclass(frozen=True)
class DatabaseConfig:
    dsn_env: str
    schema: str

    @property
    def dsn(self) -> str:
        value = os.environ.get(self.dsn_env)
        if not value:
            raise ConfigError(f"database DSN env var {self.dsn_env!r} is not set")
        return value


@dataclass(frozen=True)
class ReadwiseConfig:
    api_token_env: str
    base_url: str
    page_size: int

    @property
    def api_token(self) -> str:
        value = os.environ.get(self.api_token_env)
        if not value:
            raise ConfigError(f"Readwise token env var {self.api_token_env!r} is not set")
        return value


@dataclass(frozen=True)
class PinboardConfig:
    api_token_env: str
    base_url: str

    @property
    def api_token(self) -> str:
        value = os.environ.get(self.api_token_env)
        if not value:
            raise ConfigError(f"Pinboard token env var {self.api_token_env!r} is not set")
        return value


@dataclass(frozen=True)
class ReviewConfig:
    staging_root: Path
    daily_root: Path
    batch_size: int
    privacy: str


@dataclass(frozen=True)
class ClassifierConfig:
    mode: str
    confidence_threshold: float


@dataclass(frozen=True)
class IntakeConfig:
    path: Path
    database: DatabaseConfig
    readwise: ReadwiseConfig
    pinboard: PinboardConfig
    review: ReviewConfig
    final_default_root: Path
    classifier: ClassifierConfig
    active_context_file: Path
    destinations: dict[str, DestinationConfig]


def _resolve_path(config_path: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (config_path.parent.parent / path).resolve()


def load_config(path: str | Path | None = None) -> IntakeConfig:
    config_path = Path(path or os.environ.get("INTAKE_CONFIG", "config/intake.local.yaml")).expanduser()
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    data = yaml.safe_load(config_path.read_text()) or {}
    try:
        database = DatabaseConfig(
            dsn_env=data["database"].get("dsn_env", "INTAKE_DATABASE_DSN"),
            schema=data["database"].get("schema", "intake"),
        )
        readwise = ReadwiseConfig(
            api_token_env=data["readwise"].get("api_token_env", "READWISE_API_TOKEN"),
            base_url=data["readwise"].get("base_url", "https://readwise.io/api/v3").rstrip("/"),
            page_size=int(data["readwise"].get("page_size", 100)),
        )
        pinboard_data = data.get("pinboard") or {}
        pinboard = PinboardConfig(
            api_token_env=pinboard_data.get("api_token_env", "PINBOARD_API_TOKEN"),
            base_url=pinboard_data.get("base_url", "https://api.pinboard.in/v1").rstrip("/"),
        )
        review = ReviewConfig(
            staging_root=_resolve_path(config_path, data["review"]["staging_root"]),
            daily_root=_resolve_path(config_path, data["review"]["daily_root"]),
            batch_size=int(data["review"].get("batch_size", 25)),
            privacy=data["review"].get("privacy", "private"),
        )
        classifier = ClassifierConfig(
            mode=data["classifier"].get("mode", "rules"),
            confidence_threshold=float(data["classifier"].get("confidence_threshold", 0.75)),
        )
        destinations = {
            key: DestinationConfig(
                key=key,
                label=value["label"],
                path=_resolve_path(config_path, value["path"]),
                private=bool(value.get("private", True)),
            )
            for key, value in data["destinations"].items()
        }
        active_context_file = _resolve_path(
            config_path,
            data.get("active_context", {}).get("interests_file", "./config/active-context.example.yaml"),
        )
        final_default_root = _resolve_path(
            config_path,
            data.get("final_notes", {}).get("default_root", "./out/knowledge"),
        )
    except KeyError as exc:
        raise ConfigError(f"missing required config key: {exc}") from exc

    _validate_destinations(destinations)
    return IntakeConfig(
        path=config_path,
        database=database,
        readwise=readwise,
        pinboard=pinboard,
        review=review,
        final_default_root=final_default_root,
        classifier=classifier,
        active_context_file=active_context_file,
        destinations=destinations,
    )


def _validate_destinations(destinations: dict[str, DestinationConfig]) -> None:
    required = set(KNOWLEDGE_BASE_KEYS)
    missing = sorted(required - set(destinations))
    if missing:
        raise ConfigError(f"missing destination definitions: {', '.join(missing)}")


def load_active_context(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}
