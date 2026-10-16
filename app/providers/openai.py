from collections.abc import AsyncIterator

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError

from app.providers.base import ChatProvider, ProviderError
from app.schemas.chat import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChoiceMessage,
    DeltaMessage,
    Usage,
)


class OpenAIProvider(ChatProvider):
    name = "openai"

    def __init__(self, *, api_key: str, default_model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._default_model = default_model

    def _request_payload(self, request: ChatCompletionRequest) -> dict:
        model = request.model or self._default_model
        payload = {
            "model": model.removeprefix("openai/"),
            "messages": [
                message.model_dump(exclude_none=True) for message in request.messages
            ],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        return payload

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        try:
            response = await self._client.chat.completions.create(
                **self._request_payload(request)
            )
        except RateLimitError as exc:
            raise ProviderError(
                "The provider rate limit was reached.",
                status_code=429,
                category="rate_limit",
            ) from exc
        except APITimeoutError as exc:
            raise ProviderError(
                "The provider timed out.",
                status_code=504,
                category="timeout",
            ) from exc
        except APIError as exc:
            raise ProviderError(
                "The provider request failed.", status_code=502
            ) from exc

        usage = response.usage
        return ChatCompletionResponse(
            id=response.id,
            created=response.created,
            model=response.model,
            choices=[
                ChatCompletionChoice(
                    index=choice.index,
                    message=ChoiceMessage(content=choice.message.content),
                    finish_reason=choice.finish_reason,
                )
                for choice in response.choices
            ],
            usage=Usage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            ),
            provider=self.name,
            metadata={"usage_available": usage is not None},
        )

    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        try:
            stream = await self._client.chat.completions.create(
                **self._request_payload(request), stream=True
            )
            async for chunk in stream:
                yield ChatCompletionChunk(
                    id=chunk.id,
                    created=chunk.created,
                    model=chunk.model,
                    choices=[
                        ChatCompletionChunkChoice(
                            index=choice.index,
                            delta=DeltaMessage(
                                role=(
                                    "assistant"
                                    if choice.delta.role == "assistant"
                                    else None
                                ),
                                content=choice.delta.content,
                            ),
                            finish_reason=choice.finish_reason,
                        )
                        for choice in chunk.choices
                    ],
                    provider=self.name,
                )
        except RateLimitError as exc:
            raise ProviderError(
                "The provider rate limit was reached.",
                status_code=429,
                category="rate_limit",
            ) from exc
        except APITimeoutError as exc:
            raise ProviderError(
                "The provider timed out.",
                status_code=504,
                category="timeout",
            ) from exc
        except APIError as exc:
            raise ProviderError(
                "The provider request failed.", status_code=502
            ) from exc
