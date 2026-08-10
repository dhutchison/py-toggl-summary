"""Persistent non-secret settings and OS credential-store access."""

from __future__ import annotations

import os
import stat
import tomllib
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import keyring
import tomli_w
from keyring.errors import KeyringError
from platformdirs import user_config_path

from .domain import DEFAULT_ACTIVITY_TYPES

CONFIG_SERVICE = "toggl-cli"
CONFIG_ACCOUNT = "api-token"
SECRET_KEY_PARTS = ("token", "password", "authorization", "secret")


class ConfigError(RuntimeError):
    """A configuration or credential failure safe to show to a user."""


class CredentialStore(Protocol):
    def get(self) -> str | None: ...

    def set(self, token: str) -> None: ...


class KeyringCredentialStore:
    def get(self) -> str | None:
        try:
            return keyring.get_password(CONFIG_SERVICE, CONFIG_ACCOUNT)
        except KeyringError as error:
            raise ConfigError("The operating-system credential store is unavailable.") from error

    def set(self, token: str) -> None:
        try:
            keyring.set_password(CONFIG_SERVICE, CONFIG_ACCOUNT, token)
        except KeyringError as error:
            raise ConfigError(
                "The API token could not be saved to the credential store."
            ) from error


@dataclass(frozen=True, slots=True)
class ReviewSettings:
    activity_types: tuple[str, ...] = DEFAULT_ACTIVITY_TYPES
    jira_activity_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Settings:
    workspace_id: int | None = None
    review: ReviewSettings = field(default_factory=ReviewSettings)
    raw_config: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def _without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_secrets(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in SECRET_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


def default_config_path() -> Path:
    return Path(user_config_path("toggl-cli", appauthor=False)) / "config.toml"


def _validated_string_list(
    value: Any,
    *,
    setting: str,
    path: Path,
    allow_empty: bool,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a non-empty array" if not allow_empty else "an array"
        raise ConfigError(f"{path}: review.{setting} must be {qualifier} of strings.")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item or item != item.strip():
            raise ConfigError(
                f"{path}: review.{setting}[{index}] must be a non-blank string "
                "without surrounding whitespace."
            )
        key = item.casefold()
        if key in seen:
            raise ConfigError(f"{path}: review.{setting}[{index}] duplicates another value.")
        if key == "marker":
            raise ConfigError(
                f"{path}: review.{setting}[{index}] uses reserved activity type marker."
            )
        seen.add(key)
        result.append(item)
    return result


def _review_settings(raw: dict[str, Any], path: Path) -> ReviewSettings:
    review = raw.get("review", {})
    if not isinstance(review, dict):
        raise ConfigError(f"{path}: review must be a TOML table.")
    activity_value = review.get("activity_types", list(DEFAULT_ACTIVITY_TYPES))
    activities = _validated_string_list(
        activity_value, setting="activity_types", path=path, allow_empty=False
    )
    jira_value = review.get("jira_activity_types", [])
    jira = _validated_string_list(
        jira_value, setting="jira_activity_types", path=path, allow_empty=True
    )
    by_key = {activity.casefold(): activity for activity in activities}
    unknown = [value for value in jira if value.casefold() not in by_key]
    if unknown:
        raise ConfigError(
            f"{path}: review.jira_activity_types contains value outside "
            f"activity_types: {unknown[0]!r}."
        )
    jira_keys = {value.casefold() for value in jira}
    return ReviewSettings(
        activity_types=tuple(activities),
        jira_activity_types=tuple(
            activity for activity in activities if activity.casefold() in jira_keys
        ),
    )


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or default_config_path()
    if not config_path.exists():
        return Settings()
    try:
        with config_path.open("rb") as config_file:
            raw = tomllib.load(config_file)
        toggl = raw.get("toggl", {})
        workspace = toggl.get("workspace_id")
        if workspace is not None and (not isinstance(workspace, int) or workspace <= 0):
            raise ConfigError("config.toml contains an invalid toggl.workspace_id.")
        return Settings(
            workspace_id=workspace,
            review=_review_settings(raw, config_path),
            raw_config=raw,
        )
    except ConfigError:
        raise
    except (OSError, tomllib.TOMLDecodeError, AttributeError) as error:
        raise ConfigError(f"Could not read configuration: {config_path}") from error


def save_settings(
    settings: Settings,
    token: str,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> None:
    if not token:
        raise ConfigError("An API token is required when saving configuration.")
    credential_store = credentials or KeyringCredentialStore()
    credential_store.set(token)
    config_path = path or default_config_path()
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = config_path.with_suffix(".toml.tmp")
        raw_config = _without_secrets(deepcopy(settings.raw_config))
        toggl_settings = dict(raw_config.get("toggl", {}))
        if settings.workspace_id is not None:
            toggl_settings["workspace_id"] = settings.workspace_id
        else:
            toggl_settings.pop("workspace_id", None)
        raw_config["toggl"] = toggl_settings
        if "review" in raw_config or settings.review != ReviewSettings():
            review_settings = dict(raw_config.get("review", {}))
            review_settings["activity_types"] = list(settings.review.activity_types)
            review_settings["jira_activity_types"] = list(settings.review.jira_activity_types)
            raw_config["review"] = review_settings
        temporary_path.write_text(tomli_w.dumps(raw_config), encoding="utf-8")
        os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
        temporary_path.replace(config_path)
    except OSError as error:
        raise ConfigError(f"Could not save configuration: {config_path}") from error


def load_token(credentials: CredentialStore | None = None) -> str | None:
    return (credentials or KeyringCredentialStore()).get()
