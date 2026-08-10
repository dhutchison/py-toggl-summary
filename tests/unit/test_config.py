from pathlib import Path

import pytest
from keyring.errors import KeyringError

from toggl_cli.config import (
    ConfigError,
    KeyringCredentialStore,
    ReviewSettings,
    Settings,
    load_settings,
    save_settings,
)


class FakeCredentials:
    def __init__(self) -> None:
        self.token: str | None = None

    def get(self) -> str | None:
        return self.token

    def set(self, token: str) -> None:
        self.token = token


def test_saved_config_contains_workspace_but_never_token(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    credentials = FakeCredentials()

    save_settings(Settings(123), "do-not-write-me", config_path, credentials)

    assert credentials.token == "do-not-write-me"
    assert load_settings(config_path) == Settings(123)
    assert "do-not-write-me" not in config_path.read_text()


def test_saving_without_workspace_writes_valid_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_settings(Settings(), "token", config_path, FakeCredentials())

    assert load_settings(config_path) == Settings()


def test_saving_preserves_unknown_configuration_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("future = 'keep-me'\n[toggl]\nworkspace_id = 123\nregion = 'uk'\n")
    settings = load_settings(config_path)

    save_settings(settings, "token", config_path, FakeCredentials())

    saved = config_path.read_text()
    assert 'future = "keep-me"' in saved
    assert 'region = "uk"' in saved


def test_saving_drops_secret_like_unknown_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[toggl]\nworkspace_id = 123\napi_token = 'old-secret'\n")
    settings = load_settings(config_path)

    save_settings(settings, "token", config_path, FakeCredentials())

    assert "old-secret" not in config_path.read_text()


def test_invalid_workspace_configuration_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[toggl]\nworkspace_id = -1\n")

    with pytest.raises(ConfigError, match=r"invalid toggl\.workspace_id"):
        load_settings(config_path)


def test_keyring_failures_become_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(service: str, account: str) -> None:
        raise KeyringError("unavailable")

    monkeypatch.setattr("toggl_cli.config.keyring.get_password", fail)

    with pytest.raises(ConfigError, match="credential store is unavailable"):
        KeyringCredentialStore().get()


def test_keyring_save_failures_become_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(service: str, account: str, token: str) -> None:
        raise KeyringError("unavailable")

    monkeypatch.setattr("toggl_cli.config.keyring.set_password", fail)

    with pytest.raises(ConfigError, match="could not be saved"):
        KeyringCredentialStore().set("token")


def test_review_settings_use_defaults_and_preserve_canonical_jira_order(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[review]\nactivity_types = ["Doing", "Reviewing"]\njira_activity_types = ["doing"]\n'
    )

    assert load_settings(config_path).review == ReviewSettings(("Doing", "Reviewing"), ("Doing",))


@pytest.mark.parametrize(
    "contents, message",
    [
        ('[review]\nactivity_types = ["Doing", "doing"]\n', "duplicates"),
        ('[review]\nactivity_types = ["marker"]\n', "reserved"),
        ("[review]\nactivity_types = []\n", "non-empty"),
        ('[review]\nactivity_types = [" Doing"]\n', "surrounding whitespace"),
        (
            '[review]\nactivity_types = ["doing"]\njira_activity_types = ["meeting"]\n',
            "outside activity_types",
        ),
    ],
)
def test_invalid_review_settings_are_rejected(tmp_path: Path, contents: str, message: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(contents)

    with pytest.raises(ConfigError, match=message):
        load_settings(config_path)
