from collections.abc import AsyncIterator
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import authenticate_and_admit
from app.audit import append_audit_event
from app.database import get_session
from app.health import ProviderAttempt, load_model_health, record_provider_attempts
from app.inference.engine import (
    CircuitOpenError,
    InferenceDecisionEngine,
    NoEligibleModelError,
)
from app.inference.executor import execute_completion
from app.inference.metrics import add_prediction
from app.observability import LangSmithObserver
from app.persistence.repository import request_fingerprint, save_inference
from app.policy.rollout import (
    apply_assignment,
    assign_rollout,
    attach_assignment,
    record_outcome_and_apply_guardrails,
)
from app.privacy import protect_request
from app.providers.base import ChatProvider, ProviderError
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse

router = APIRouter(prefix="/v1", tags=["chat"])


def get_providers(request: Request) -> dict[str, ChatProvider]:
    return request.app.state.providers


def get_decision_engine(request: Request) -> InferenceDecisionEngine:
    return request.app.state.decision_engine


def get_observer(request: Request) -> LangSmithObserver:
    return request.app.state.observer


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    body: ChatCompletionRequest,
    request: Request,
    providers: Annotated[dict[str, ChatProvider], Depends(get_providers)],
    decision_engine: Annotated[InferenceDecisionEngine, Depends(get_decision_engine)],
    session: Annotated[AsyncSession, Depends(get_session)],
    observer: Annotated[LangSmithObserver, Depends(get_observer)],
) -> ChatCompletionResponse | StreamingResponse:
    access = await authenticate_and_admit(session, request)
    protected_body = body
    privacy = None
    if access is not None:
        privacy = protect_request(body, access.project.pii_policy)
        protected_body = privacy.request
        if privacy.match_count:
            await append_audit_event(
                session,
                signing_key=request.app.state.settings.inferenceiq_audit_signing_key,
                project_id=access.project.id,
                event_type=f"privacy.pii_{privacy.action}",
                actor_type="api_key",
                actor_id=access.api_key_id,
                details={
                    "detected_types": privacy.detected_types,
                    "match_count": privacy.match_count,
                    "content_recorded": False,
                },
            )
            if privacy.action == "block":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Request blocked by the project's PII policy",
                        "detected_types": privacy.detected_types,
                    },
                )

    assignment = await assign_rollout(session, protected_body)
    effective_body = apply_assignment(protected_body, assignment)
    settings = request.app.state.settings
    health = await load_model_health(
        session,
        cooldown_seconds=settings.circuit_cooldown_seconds,
        now=request.app.state.health_clock(),
    )
    try:
        decision = decision_engine.decide(
            requested_model=effective_body.model,
            options=effective_body.inferenceiq,
            health=health,
        )
    except CircuitOpenError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except NoEligibleModelError as exc:
        await record_outcome_and_apply_guardrails(
            session,
            assignment,
            receipt_id=None,
            success=False,
            latency_ms=None,
            cost_usd=None,
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    decision.receipt = attach_assignment(decision.receipt, assignment)
    if privacy is not None:
        decision.receipt = decision.receipt.model_copy(
            update={
                "pii_action": privacy.action,
                "pii_detected_types": privacy.detected_types,
                "pii_redaction_count": (
                    privacy.match_count if privacy.action == "redact" else 0
                ),
            }
        )

    if body.stream:
        provider = providers.get(decision.model.provider)
        if provider is None:
            await record_provider_attempts(
                session,
                [
                    ProviderAttempt(
                        model_id=decision.model.model_id,
                        provider=decision.model.provider,
                        success=False,
                        latency_ms=0,
                        error_category="configuration",
                    )
                ],
                failure_threshold=settings.circuit_failure_threshold,
                ewma_alpha=settings.health_ewma_alpha,
                now=request.app.state.health_clock(),
            )
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{decision.model.provider}' is not configured.",
            )
        provider_request = effective_body.model_copy(
            update={"model": decision.model.model_id}
        )
        decision.receipt = add_prediction(
            decision.receipt,
            decision.model,
            provider_request,
        )

        async def event_stream() -> AsyncIterator[str]:
            started_at = perf_counter()
            try:
                async for chunk in provider.stream(provider_request):
                    yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"
            except ProviderError as exc:
                await record_provider_attempts(
                    session,
                    [
                        ProviderAttempt(
                            model_id=decision.model.model_id,
                            provider=decision.model.provider,
                            success=False,
                            latency_ms=(perf_counter() - started_at) * 1000,
                            error_category=exc.category,
                        )
                    ],
                    failure_threshold=settings.circuit_failure_threshold,
                    ewma_alpha=settings.health_ewma_alpha,
                    now=request.app.state.health_clock(),
                )
                raise
            await record_provider_attempts(
                session,
                [
                    ProviderAttempt(
                        model_id=decision.model.model_id,
                        provider=decision.model.provider,
                        success=True,
                        latency_ms=(perf_counter() - started_at) * 1000,
                    )
                ],
                failure_threshold=settings.circuit_failure_threshold,
                ewma_alpha=settings.health_ewma_alpha,
                now=request.app.state.health_clock(),
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "X-InferenceIQ-Receipt-ID": decision.receipt.receipt_id,
                "X-InferenceIQ-Model": decision.model.model_id,
                "X-InferenceIQ-Policy": decision.receipt.policy,
            },
        )

    attempts: list[ProviderAttempt] = []

    def observe_attempt(
        model_id: str,
        provider: str,
        success: bool,
        latency_ms: float,
        error_category: str | None,
    ) -> None:
        latency_observer = getattr(request.app.state, "health_latency_ms", None)
        attempts.append(
            ProviderAttempt(
                model_id=model_id,
                provider=provider,
                success=success,
                latency_ms=(
                    latency_observer(model_id, latency_ms)
                    if latency_observer
                    else latency_ms
                ),
                error_category=error_category,
            )
        )

    try:
        response = await execute_completion(
            effective_body,
            decision,
            providers,
            attempt_observer=observe_attempt,
        )
        await record_provider_attempts(
            session,
            attempts,
            failure_threshold=settings.circuit_failure_threshold,
            ewma_alpha=settings.health_ewma_alpha,
            now=request.app.state.health_clock(),
        )
        trace_id = await observer.record_inference(
            request_fingerprint=request_fingerprint(effective_body),
            response=response,
        )
        await save_inference(
            session,
            effective_body,
            response,
            langsmith_trace_id=trace_id,
            project_id=access.project.id if access else None,
        )
        receipt = response.inference_receipt
        await record_outcome_and_apply_guardrails(
            session,
            assignment,
            receipt_id=receipt.receipt_id if receipt else None,
            success=True,
            latency_ms=receipt.provider_latency_ms if receipt else None,
            cost_usd=receipt.actual_cost_usd if receipt else None,
        )
        return response
    except ProviderError as exc:
        await record_provider_attempts(
            session,
            attempts,
            failure_threshold=settings.circuit_failure_threshold,
            ewma_alpha=settings.health_ewma_alpha,
            now=request.app.state.health_clock(),
        )
        await record_outcome_and_apply_guardrails(
            session,
            assignment,
            receipt_id=decision.receipt.receipt_id,
            success=False,
            latency_ms=None,
            cost_usd=None,
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
