from app.config import Settings
from app.inference.catalog import create_model_catalog


def test_mock_mode_never_adds_real_providers() -> None:
    settings = Settings(
        provider="mock",
        openai_api_key="present-but-must-not-be-used",
        anthropic_api_key="present-but-must-not-be-used",
        ollama_enabled=True,
    )

    catalog = create_model_catalog(settings)

    assert {profile.provider for profile in catalog} == {"mock"}


def test_real_mode_builds_cross_provider_catalog() -> None:
    settings = Settings(
        provider="openai",
        openai_api_key="test-openai-key",
        anthropic_api_key="test-anthropic-key",
        ollama_enabled=True,
    )

    catalog = create_model_catalog(settings)

    assert [profile.provider for profile in catalog] == [
        "openai",
        "anthropic",
        "ollama",
    ]
