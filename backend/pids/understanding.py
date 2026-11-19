"""Small deterministic extractors; all outputs remain reviewable candidates."""

from __future__ import annotations

import re
from calendar import month_name
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, JsonValue

from pids.contracts import Contract

RULE_VERSION = "pids-rules-v2"
MAX_CANDIDATES = 100
_MONTHS = {name.casefold(): index for index, name in enumerate(month_name) if name}
_WEEKDAYS = {
    name.casefold(): index
    for index, name in enumerate(
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    )
}
_DATE_PATTERNS = (
    re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/20\d{2}\b"),
    re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+20\d{2}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+20\d{2}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:tomorrow|today|next\s+Monday|next\s+Tuesday|next\s+Wednesday|next\s+Thursday|"
        r"next\s+Friday|next\s+Saturday|next\s+Sunday|Monday|Tuesday|Wednesday|Thursday|"
        r"Friday|Saturday|Sunday)\b",
        re.IGNORECASE,
    ),
)
_DEADLINE_CUE = re.compile(
    r"\b(?:by|before|until|due(?:\s+date)?|deadline|no\s+later\s+than|on\s+or\s+before)\b"
    r"[^.!?]{0,40}$",
    re.IGNORECASE,
)
_NEGATED_DEADLINE = re.compile(
    r"\b(?:(?:not|never)(?:\s+yet)?\s+due|no\s+deadline|deadline\s+(?:is\s+)?(?:not|cancelled))\b",
    re.IGNORECASE,
)
_TASK_START = re.compile(
    r"^(?:please\s+|action:\s*|i need to\s+|we need to\s+)?"
    r"(?:submit|prepare|call|send|email|contact|review|complete|finish|schedule|"
    r"attend|upload|download|pay|register|apply|confirm|reply|respond|update|"
    r"write|create|build|fix|check|book|return|bring|share|deliver|file|sign)\b",
    re.IGNORECASE,
)
_GENERIC_TASK_ONLY = re.compile(
    r"^(?:please\s+|kindly\s+)?(?:check|review|see|refer|look|click|read)(?:\s+it)?[.!?]*$",
    re.IGNORECASE,
)


class EvidenceCandidate(Contract):
    kind: str = Field(min_length=1, max_length=64)
    evidence: str = Field(min_length=1, max_length=1024)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    rule: str = RULE_VERSION
    fields: dict[str, JsonValue]
    review_status: str = "needs_review"
    explicit: bool = True


class EntityMention(Contract):
    entity_type: str = Field(min_length=1, max_length=64)
    canonical_name: str = Field(min_length=1, max_length=512)
    evidence: str = Field(min_length=1, max_length=1024)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    rule: str = RULE_VERSION


class UnderstandingResult(Contract):
    tasks: tuple[EvidenceCandidate, ...] = ()
    deadlines: tuple[EvidenceCandidate, ...] = ()
    events: tuple[EvidenceCandidate, ...] = ()
    relationships: tuple[EvidenceCandidate, ...] = ()
    facts: tuple[EvidenceCandidate, ...] = ()
    entities: tuple[EntityMention, ...] = ()


def _snippet(text: str) -> str:
    return text if len(text) <= 1024 else text[:1021].rstrip() + "..."


def _sentences(text: str):
    for match in re.finditer(r"[^\n.!?]+(?:[.!?]+|$)", text):
        start, end = match.span()
        sentence = match.group().strip()
        if sentence:
            left = len(match.group()) - len(match.group().lstrip())
            yield sentence, start + left, start + left + len(sentence)


def _absolute_date(expression: str) -> date | None:
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", expression):
        try:
            return date.fromisoformat(expression)
        except ValueError:
            return None
    numeric = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(20\d{2})", expression)
    if numeric:
        first, second, year = map(int, numeric.groups())
        if first <= 12 and second <= 12 and first != second:
            return None
        month, day = (second, first) if first > 12 else (first, second)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    month_first = re.fullmatch(
        r"([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d{2})", expression
    )
    day_first = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", expression)
    match = month_first or day_first
    if match:
        if month_first:
            month, day, year = match.groups()
        else:
            day, month, year = match.groups()
        try:
            return date(int(year), _MONTHS[month.casefold()], int(day))
        except (ValueError, KeyError):
            return None
    return None


