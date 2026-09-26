"""Model-chain failover: a job that fails on one model is retried on the next.

The fake harness is a Python one-liner. Model targets pass ``-X model=<name>``,
which Python accepts on every platform and exposes as ``sys._xoptions``, so the
script can fail or succeed depending on which model the bridge launched it with.
"""

import asyncio

from remote_agent_protocol import agent_bridge

_SCRIPT = (
    "import sys; model = sys._xoptions.get('model', 'default'); "
    "bad = {'broke': 'Error: insufficient_quota: out of credits', "
    "'locked': 'Error: 401 Unauthorized: invalid api key', "
    "'missing': '404 model_name:', "
    "'throttled': 'Error: 429 too many requests, rate limit hit'}; "
    "line = bad.get(model); "
    "print(line if line else 'answered by ' + model, flush=True); "
    "sys.exit(1 if line else 0)"
)
_BACKENDS = {"worker": ["{python}", "-u", "-c", _SCRIPT, "{task}"]}


def _target(model: str) -> dict:
    return {"label": f"Model {model}", "args": ["-X", f"model={model}"]}


_TARGETS = {
    "worker": {
        name: _target(name) for name in ("broke", "locked", "missing", "throttled", "good", "spare")
    }
}


def _run_job(chain: list[str] | None, *, default: str | None = None, runs: int = 1):
    events: list[dict] = []
    finished: list[agent_bridge.AgentJob] = []

    async def on_finished(job):
        finished.append(job)

    async def scenario():
        bridge = agent_bridge.AgentBridge(
            _BACKENDS,
            events.append,
            on_finished=on_finished,
            model_targets=_TARGETS,
            default_model_targets={"worker": default} if default else None,
            model_chains={"worker": chain} if chain else None,
        )
        jobs = []
        for _ in range(runs):
            job_id = await bridge.start("worker", "do the thing")
            # Generous: each attempt starts a Python interpreter, which is slow
            # when the whole suite runs in parallel with it.
            for _ in range(3000):
                job = bridge.get(job_id)
                if job.status not in {agent_bridge.STATUS_RUNNING, agent_bridge.STATUS_WAITING}:
                    break
                await asyncio.sleep(0.02)
            jobs.append(bridge.get(job_id))
        return bridge, jobs

    bridge, jobs = asyncio.run(scenario())
    return bridge, jobs, events, finished


def test_model_not_found_is_classified():
    assert agent_bridge.detect_provider_failure("404 model_name:") == "model_not_found"
    assert (
        agent_bridge.detect_provider_failure("Error: The model `gpt-9` does not exist")
        == "model_not_found"
    )
    assert agent_bridge.detect_provider_failure("I looked for the model but it was fine") is None


def test_quota_failure_moves_to_the_next_model_and_finishes_once():
    bridge, (job,), events, finished = _run_job(["broke", "good"])

    assert job.status == agent_bridge.STATUS_DONE
    assert job.model_label == "Model good"
    assert "answered by good" in job.result or "answered by good" in job.summary
    assert job.model_failovers == ["Model broke: quota"]
    assert [event["event"] for event in events].count("finished") == 1
    assert len(finished) == 1
    failover = next(event for event in events if event["event"] == "model_failover")
    assert (failover["from_model"], failover["to_model"]) == ("Model broke", "Model good")
    assert failover["status"] == agent_bridge.STATUS_RUNNING


def test_auth_and_model_not_found_walk_the_whole_chain():
    _, (job,), _, _ = _run_job(["locked", "missing", "good"])

    assert job.status == agent_bridge.STATUS_DONE
    assert job.model_failovers == ["Model locked: auth", "Model missing: model not found"]


def test_the_model_that_worked_becomes_the_default_for_later_jobs():
    bridge, (first, second), _, _ = _run_job(["broke", "good"], runs=2)

    assert first.model_failovers == ["Model broke: quota"]
    assert second.model_failovers == []
    assert second.model_label == "Model good"
    assert bridge._model_providers["worker"] == "good"


