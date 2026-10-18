from pathlib import Path

import pytest
from app.config import Settings
from pydantic import AliasChoices
from pydantic.fields import FieldInfo

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"

GOOGLE_UNPREFIXED = {"GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"}

MAY_BE_EMPTY = {"FV_SEED_DIR", "FV_REPLAY_DIR", "FV_DOCS_DIR"}


def _env_names_for(field_name: str, field: FieldInfo) -> list[str]:
    alias = field.validation_alias
    if isinstance(alias, AliasChoices):
        return [str(choice) for choice in alias.choices]
    if alias is None:
        return [f"FV_{field_name.upper()}"]
    return [str(alias)]


def _readable_env_names() -> set[str]:
    names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        names.update(_env_names_for(field_name, field))
    return names


def _literal_defaults() -> dict[str, str]:
    defaults: dict[str, str] = {}
    for field_name, field in Settings.model_fields.items():
        value = field.default
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        defaults[_env_names_for(field_name, field)[0]] = str(value)
    return defaults


def _documented_pairs() -> dict[str, str]:
    return {
        line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }


def _documented_variables() -> set[str]:
    return set(_documented_pairs())


def test_env_example_documents_every_configured_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    readable = _readable_env_names()
    assert readable, "Settings declares no fields; the test is vacuous"
    assert readable >= GOOGLE_UNPREFIXED, GOOGLE_UNPREFIXED - readable
    configured = readable | GOOGLE_UNPREFIXED
    assert configured <= _documented_variables()
    monkeypatch.delenv("FV_MODEL_LOCATION", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west4")
    assert Settings().model_location == "europe-west4", "the environment must be read when Settings is constructed, not at import"


def test_env_example_documents_nothing_that_does_not_exist() -> None:
    documented = _documented_variables()
    assert documented, ".env.example documents nothing; the test is vacuous"
    assert documented <= (_readable_env_names() | GOOGLE_UNPREFIXED)


def test_documented_defaults_match_config() -> None:
    defaults = _literal_defaults()
    assert defaults, "no literal defaults found; the test is vacuous"
    wrong = {name: (value, defaults[name]) for name, value in _documented_pairs().items() if name in defaults and value != defaults[name]}
    assert not wrong, wrong


def test_only_path_variables_may_be_empty() -> None:
    empty = {name for name, value in _documented_pairs().items() if not value}
    allowed = MAY_BE_EMPTY | {name for name, value in _literal_defaults().items() if not value}
    assert empty == allowed, empty
