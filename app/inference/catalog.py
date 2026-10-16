from app.config import Settings
from app.inference.models import ModelProfile


def create_model_catalog(settings: Settings) -> list[ModelProfile]:
    """Create a small development catalog; a database will replace this later."""

    if settings.provider == "mock":
        return [
            ModelProfile(
                model_id="mock/fast",
                provider="mock",
                quality_score=0.55,
                estimated_latency_ms=250,
                input_cost_per_million=0.10,
                output_cost_per_million=0.20,
                zero_data_retention=True,
            ),
            ModelProfile(
                model_id="mock/balanced",
                provider="mock",
                quality_score=0.78,
                estimated_latency_ms=700,
                input_cost_per_million=0.50,
                output_cost_per_million=1.50,
                zero_data_retention=True,
            ),
            ModelProfile(
                model_id="mock/quality",
                provider="mock",
                quality_score=0.96,
                estimated_latency_ms=1600,
                input_cost_per_million=3.00,
                output_cost_per_million=12.00,
                zero_data_retention=False,
            ),
        ]

    profiles: list[ModelProfile] = []

    if settings.openai_api_key:
        profiles.append(
            ModelProfile(
                model_id=f"openai/{settings.openai_model}",
                provider="openai",
                quality_score=0.78,
                estimated_latency_ms=900,
                input_cost_per_million=settings.openai_input_cost_per_million,
                output_cost_per_million=settings.openai_output_cost_per_million,
            )
        )

    if settings.anthropic_api_key:
        profiles.append(
            ModelProfile(
                model_id=f"anthropic/{settings.anthropic_model}",
                provider="anthropic",
                quality_score=0.82,
                estimated_latency_ms=1100,
                input_cost_per_million=settings.anthropic_input_cost_per_million,
                output_cost_per_million=settings.anthropic_output_cost_per_million,
            )
        )

    if settings.ollama_enabled or settings.provider == "ollama":
        profiles.append(
            ModelProfile(
                model_id=f"ollama/{settings.ollama_model}",
                provider="ollama",
                quality_score=0.60,
                estimated_latency_ms=1400,
                input_cost_per_million=0,
                output_cost_per_million=0,
                zero_data_retention=True,
            )
        )

    if not profiles:
        raise RuntimeError("No providers are configured for automatic selection")

    # Unprefixed explicit model names use the first profile as their provider.
    profiles.sort(key=lambda profile: profile.provider != settings.provider)
    return profiles
