import time
from collections.abc import AsyncIterator
from uuid import uuid4

from app.providers.base import ChatProvider
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


class MockProvider(ChatProvider):
    """A deterministic provider for local development and tests."""

    name = "mock"

    @staticmethod
    def _last_user_message(request: ChatCompletionRequest) -> str:
        return next(
            (
                message.content
                for message in reversed(request.messages)
                if message.role == "user" and message.content
            ),
            "",
        )

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        last_user_message = self._last_user_message(request)
        content = f"Mock response: {last_user_message}"
        prompt_tokens = sum(
            len((message.content or "").split()) for message in request.messages
        )
        completion_tokens = len(content.split())

        return ChatCompletionResponse(
            id=f"chatcmpl-mock-{uuid4().hex}",
            created=int(time.time()),
            model=request.model or "mock-model",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChoiceMessage(content=content),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            provider=self.name,
            metadata={"simulated": True},
        )

    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        completion_id = f"chatcmpl-mock-{uuid4().hex}"
        created = int(time.time())
        model = request.model or "mock-model"
        words = f"Mock response: {self._last_user_message(request)}".split(" ")

        for index, word in enumerate(words):
            yield ChatCompletionChunk(
                id=completion_id,
                created=created,
                model=model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta=DeltaMessage(
                            role="assistant" if index == 0 else None,
                            content=word if index == 0 else f" {word}",
                        ),
                    )
                ],
                provider=self.name,
            )

        yield ChatCompletionChunk(
            id=completion_id,
            created=created,
            model=model,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta=DeltaMessage(),
                    finish_reason="stop",
                )
            ],
            provider=self.name,
        )
