"""TelemetryRecorder -- orchestration metrics, kept separate from persona memory.

Appends one JSON object per line to ``cfg.ORCHESTRATION_TELEMETRY_FILE``
(never mem0/jess_memory.json -- this is operational telemetry, not
conversation the persona recalls). :meth:`summary` never invents a derived
statistic it has no rows to support; anything unobserved is ``None``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from loguru import logger


@dataclass
class TelemetryEvent:
    """One recorded moment in the UNDERSTAND->ROUTE->DISPATCH->TRACK->INTERPRET->RELAY lifecycle."""

    stage: str  # "route" | "outcome"
    route: str = ""  # "local" | "cloud"
    provider: str = ""
    model: str = ""
    persona: str = ""
    intent: str = ""
    harness: str = ""
    original_harness: str = ""  # the pre-cloud-refinement pick ("route" stage)
    risk_score: float | None = None
    risk_factors: dict[str, float] | None = None  # the 8 RiskFactors values ("route" stage)
    reason: str = ""  # StructuredDecision.reason_summary -- why local/cloud ("route" stage)
    intent_confidence: float | None = None
    routing_confidence: float | None = None
    route_latency_ms: float | None = None  # time spent in evaluate() itself ("route" stage)
    latency_ms: float | None = None  # harness job wall time ("outcome" stage)
    outcome: str = ""  # "success" | "failed" | "cancelled" | ""
    escalated: bool = False
    fallback: bool = False
    wrong_harness_corrected: bool = False
    duplicate_blocked: bool = False
    timestamp: float = field(default_factory=time.time)


class TelemetryRecorder:
    """Appends orchestration events to a JSONL file and derives summary stats."""

    def __init__(self, path: str | Path) -> None:
        """Initialize the recorder, creating the parent directory if needed."""
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: TelemetryEvent) -> None:
        """Append one event as a JSON line; failures are logged, never raised."""
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(f"orchestration telemetry write failed: {exc}")

    def _rows(self) -> list[dict]:
        if not self._path.exists():
            return []
        rows: list[dict] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def recent(self, limit: int = 20) -> list[dict]:
        """The most recent recorded events, newest first -- for live inspection."""
        return list(reversed(self._rows()[-limit:]))

    def summary(self) -> dict[str, float | None]:
        """Derived stats, each ``None`` when there are no rows to compute it from."""
        keys = (
            "local_resolution_pct",
            "cloud_escalation_pct",
            "unnecessary_delegation_pct",
            "wrong_harness_correction_pct",
            "routing_failure_pct",
            "fallback_pct",
            "local_success_pct",
            "cloud_success_pct",
            "avg_latency_ms",
            "avg_local_route_latency_ms",
            "avg_cloud_route_latency_ms",
        )
        all_rows = self._rows()
        result: dict[str, float | None] = dict.fromkeys(keys)

        # Route-stage stats (reasoning time) exist as soon as any turn has been
        # routed, independent of whether its job has finished yet.
        route_rows = [r for r in all_rows if r.get("stage") == "route"]
        result["avg_local_route_latency_ms"] = self._avg(
            [r for r in route_rows if r.get("route") == "local"], "route_latency_ms"
        )
        result["avg_cloud_route_latency_ms"] = self._avg(
            [r for r in route_rows if r.get("route") == "cloud"], "route_latency_ms"
        )

        # Outcome-stage stats (success/failure/latency) exist only for turns
        # whose dispatched job has actually reached a terminal state.
        outcome_rows = [r for r in all_rows if r.get("stage") == "outcome"]
        if not outcome_rows:
            return result
        total = len(outcome_rows)

        def pct(predicate) -> float:
            return round(100.0 * sum(1 for r in outcome_rows if predicate(r)) / total, 1)

        def success_pct(subset: list[dict]) -> float | None:
            if not subset:
                return None
            return round(
                100.0 * sum(1 for r in subset if r.get("outcome") == "success") / len(subset), 1
            )

        local_rows = [r for r in outcome_rows if r.get("route") == "local"]
        cloud_rows = [r for r in outcome_rows if r.get("route") == "cloud"]
        result.update(
            {
                "local_resolution_pct": pct(lambda r: r.get("route") == "local"),
                "cloud_escalation_pct": pct(lambda r: r.get("route") == "cloud"),
                "unnecessary_delegation_pct": pct(lambda r: r.get("outcome") == "cancelled"),
                "wrong_harness_correction_pct": pct(lambda r: r.get("wrong_harness_corrected")),
                "routing_failure_pct": pct(lambda r: r.get("outcome") == "failed"),
                "fallback_pct": pct(lambda r: r.get("fallback")),
                "local_success_pct": success_pct(local_rows),
                "cloud_success_pct": success_pct(cloud_rows),
                "avg_latency_ms": self._avg(outcome_rows, "latency_ms"),
            }
        )
        return result

    @staticmethod
    def _avg(rows: list[dict], field_name: str) -> float | None:
        values = [r[field_name] for r in rows if isinstance(r.get(field_name), (int, float))]
        return round(sum(values) / len(values), 1) if values else None