def _resolve_date(
    expression: str,
    reference_time: datetime | None,
    timezone_name: str | None,
) -> tuple[date | None, str]:
    absolute = _absolute_date(expression)
    if absolute is not None:
        return absolute, "resolved_absolute"
    if re.search(r"\d", expression) and not re.fullmatch(
        r"\d{1,2}/\d{1,2}/20\d{2}", expression
    ):
        return None, "invalid_calendar_date"
    if re.fullmatch(r"\d{1,2}/\d{1,2}/20\d{2}", expression):
        return None, "ambiguous_numeric_date"
    if reference_time is None or timezone_name is None:
        return None, "missing_reference_timezone"
    local_date = reference_time.astimezone(ZoneInfo(timezone_name)).date()
    normalized = expression.casefold()
    if normalized == "today":
        return local_date, "resolved_relative"
    if normalized == "tomorrow":
        return local_date + timedelta(days=1), "resolved_relative"
    weekday_text = normalized.removeprefix("next ")
    if weekday_text in _WEEKDAYS:
        days = (_WEEKDAYS[weekday_text] - local_date.weekday()) % 7
        if normalized.startswith("next ") and days == 0:
            days = 7
        return local_date + timedelta(days=days), "resolved_relative"
    return None, "unsupported_date_expression"


def _date_mentions(
    sentence: str, offset: int, reference_time: datetime | None, timezone_name: str | None
) -> list[EvidenceCandidate]:
    found: list[EvidenceCandidate] = []
    seen: set[tuple[int, int]] = set()
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(sentence):
            if match.span() in seen:
                continue
            seen.add(match.span())
            expression = match.group()
            due_date, resolution = _resolve_date(expression, reference_time, timezone_name)
            preceding_text = sentence[: match.start()]
            deadline_signal = bool(_DEADLINE_CUE.search(preceding_text)) and not bool(
                _NEGATED_DEADLINE.search(preceding_text[-80:])
            )
            found.append(
                EvidenceCandidate(
                    kind="deadline_candidate",
                    evidence=_snippet(sentence),
                    start=offset,
                    end=offset + len(sentence),
                    fields={
                        "expression": expression,
                        "due_date": due_date.isoformat() if due_date else None,
                        "resolution": resolution,
                        "deadline_signal": deadline_signal,
                    },
                )
            )
            if len(found) >= MAX_CANDIDATES:
                return found
    return found


def _entity_mentions(text: str) -> list[EntityMention]:
    patterns = (
        ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
        ("url", re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)),
        (
            "organization",
            re.compile(
                r"\b[A-Z][A-Za-z&'.-]*(?:\s+[A-Z][A-Za-z&'.-]*){0,3}\s+"
                r"(?:Inc\.?|Corp\.?|Corporation|LLC|Ltd\.?|University|College|Technologies|Company)\b"
            ),
        ),
        (
            "project",
            re.compile(
                r"\b(?i:project)\s+([A-Z][A-Za-z0-9'-]*(?:\s+[A-Z][A-Za-z0-9'-]*){0,2})"
            ),
        ),
        (
            "person",
            re.compile(
                r"\b(?i:with|to|from|by|contact|call|email)\s+"
                r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"
            ),
        ),
        (
            "location",
            re.compile(
                r"\b(?:in|at|near)\s+(Seattle|Boston|London|Paris|New York|San Francisco|Toronto|Delhi)\b",
                re.IGNORECASE,
            ),
        ),
    )
    mentions: dict[tuple[str, str], EntityMention] = {}
    for entity_type, pattern in patterns:
        for match in pattern.finditer(text):
            value = match.group(1) if match.lastindex else match.group()
            start = match.start(1) if match.lastindex else match.start()
            value = value.rstrip(".,;:!?)]}") if entity_type == "url" else value
            end = start + len(value)
            key = (entity_type, value.casefold())
            mentions.setdefault(
                key,
                EntityMention(
                    entity_type=entity_type,
                    canonical_name=value.rstrip(".,;"),
                    evidence=_snippet(text[max(0, start - 60) : min(len(text), end + 60)]),
                    start=start,
                    end=end,
                ),
            )
            if len(mentions) >= MAX_CANDIDATES:
                return list(mentions.values())
    return sorted(mentions.values(), key=lambda item: (item.start, item.entity_type, item.canonical_name))


