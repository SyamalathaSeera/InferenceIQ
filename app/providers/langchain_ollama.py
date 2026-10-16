import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_ollama import ChatOllama

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


class LangChainOllamaProvider(ChatProvider):
    """Local Ollama adapter implemented through LangChain's ChatOllama."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        default_model: str,
        model_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._default_model = default_model
        self._model_factory = model_factory or (
            lambda model: ChatOllama(model=model, base_url=base_url)
        )

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        model_name = self._model_name(request.model)
        model = self._model_factory(model_name)
        try:
            response: AIMessage = await model.ainvoke(
                self._messages(request), **self._generation_parameters(request)
            )
        except Exception as exc:
            raise ProviderError(
                "The Ollama request failed. Confirm that Ollama is running "
                "and the configured model has been pulled."
            ) from exc

        usage = response.usage_metadata or {}
        return ChatCompletionResponse(
            id=response.id or f"chatcmpl-ollama-{uuid4().hex}",
            created=int(time.time()),
            model=model_name,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChoiceMessage(content=self._text(response.content)),
                    finish_reason=self._finish_reason(response.response_metadata),
                )
            ],
            usage=Usage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
            provider=self.name,
            metadata={
                "usage_available": (
                    "input_tokens" in usage and "output_tokens" in usage
                )
            },
        )

    async def stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        model_name = self._model_name(request.model)
        model = self._model_factory(model_name)
        completion_id = f"chatcmpl-ollama-{uuid4().hex}"
        created = int(time.time())
        first_chunk = True

        try:
            async for chunk in model.astream(self._messages(request)):
                assert isinstance(chunk, AIMessageChunk)
                yield ChatCompletionChunk(
                    id=chunk.id or completion_id,
                    created=created,
                    model=model_name,
                    choices=[
                        ChatCompletionChunkChoice(
                            index=0,
                            delta=DeltaMessage(
                                role="assistant" if first_chunk else None,
                                content=self._text(chunk.content) or None,
                            ),
                            finish_reason=self._finish_reason(
                                chunk.response_metadata, default=None
                            ),
                        )
                    ],
                    provider=self.name,
                )
                first_chunk = False
        except Exception as exc:
            raise ProviderError(
                "The Ollama stream failed. Confirm that Ollama is running "
                "and the configured model has been pulled."
            ) from exc

    def _model_name(self, requested_model: str | None) -> str:
        model = requested_model or self._default_model
        return model.removeprefix("ollama/")

    @staticmethod
    def _generation_parameters(request: ChatCompletionRequest) -> dict:
        options = {
            key: value
            for key, value in {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            }.items()
            if value is not None
        }
        return {"options": options} if options else {}

    @staticmethod
    def _messages(request: ChatCompletionRequest) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        for message in request.messages:
            content = message.content or ""
            if message.role == "system":
                messages.append(SystemMessage(content=content))
            elif message.role == "user":
                messages.append(HumanMessage(content=content))
            elif message.role == "assistant":
                messages.append(AIMessage(content=content))
            elif message.role == "tool":
                messages.append(
                    ToolMessage(
                        content=content,
                        tool_call_id=message.tool_call_id or "unknown",
                    )
                )
        return messages

    @staticmethod
    def _text(content: str | list[str | dict[str, Any]]) -> str:
        if isinstance(content, str):
            return content
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )

    @staticmethod
    def _finish_reason(
        metadata: dict[str, Any], *, default: str | None = "stop"
    ) -> str | None:
        return metadata.get("done_reason") or metadata.get("stop_reason") or default
