from pathlib import Path

import pytest
from pydantic import ValidationError

from entsoe_py.config import Settings


def test_reads_token_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # empty dir -> no .env file is picked up
    monkeypatch.setenv("ENTSOE_API_TOKEN", "dummy-token")

    settings = Settings()

    assert settings.api_token.get_secret_value() == "dummy-token"
    assert "dummy-token" not in repr(settings)  # the secret stays hidden


def test_missing_token_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENTSOE_API_TOKEN", raising=False)

    with pytest.raises(ValidationError):
        Settings()