def understand(
    text: str,
    *,
    reference_time: datetime | None = None,
    source_timezone: str | None = None,
) -> UnderstandingResult:
    if reference_time is not None and (
        reference_time.tzinfo is None or reference_time.utcoffset() is None
    ):
        raise ValueError("reference_time must include a timezone")
    if source_timezone is not None:
        try:
            ZoneInfo(source_timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("source_timezone must be a valid IANA timezone") from exc
    tasks: list[EvidenceCandidate] = []
    deadlines: list[EvidenceCandidate] = []
    events: list[EvidenceCandidate] = []
    relationships: list[EvidenceCandidate] = []
    facts: list[EvidenceCandidate] = []
    for sentence, start, _ in _sentences(text):
        if len(deadlines) < MAX_CANDIDATES:
            dates = _date_mentions(sentence, start, reference_time, source_timezone)
            deadlines.extend(dates[: MAX_CANDIDATES - len(deadlines)])
        # Generic email boilerplate such as “Please Check.” is not a user task.
        # Keep concrete action sentences, but do not turn a bare verb into a task.
        concrete_task = (
            bool(_TASK_START.search(sentence))
            and not bool(_GENERIC_TASK_ONLY.fullmatch(sentence.strip()))
            and not bool(re.fullmatch(r"(?i:email)\s*=\s*[^\s]+", sentence.strip()))
        )
        if len(tasks) < MAX_CANDIDATES and concrete_task and len(sentence.split()) >= 3:
            tasks.append(
                EvidenceCandidate(
                    kind="task_candidate",
                    evidence=_snippet(sentence),
                    start=start,
                    end=start + len(sentence),
                    fields={"description": sentence.rstrip(".!?")},
                )
            )
        if len(events) < MAX_CANDIDATES and re.search(
            r"\b(meeting|interview|appointment|webinar|class)\b", sentence, re.I
        ):
            events.append(
                EvidenceCandidate(
                    kind="event_candidate",
                    evidence=_snippet(sentence),
                    start=start,
                    end=start + len(sentence),
                    fields={"description": sentence.rstrip(".!?")},
                )
            )
        status_fact = re.search(
            r"\b(?i:project|task|application)\s+"
            r"([A-Z][A-Za-z0-9'-]*(?:\s+[A-Z][A-Za-z0-9'-]*){0,2})\s+"
            r"(?i:is|has status)\s+"
            r"(?i:(blocked|completed|pending|approved|rejected|submitted))\b",
            sentence,
        )
        if len(facts) < MAX_CANDIDATES and status_fact:
            facts.append(
                EvidenceCandidate(
                    kind="explicit_status_fact",
                    evidence=_snippet(sentence),
                    start=start,
                    end=start + len(sentence),
                    fields={
                        "subject": status_fact.group(1),
                        "status": status_fact.group(2).casefold(),
                    },
                )
            )
        relation = re.search(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s+(?i:works?)\s+(?i:at|for)\s+"
            r"([A-Z][A-Za-z&'.-]*(?:\s+[A-Z][A-Za-z&'.-]*){0,3}\s+"
            r"(?:Inc\.?|Corp\.?|Corporation|LLC|Ltd\.?|University|College|Technologies|Company))\b",
            sentence,
        )
        if len(relationships) < MAX_CANDIDATES and relation:
            relationships.append(
                EvidenceCandidate(
                    kind="relationship_candidate",
                    evidence=_snippet(sentence),
                    start=start,
                    end=start + len(sentence),
                    fields={
                        "subject": relation.group(1),
                        "relation_type": "works_for",
                        "object": relation.group(2),
                    },
                )
            )
    return UnderstandingResult(
        tasks=tuple(tasks),
        deadlines=tuple(deadlines),
        events=tuple(events),
        relationships=tuple(relationships),
        facts=tuple(facts),
        entities=tuple(_entity_mentions(text)),
    )
