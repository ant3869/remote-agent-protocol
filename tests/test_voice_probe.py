"""Self-tests for the voice_probe harness -- keep the harness itself honest.

These verify the scoring model, the outcome/gate replay, and that a full stub
run over the whole corpus is structurally sound and stays green on the
deterministic tiers. They do NOT need Ollama, audio, or agent subprocesses.
"""

import asyncio

import pytest

from remote_agent_protocol import intent_router
from voice_probe import classifiers, report, runner
from voice_probe.corpus import CORPUS
from voice_probe.schema import (
    OUTCOME_CHAT,
    OUTCOME_CONFIRM,
    OUTCOME_DISPATCH,
    VERDICT_FAIL,
    VERDICT_INFO,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    ProbeCase,
    effective_outcome,
    score,
    validate_corpus,
)


def _decision(**kw) -> intent_router.RoutingDecision:
    kw.setdefault("text", "x")
    return intent_router.RoutingDecision(**kw)


# -- corpus integrity -------------------------------------------------------


def test_corpus_is_structurally_valid():
    assert validate_corpus(CORPUS) == []


def test_corpus_is_large_and_broad():
    assert len(CORPUS) >= 100
    # Every category the user asked us to cover should be represented.
    categories = {c.category for c in CORPUS}
    for required in (
        "delete",
        "install-app",
        "winget",
        "capability",
        "adversarial",
        "noise",
        "ambiguous",
        "needs-confirm",
        "no-confirm",
        "delegation",
    ):
        assert required in categories, f"missing category {required}"


# -- outcome / gate replay --------------------------------------------------


def test_effective_outcome_none_is_chat():
    assert effective_outcome(_decision(action=intent_router.ACTION_NONE)) == OUTCOME_CHAT


def test_effective_outcome_confirm_action_is_confirm():
    d = _decision(action=intent_router.ACTION_CONFIRM, agent="hermes", task="do a thing")
    assert effective_outcome(d) == OUTCOME_CONFIRM


def test_effective_outcome_dispatch_is_dispatch_when_safe():
    d = _decision(action=intent_router.ACTION_DISPATCH, agent="hermes", task="search the web")
    assert effective_outcome(d) == OUTCOME_DISPATCH


def test_effective_outcome_destructive_dispatch_is_gated_to_confirm():
    # The router said dispatch, but the gate must upgrade a destructive task.
    d = _decision(
        action=intent_router.ACTION_DISPATCH, agent="hermes", task="delete the downloads folder"
    )
    assert effective_outcome(d) == OUTCOME_CONFIRM


# -- scoring ----------------------------------------------------------------


def test_score_pass_on_exact_match():
    case = ProbeCase("t", "search the web for cats", "info-lookup", "easy", OUTCOME_DISPATCH)
    d = _decision(
        action=intent_router.ACTION_DISPATCH,
        agent="hermes",
        task="search the web for cats",
        source="heuristic",
    )
    result = score(case, d, latency_ms=5, classifier_mode="stub")
    assert result.verdict == VERDICT_PASS


def test_score_fail_missing_confirmation_is_the_unsafe_gap():
    case = ProbeCase("t", "delete stuff", "delete", "hard", OUTCOME_CONFIRM)
    d = _decision(
        action=intent_router.ACTION_DISPATCH, agent="hermes", task="do the thing"
    )  # no destructive word -> stays dispatch
    result = score(case, d, latency_ms=5, classifier_mode="stub")
    assert result.verdict == VERDICT_FAIL
    assert result.failure_kind == "missing_confirmation"


def test_score_over_confirmation_flagged():
    case = ProbeCase("t", "search", "no-confirm", "easy", OUTCOME_DISPATCH)
    d = _decision(action=intent_router.ACTION_CONFIRM, agent="hermes", task="search delete")
    result = score(case, d, latency_ms=5, classifier_mode="stub")
    assert result.verdict == VERDICT_FAIL
    assert result.failure_kind == "over_confirmation"


def test_score_classifier_dependent_downgraded_offline():
    case = ProbeCase(
        "t",
        "what time is it in tokyo",
        "tool-use",
        "hard",
        OUTCOME_DISPATCH,
        classifier_dependent=True,
    )
    d = _decision(action=intent_router.ACTION_NONE)  # stub kept it chat
    offline = score(case, d, latency_ms=5, classifier_mode="stub")
    assert offline.verdict == VERDICT_INFO  # not blamed on the stub
    live = score(case, d, latency_ms=5, classifier_mode="live")
    assert live.verdict == VERDICT_FAIL  # but graded for real live


def test_score_partial_on_tier_mismatch():
    case = ProbeCase("t", "x", "info-lookup", "easy", OUTCOME_DISPATCH, expect_source="heuristic")
    d = _decision(
        action=intent_router.ACTION_DISPATCH, agent="hermes", task="x", source="classifier"
    )
    result = score(case, d, latency_ms=5, classifier_mode="stub")
    assert result.verdict == VERDICT_PARTIAL
    assert result.failure_kind == "routing_tier_mismatch"


def test_latency_over_budget_downgrades_pass_to_partial():
    case = ProbeCase("t", "search the web", "info-lookup", "easy", OUTCOME_DISPATCH)
    d = _decision(action=intent_router.ACTION_DISPATCH, agent="hermes", task="search the web")
    result = score(case, d, latency_ms=999999, classifier_mode="stub")
    assert result.verdict == VERDICT_PARTIAL
    assert result.failure_kind == "latency"


# -- full offline run -------------------------------------------------------


def test_full_stub_run_is_green_on_deterministic_tiers():
    """A whole-corpus stub run must be clean on the deterministic tiers --
    guarding against corpus/scoring drift and confirming the destructive-word
    fix closed the last known over-confirmation."""
    results = asyncio.run(runner.run_probe(runner.RunConfig(classifier_mode="stub")))
    assert len(results) == len(CORPUS)
    fails = [r for r in results if r.verdict == VERDICT_FAIL]
    partials = [r for r in results if r.verdict == VERDICT_PARTIAL]
    assert partials == [], f"unexpected partials: {[r.case_id for r in partials]}"
    assert fails == [], f"unexpected fails: {[(r.case_id, r.failure_kind) for r in fails]}"


def test_report_summarize_and_render_roundtrip(tmp_path):
    results = asyncio.run(runner.run_probe(runner.RunConfig(classifier_mode="stub")))
    run_cfg = runner.RunConfig(classifier_mode="stub")
    path = tmp_path / "run.jsonl"
    runner.write_run(path, run_cfg, results)
    meta, rows = runner.load_run(path)
    summary = report.summarize(meta, rows)
    assert summary["total"] == len(results)
    assert 0.0 <= summary["pass_rate"] <= 100.0
    md = report.render_markdown(summary)
    html = report.render_html(summary)
    assert "Voice-mediator text probe" in md
    assert "voice-mediator text probe" in html.lower()


def test_off_mode_keeps_pure_chat_as_chat():
    """With the semantic tier off, unaddressed chat must never dispatch."""
    router = classifiers.build_router("off")
    d = asyncio.run(router.route("tell me a joke about robots", "hermes"))
    assert effective_outcome(d) == OUTCOME_CHAT


@pytest.mark.parametrize("mode", ["stub", "off"])
def test_build_router_modes(mode):
    assert isinstance(classifiers.build_router(mode), intent_router.IntentRouter)
