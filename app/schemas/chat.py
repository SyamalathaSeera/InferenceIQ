from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.inference.models import InferenceOptions, InferenceReceipt


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None

    model_config = ConfigDict(extra="allow")


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    stream: bool = False
    inferenceiq: InferenceOptions = Field(default_factory=InferenceOptions)

    # Accepting extra fields makes the first version tolerant of OpenAI-compatible
    # clients while we add explicit support for tools and other options later.
    model_config = ConfigDict(extra="allow")


class ChoiceMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChoiceMessage
    finish_reason: str | None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: Usage
    provider: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    inference_receipt: InferenceReceipt | None = None


class DeltaMessage(BaseModel):
    role: Literal["assistant"] | None = None
    content: str | None = None


class ChatCompletionChunkChoice(BaseModel):
    index: int
    delta: DeltaMessage
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]
    provider: str
