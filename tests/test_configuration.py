import secrets

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from scripts.configure import build_configuration


def test_configuration_generates_separate_secrets_and_test_database() -> None:
    template = {key: value or "" for key, value in dotenv_values(".env.example").items()}
    key = secrets.token_urlsafe(32)
    first = build_configuration("https://tracker.example", "test_skudarnov_", key, template)
    second = build_configuration("https://tracker.example", "test_skudarnov_", key, template)
    assert first["KEITARO_API_KEY"] == key
    assert first["APP_TOKEN"] != second["APP_TOKEN"]
    assert first["APP_TOKEN"] != first["POSTGRES_PASSWORD"]
    assert first["TEST_DATABASE_URL"].endswith("_test")
    assert first["POSTGRES_PASSWORD"] in first["DATABASE_URL"]
    with pytest.raises(ValidationError):
        build_configuration("http://tracker.example", "test_skudarnov_", key, template)
