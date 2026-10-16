from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

from app.schemas.chat import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)


class ProviderError(Exception):
    """A provider failure represented independently of any provider SDK."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        category: Literal[
            "rate_limit",
            "timeout",
            "unavailable",
            "invalid_request",
            "provider_error",
            "configuration",
        ] = "provider_error",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.retryable = retryable


class ChatProvider(ABC):
    name: str

    @abstractmethod
    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Return one normalized, non-streaming chat completion."""

    @abstractmethod
    def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Yield normalized chat-completion chunks as they arrive."""
