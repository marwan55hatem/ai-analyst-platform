"""Helpers for chat tests: SSE parsing, event contract checks, fake sandbox, chat setup."""

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from tests.dataset_helpers import csv_bytes, sample, upload_ready
from tests.helpers import API, Account

EVENT_FIELDS = {
    "token": {"text"},
    "tool_call": {"id", "name", "arguments"},
    "tool_result": {"id", "name", "ok", "summary", "evidence"},
    "chart": {"id", "spec"},
    "caveat": {"kind", "message"},
    "final": {
        "text",
        "model",
        "scripted",
        "untraced_numbers",
        "tool_calls",
        "usage",
        "cost_usd_approx",
        "cost_is_approximate",
        "message_id",
    },
    "error": {"code", "message"},
}


@dataclass
class Event:
    """One parsed SSE event."""

    type: str
    data: dict[str, Any]


def parse_sse(text: str) -> list[Event]:
    """Parse an SSE body and check every event against the published contract."""
    events: list[Event] = []
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        lines = frame.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: "), frame
        kind = lines[0][len("event: ") :]
        data = json.loads(lines[1][len("data: ") :])
        assert kind in EVENT_FIELDS, kind
        assert EVENT_FIELDS[kind] <= set(data), (kind, data)
        events.append(Event(kind, data))
    return events


def ask(client: TestClient, account: Account, conversation_id: str, message: str) -> list[Event]:
    """POST a question to the SSE endpoint and return its parsed events."""
    response = client.post(
        f"{API}/chat/stream",
        headers=account.headers,
        json={"conversation_id": conversation_id, "message": message},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def kinds(events: list[Event], skip_tokens: bool = True) -> list[str]:
    """Return the event types in order (tokens collapsed)."""
    out: list[str] = []
    for event in events:
        if skip_tokens and event.type == "token":
            if not out or out[-1] != "token*":
                out.append("token*")
            continue
        out.append(event.type)
    return out


def conversation(client: TestClient, account: Account, dataset_id: str) -> str:
    """Create a conversation and return its id."""
    response = client.post(
        f"{API}/conversations", headers=account.headers, json={"dataset_id": dataset_id}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def sample_chat(client: TestClient, account: Account) -> tuple[str, str]:
    """Upload the small clean sample and open a conversation on it: (dataset id, conversation id)."""
    dataset = upload_ready(client, account, sample("retail_clean.csv"), "retail.csv")
    return dataset["id"], conversation(client, account, dataset["id"])


def pii_csv() -> bytes:
    """A small sales file with an email column and an email-like customer id."""
    rows = []
    for i in range(60):
        rows.append(
            [
                f"2024-{1 + i % 12:02d}-{1 + i % 27:02d}",
                f"buyer{i % 9}@example.com",
                f"+44 7700 9000{i % 9:02d}",
                ["Pen", "Paper", "Desk"][i % 3],
                str(1 + i % 4),
                str(round(2.5 + i % 5, 2)),
            ]
        )
    return csv_bytes(
        rows, ["order_date", "customer_email", "phone", "product", "quantity", "unit_price"]
    )


@dataclass
class FakeSandbox:
    """Records what would have been sent to the sandbox and answers with a canned result."""

    answer: dict[str, Any] = field(
        default_factory=lambda: {"ok": True, "result": 42, "stdout": "", "duration_ms": 3}
    )
    calls: list[Any] = field(default_factory=list)

    def run(self, code: str, data: Any) -> dict[str, Any]:
        self.calls.append((code, data))
        return self.answer
