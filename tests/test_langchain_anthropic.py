import asyncio
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk

from app.providers.langchain_anthropic import LangChainAnthropicProvider
from app.schemas.chat import ChatCompletionRequest


class FakeLangChainModel:
    async def ainvoke(self, messages: list) -> AIMessage:
        assert messages[-1].content == "Hello Anthropic"
        return AIMessage(
            id="anthropic-test-id",
            content="Hello from LangChain",
            response_metadata={"stop_reason": "end_turn"},
            usage_metadata={
                "input_tokens": 3,
                "output_tokens": 4,
                "total_tokens": 7,
            },
        )

    async def astream(self, messages: list) -> AsyncIterator[AIMessageChunk]:
        yield AIMessageChunk(content="Hello ")
        yield AIMessageChunk(
            content="stream",
            response_metadata={"stop_reason": "end_turn"},
        )


def create_fake_model(model_name: str) -> FakeLangChainModel:
    assert model_name == "test-model"
    return FakeLangChainModel()


def request(*, stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="anthropic/test-model",
        messages=[{"role": "user", "content": "Hello Anthropic"}],
        stream=stream,
    )


def test_langchain_anthropic_normalizes_completion() -> None:
    provider = LangChainAnthropicProvider(
        api_key="unused-test-key",
        default_model="test-model",
        model_factory=create_fake_model,
    )

    response = asyncio.run(provider.complete(request()))

    assert response.provider == "anthropic"
    assert response.model == "test-model"
    assert response.choices[0].message.content == "Hello from LangChain"
    assert response.usage.total_tokens == 7


def test_langchain_anthropic_normalizes_stream() -> None:
    provider = LangChainAnthropicProvider(
        api_key="unused-test-key",
        default_model="test-model",
        model_factory=create_fake_model,
    )

    async def collect_chunks() -> list:
        return [chunk async for chunk in provider.stream(request(stream=True))]

    chunks = asyncio.run(collect_chunks())

    assert "".join(chunk.choices[0].delta.content or "" for chunk in chunks) == (
        "Hello stream"
    )
    assert chunks[-1].choices[0].finish_reason == "end_turn"
