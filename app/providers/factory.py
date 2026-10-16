from app.config import Settings
from app.providers.base import ChatProvider
from app.providers.langchain_anthropic import LangChainAnthropicProvider
from app.providers.langchain_ollama import LangChainOllamaProvider
from app.providers.mock import MockProvider
from app.providers.openai import OpenAIProvider


def create_providers(settings: Settings) -> dict[str, ChatProvider]:
    providers: dict[str, ChatProvider] = {}

    if settings.provider == "mock":
        providers["mock"] = MockProvider()

    if settings.openai_api_key:
        providers["openai"] = OpenAIProvider(
            api_key=settings.openai_api_key,
            default_model=settings.openai_model,
        )

    if settings.anthropic_api_key:
        providers["anthropic"] = LangChainAnthropicProvider(
            api_key=settings.anthropic_api_key,
            default_model=settings.anthropic_model,
        )

    if settings.ollama_enabled or settings.provider == "ollama":
        providers["ollama"] = LangChainOllamaProvider(
            base_url=settings.ollama_base_url,
            default_model=settings.ollama_model,
        )

    if settings.provider == "openai" and "openai" not in providers:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when PROVIDER=openai")

    if settings.provider == "anthropic" and "anthropic" not in providers:
        raise RuntimeError("ANTHROPIC_API_KEY is required when PROVIDER=anthropic")

    return providers
