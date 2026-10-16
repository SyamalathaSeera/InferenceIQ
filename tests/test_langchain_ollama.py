import asyncio
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk

from app.providers.langchain_ollama import LangChainOllamaProvider
from app.schemas.chat import ChatCompletionRequest


class FakeOllamaModel:
    async def ainvoke(self, messages: list) -> AIMessage:
        assert messages[-1].content == "Hello local model"
        return AIMessage(
            id="ollama-test-id",
            content="Local response",
            response_metadata={"done_reason": "stop"},
            usage_metadata={
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
            },
        )

    async def astream(self, messages: list) -> AsyncIterator[AIMessageChunk]:
        yield AIMessageChunk(content="Local ")
        yield AIMessageChunk(
            content="stream",
            response_metadata={"done_reason": "stop"},
        )


def create_fake_model(model_name: str) -> FakeOllamaModel:
    assert model_name == "test-model"
    return FakeOllamaModel()


def request(*, stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="ollama/test-model",
        messages=[{"role": "user", "content": "Hello local model"}],
        stream=stream,
    )


def test_langchain_ollama_normalizes_completion() -> None:
    provider = LangChainOllamaProvider(
        base_url="http://unused.test",
        default_model="test-model",
        model_factory=create_fake_model,
    )

    response = asyncio.run(provider.complete(request()))

    assert response.provider == "ollama"
    assert response.model == "test-model"
    assert response.choices[0].message.content == "Local response"
    assert response.usage.total_tokens == 5


def test_langchain_ollama_normalizes_stream() -> None:
    provider = LangChainOllamaProvider(
        base_url="http://unused.test",
        default_model="test-model",
        model_factory=create_fake_model,
    )

    async def collect_chunks() -> list:
        return [chunk async for chunk in provider.stream(request(stream=True))]

    chunks = asyncio.run(collect_chunks())

    content = "".join(chunk.choices[0].delta.content or "" for chunk in chunks)
    assert content == "Local stream"
    assert chunks[-1].choices[0].finish_reason == "stop"
