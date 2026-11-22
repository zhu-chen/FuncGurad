"""TraceEvent 写入器：严格按 shared/contracts/trace_event.schema.json 输出。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_TYPES = {
    "run_start",
    "knowledge_search",
    "repo_tool",
    "llm_step",
    "decision",
    "report",
    "run_end",
    "error",
}


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class TraceWriter:
    """同一 run 内单调递增 seq，并逐条 flush 到 JSONL。"""

    def __init__(self, run_id: str, path: Path | None = None) -> None:
        self.run_id = run_id
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)

    def emit(
        self,
        event_type: str,
        status: str = "ok",
        name: str | None = None,
        payload: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> dict[str, Any]:
        if event_type not in ALLOWED_TYPES:
            raise ValueError(f"TraceEvent 非法 type: {event_type}")
        if status not in ("ok", "error"):
            raise ValueError(f"TraceEvent 非法 status: {status}")

        event: dict[str, Any] = {
            "run_id": self.run_id,
            "seq": self._seq,
            "type": event_type,
            "status": status,
            "timestamp": _now(),
        }
        self._seq += 1
        if name:
            event["name"] = name
        if duration_ms is not None:
            event["duration_ms"] = round(max(0.0, duration_ms), 2)
        if payload is not None:
            event["payload"] = payload

        self.events.append(event)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event
