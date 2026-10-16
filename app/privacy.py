import re
from dataclasses import dataclass

from app.schemas.access import PiiPolicy
from app.schemas.chat import ChatCompletionRequest


@dataclass(frozen=True)
class PrivacyResult:
    request: ChatCompletionRequest
    action: PiiPolicy
    detected_types: list[str]
    match_count: int


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    ("ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    (
        "phone",
        re.compile(
            r"(?<!\w)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)"
            r"\d{3}[ .-]\d{4}(?!\w)"
        ),
    ),
]
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def protect_request(
    request: ChatCompletionRequest,
    policy: PiiPolicy,
) -> PrivacyResult:
    if policy == "off":
        return PrivacyResult(
            request=request, action=policy, detected_types=[], match_count=0
        )

    detected: set[str] = set()
    total = 0
    protected_messages = []
    for message in request.messages:
        content = message.content
        if not content:
            protected_messages.append(message)
            continue
        transformed = content
        for pii_type, pattern in _PATTERNS:
            matches = list(pattern.finditer(transformed))
            if matches:
                detected.add(pii_type)
                total += len(matches)
                if policy == "redact":
                    transformed = pattern.sub(
                        f"[{pii_type.upper()}_REDACTED]", transformed
                    )

        transformed, card_count = _protect_cards(transformed, redact=policy == "redact")
        if card_count:
            detected.add("payment_card")
            total += card_count
        protected_messages.append(message.model_copy(update={"content": transformed}))

    protected_request = (
        request.model_copy(update={"messages": protected_messages})
        if policy == "redact" and total
        else request
    )
    return PrivacyResult(
        request=protected_request,
        action=policy,
        detected_types=sorted(detected),
        match_count=total,
    )


def _protect_cards(value: str, *, redact: bool) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        candidate = match.group(0)
        digits = "".join(character for character in candidate if character.isdigit())
        if not _passes_luhn(digits):
            return candidate
        count += 1
        return "[PAYMENT_CARD_REDACTED]" if redact else candidate

    return _CARD_CANDIDATE.sub(replace, value), count


def _passes_luhn(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        number = int(character)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0