def test_an_exhausted_chain_reports_the_last_failure():
    _, (job,), events, finished = _run_job(["broke", "locked"])

    assert job.status == agent_bridge.STATUS_FAILED
    assert job.failure_kind == "auth"
    assert job.model_failovers == ["Model broke: quota"]
    assert [event["event"] for event in events].count("finished") == 1
    assert len(finished) == 1


def test_rate_limits_do_not_switch_models():
    _, (job,), events, _ = _run_job(["throttled", "good"])

    assert job.status == agent_bridge.STATUS_FAILED
    assert job.failure_kind == "rate_limit"
    assert job.model_failovers == []
    assert not any(event["event"] == "model_failover" for event in events)


def test_no_chain_means_no_failover():
    _, (job,), _, _ = _run_job(None, default="broke")

    assert job.status == agent_bridge.STATUS_FAILED
    assert job.failure_kind == "quota"
    assert job.model_failovers == []


def test_default_model_target_wins_over_the_chain_head():
    bridge, (job,), _, _ = _run_job(["broke", "good"], default="spare")

    assert job.model_label == "Model spare"
    assert job.status == agent_bridge.STATUS_DONE
    assert bridge.model_chain("worker") == ["broke", "good"]


def test_unknown_chain_entries_are_skipped():
    bridge = agent_bridge.AgentBridge(
        _BACKENDS,
        lambda _event: None,
        model_targets=_TARGETS,
        model_chains={"worker": ["nope", "good"], "ghost": ["good"]},
    )

    assert bridge.model_chain("worker") == ["good"]
    assert bridge.model_chain("ghost") == []
    assert bridge._model_labels["worker"] == "Model good"


def test_internal_jobs_never_fail_over():
    events: list[dict] = []

    async def scenario():
        bridge = agent_bridge.AgentBridge(
            _BACKENDS,
            events.append,
            model_targets=_TARGETS,
            model_chains={"worker": ["broke", "good"]},
        )
        job_id = await bridge.start("worker", "probe", internal=True)
        for _ in range(3000):
            if bridge.get(job_id).status != agent_bridge.STATUS_RUNNING:
                break
            await asyncio.sleep(0.02)
        return bridge.get(job_id)

    job = asyncio.run(scenario())
    assert job.status == agent_bridge.STATUS_FAILED
    assert job.model_failovers == []


def test_a_job_never_looks_finished_between_attempts():
    # The quota branch stops the process before the failover; while it waits
    # for the exit the job must stay active, or a cancel in that window is
    # dropped and anything polling status sees a failure that isn't final.
    # Ignoring SIGTERM (POSIX) holds the process through the bridge's kill
    # grace, which is the window being tested.
    stalling = (
        "import signal, sys, time; model = sys._xoptions.get('model'); "
        "hasattr(signal, 'SIGTERM') and signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('Error: insufficient_quota' if model == 'broke' else 'ok', flush=True); "
        "time.sleep(30 if model == 'broke' else 0)"
    )
    seen: list[str] = []

    async def scenario():
        bridge = agent_bridge.AgentBridge(
            {"worker": ["{python}", "-u", "-c", stalling, "{task}"]},
            lambda _event: None,
            kill_grace_secs=0.5,
            model_targets=_TARGETS,
            model_chains={"worker": ["broke", "good"]},
        )
        job_id = await bridge.start("worker", "do the thing")
        for _ in range(6000):
            job = bridge.get(job_id)
            seen.append(job.status)
            if job.status == agent_bridge.STATUS_DONE:
                break
            await asyncio.sleep(0.005)
        return bridge.get(job_id)

    job = asyncio.run(scenario())
    assert job.status == agent_bridge.STATUS_DONE
    assert job.model_failovers == ["Model broke: quota"]
    assert agent_bridge.STATUS_FAILED not in seen
