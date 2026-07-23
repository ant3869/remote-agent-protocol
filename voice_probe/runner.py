"""The probe engine: feed every prompt through the mediator, score, log.

This drives the *decision brain* directly -- :class:`IntentRouter.route` plus
the confirmation gate replayed in :func:`schema.effective_outcome` -- which is
the same brain ``VoiceSession.send_text`` invokes for typed input and the audio
path invokes for speech. It intentionally does NOT boot the audio pipeline
(mic/STT/TTS/speakers) or spawn real agent subprocesses: those are slow,
non-deterministic, and orthogonal to the routing/delegation/confirmation
behavior this harness exists to probe. The optional live end-to-end path lives
separately (see the README) for when you want to watch a real job run.

Output is a JSONL run file (one :class:`ProbeResult` per line) plus a small
run-metadata header line, consumed by :mod:`voice_probe.report`.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from remote_agent_protocol import config as cfg
from remote_agent_protocol import intent_router
from voice_probe import classifiers
from voice_probe.corpus import CORPUS
from voice_probe.schema import ProbeCase, ProbeResult, score


@dataclass
class RunConfig:
    """Everything that parameterizes one probe run."""

    classifier_mode: str = "stub"  # live | stub | off
    default_backend: str = cfg.AGENT_DEFAULT_BACKEND
    categories: tuple[str, ...] = ()  # empty == all
    difficulties: tuple[str, ...] = ()  # empty == all
    repeats: int = 1  # re-run each case N times (surfaces classifier nondeterminism)
    model: str = ""  # tier-6 classifier tag override (live only); "" == cfg.INTENT_MODEL
    timeout_secs: float = 0.0  # per-utterance classifier budget; 0 == cfg.INTENT_TIMEOUT_SECS

    def classifier_model(self) -> str:
        """The classifier model tag this run actually uses."""
        return self.model or cfg.INTENT_MODEL

    def classifier_timeout(self) -> float:
        """The classifier timeout this run actually uses."""
        return self.timeout_secs or cfg.INTENT_TIMEOUT_SECS


def select_cases(cases: list[ProbeCase], run: RunConfig) -> list[ProbeCase]:
    """Filter the corpus by the run's category / difficulty selectors."""
    selected = cases
    if run.categories:
        wanted = {c.lower() for c in run.categories}
        selected = [c for c in selected if c.category.lower() in wanted]
    if run.difficulties:
        wanted = {d.lower() for d in run.difficulties}
        selected = [c for c in selected if c.difficulty.lower() in wanted]
    return selected


async def _probe_one(
    router: intent_router.IntentRouter, case: ProbeCase, run: RunConfig
) -> ProbeResult:
    """Route one prompt, timing it, and score the decision (or the crash)."""
    t0 = time.perf_counter()
    try:
        decision = await router.route(case.prompt, run.default_backend)
    except Exception as exc:  # a routing crash is itself a finding, not a stop
        latency_ms = int((time.perf_counter() - t0) * 1000)
        result = ProbeResult(
            case_id=case.id,
            prompt=case.prompt,
            category=case.category,
            difficulty=case.difficulty,
            classifier_mode=run.classifier_mode,
            latency_ms=latency_ms,
            expect_outcome=case.expect_outcome,
            note=case.note,
            verdict="fail",
            failure_kind="routing_crash",
            failure_detail=f"{type(exc).__name__}: {exc}",
            error=repr(exc),
        )
        result.timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        return result

    latency_ms = int((time.perf_counter() - t0) * 1000)
    result = score(
        case,
        decision,
        latency_ms=latency_ms,
        classifier_mode=run.classifier_mode,
    )
    result.timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    return result


async def run_probe(run: RunConfig, cases: list[ProbeCase] | None = None) -> list[ProbeResult]:
    """Execute a full probe run in order and return every result.

    Cases run sequentially on purpose: the live classifier keeps one small model
    resident and parallel calls would just contend for it, and sequential order
    keeps the JSONL and any live logs readable turn by turn.
    """
    corpus = cases if cases is not None else CORPUS
    selected = select_cases(corpus, run)
    router = classifiers.build_router(
        run.classifier_mode,
        model=run.classifier_model(),
        timeout_secs=run.classifier_timeout(),
    )

    if run.classifier_mode == "live":
        # Pay the cold-load once so the first real case isn't penalized for it.
        await router.warmup()

    results: list[ProbeResult] = []
    for case in selected:
        for _ in range(max(1, run.repeats)):
            results.append(await _probe_one(router, case, run))
    return results


def default_run_path(mode: str, model: str = "") -> Path:
    """A timestamped run-file path under data/voice_probe/.

    The model tag is folded into the filename for ``live`` runs so back-to-back
    model comparisons don't overwrite each other.
    """
    out_dir = cfg.DATA_DIR / "voice_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = ""
    if mode == "live" and model:
        # Sanitize the tag for a filename (drop ":latest", slashes, etc.).
        safe = model.replace(":", "-").replace("/", "-").replace("\\", "-")
        tag = f"-{safe}"
    return out_dir / f"run-{mode}{tag}-{stamp}.jsonl"


def write_run(path: Path, run: RunConfig, results: list[ProbeResult]) -> None:
    """Write a JSONL run file: a metadata header line, then one result per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "_meta": True,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "classifier_mode": run.classifier_mode,
        "default_backend": run.default_backend,
        "intent_model": run.classifier_model(),
        "classifier_timeout_secs": run.classifier_timeout(),
        "dispatch_confidence": cfg.INTENT_DISPATCH_CONFIDENCE,
        "confirm_confidence": cfg.INTENT_CONFIRM_CONFIDENCE,
        "destructive_words": list(cfg.AGENT_DESTRUCTIVE_WORDS),
        "case_count": len({r.case_id for r in results}),
        "result_count": len(results),
    }
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header) + "\n")
        for result in results:
            fh.write(json.dumps(result.as_row(), ensure_ascii=False) + "\n")


def load_run(path: Path) -> tuple[dict, list[dict]]:
    """Read a JSONL run file back into (metadata, rows)."""
    meta: dict = {}
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("_meta"):
            meta = obj
        else:
            rows.append(obj)
    return meta, rows


def run_and_save(run: RunConfig, path: Path | None = None) -> Path:
    """Convenience: run the probe and persist it, returning the run-file path."""
    results = asyncio.run(run_probe(run))
    out = path or default_run_path(run.classifier_mode)
    write_run(out, run, results)
    return out
