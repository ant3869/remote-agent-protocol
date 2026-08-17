r"""CLI: run the probe, report on a run, or list the corpus.

    .venv\\Scripts\\python -m voice_probe run [--classifier stub|live|off] [options]
    .venv\\Scripts\\python -m voice_probe report [RUN.jsonl]
    .venv\\Scripts\\python -m voice_probe list [--category X] [--difficulty Y]

``run`` executes the corpus and writes a JSONL run file plus Markdown + HTML
reports next to it under ``data/voice_probe/`` (unless ``--out`` is given), then
prints the scoreboard. ``report`` re-renders an existing run file. ``list``
dumps the selected corpus without running anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from voice_probe import report as report_mod
from voice_probe import runner
from voice_probe.corpus import CORPUS
from voice_probe.schema import (
    VERDICT_FAIL,
    VERDICT_INFO,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    validate_corpus,
)

# Committed expectations per classifier, so a routing change that lowers the
# score fails loudly instead of quietly. Live runs are graded against a small
# model's judgment, which is why they have their own bar.
BASELINE_PATH = Path(__file__).with_name("baseline.json")


def _add_selectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--category", action="append", default=[], help="restrict to a category (repeatable)"
    )
    parser.add_argument(
        "--difficulty",
        action="append",
        default=[],
        choices=["easy", "medium", "hard", "brutal"],
        help="restrict to a difficulty (repeatable)",
    )


def _cmd_run(args: argparse.Namespace) -> int:
    problems = validate_corpus(CORPUS)
    if problems:
        print("Corpus is malformed:", *problems, sep="\n  ", file=sys.stderr)
        return 2

    run = runner.RunConfig(
        classifier_mode=args.classifier,
        default_backend=args.backend,
        categories=tuple(args.category),
        difficulties=tuple(args.difficulty),
        repeats=args.repeats,
        model=args.model,
        timeout_secs=args.timeout,
    )
    selected = runner.select_cases(CORPUS, run)
    if not selected:
        print("No cases matched the selectors.", file=sys.stderr)
        return 2
    detail = (
        f"model '{run.classifier_model()}' (budget {run.classifier_timeout():.0f}s)"
        if run.classifier_mode == "live"
        else run.classifier_mode
    )
    print(
        f"Probing {len(selected)} case(s) x{run.repeats} "
        f"via classifier {detail} (backend '{run.default_backend}')..."
    )

    out = (
        Path(args.out)
        if args.out
        else runner.default_run_path(run.classifier_mode, run.classifier_model())
    )
    path = runner.run_and_save(run, out)

    meta, rows = runner.load_run(path)
    summary = report_mod.summarize(meta, rows)
    md_path = path.with_suffix(".md")
    html_path = path.with_suffix(".html")
    report_mod.write_reports(summary, md_path, html_path)

    _print_scoreboard(summary)
    print(f"\nRun:    {path}")
    print(f"Report: {md_path}")
    print(f"HTML:   {html_path}")
    return _gate(summary, args)


def _cmd_report(args: argparse.Namespace) -> int:
    path = Path(args.run)
    if not path.exists():
        print(f"No such run file: {path}", file=sys.stderr)
        return 2
    meta, rows = runner.load_run(path)
    summary = report_mod.summarize(meta, rows)
    md_path = path.with_suffix(".md")
    html_path = path.with_suffix(".html")
    report_mod.write_reports(summary, md_path, html_path)
    _print_scoreboard(summary)
    print(f"\nReport: {md_path}\nHTML:   {html_path}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    run = runner.RunConfig(categories=tuple(args.category), difficulties=tuple(args.difficulty))
    selected = runner.select_cases(CORPUS, run)
    for case in selected:
        exp = case.expect_outcome or "-"
        tag = " [clf]" if case.classifier_dependent else ""
        print(
            f"{case.id:28} {case.category:16} {case.difficulty:7} "
            f"expect={exp:9}{tag}  {case.prompt[:70]}"
        )
    print(f"\n{len(selected)} case(s).")
    return 0


def _baseline_key(summary: dict) -> str:
    """Baselines are per classifier: a stub run and a live model are different bars."""
    meta = summary.get("meta", {})
    mode = str(meta.get("classifier_mode") or "stub")
    model = str(meta.get("intent_model") or "")
    return f"{mode}:{model}" if mode == "live" and model else mode


def _gate(summary: dict, args: argparse.Namespace) -> int:
    """Decide the exit code, and record or compare a baseline when asked.

    The default stays "any failure fails the run", which is right for the
    offline corpus. A live classifier is graded against a small model's
    judgment, so a handful of failures is its steady state -- pinning to zero
    there would make the gate useless. `--fail-under` and `--baseline` express
    the bar that actually matters: did this change make it worse?
    """
    rate = float(summary["pass_rate"])
    key = _baseline_key(summary)
    if args.update_baseline:
        _write_baseline(key, rate, Path(args.baseline or BASELINE_PATH))
    elif args.baseline:
        recorded = _read_baseline(key, Path(args.baseline))
        if recorded is None:
            print(f"\nNo baseline recorded for '{key}'; run again with --update-baseline.")
        else:
            floor = recorded - args.tolerance
            verdict = "within" if rate >= floor else "BELOW"
            print(
                f"\nBaseline '{key}': {recorded:.0f}% -> this run {rate:.0f}% "
                f"({verdict} the {args.tolerance:.0f} point tolerance)"
            )
            if rate < floor:
                return 1
    if args.fail_under is not None:
        return 1 if rate < args.fail_under else 0
    if args.baseline or args.update_baseline:
        return 0
    return 1 if summary["verdicts"].get(VERDICT_FAIL, 0) else 0


def _read_baseline(key: str, path: Path) -> float | None:
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = recorded.get(key) if isinstance(recorded, dict) else None
    rate = entry.get("pass_rate") if isinstance(entry, dict) else None
    return float(rate) if isinstance(rate, int | float) else None


def _write_baseline(key: str, rate: float, path: Path) -> None:
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(recorded, dict):
            recorded = {}
    except (OSError, ValueError):
        recorded = {}
    recorded[key] = {
        "pass_rate": round(rate, 1),
        "recorded_at": datetime.now().isoformat(" ", "seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(recorded.items())), indent=2) + "\n", encoding="utf-8")
    print(f"\nBaseline '{key}' recorded at {rate:.0f}% in {path}")


def _print_scoreboard(summary: dict) -> None:
    v = summary["verdicts"]
    print("\n=== Scoreboard ===")
    print(f"  pass    {v.get(VERDICT_PASS, 0)}")
    print(f"  partial {v.get(VERDICT_PARTIAL, 0)}")
    print(f"  FAIL    {v.get(VERDICT_FAIL, 0)}")
    print(f"  info    {v.get(VERDICT_INFO, 0)}")
    print(f"  pass rate (graded): {summary['pass_rate']:.0f}%")
    if summary["failure_counts"]:
        print("  failure modes:")
        for kind, count in summary["failure_counts"]:
            print(f"    {kind}: {count}")


def main(argv: list[str] | None = None) -> int:
    """Parse argv and dispatch to the selected subcommand."""
    parser = argparse.ArgumentParser(prog="voice_probe", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="run the corpus through the mediator")
    run_p.add_argument(
        "--classifier",
        choices=["stub", "live", "off"],
        default="stub",
        help="tier-6 classifier backend (default: stub, offline)",
    )
    run_p.add_argument(
        "--backend",
        default=runner.RunConfig().default_backend,
        help="default agent backend for dispatch decisions",
    )
    run_p.add_argument(
        "--model",
        default="",
        help="live only: tier-6 classifier model tag to benchmark (default: INTENT_MODEL)",
    )
    run_p.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="live only: per-utterance classifier budget in seconds "
        "(default: INTENT_TIMEOUT_SECS; raise for large models like hermes-20b)",
    )
    run_p.add_argument(
        "--repeats", type=int, default=1, help="re-run each case N times (surfaces nondeterminism)"
    )
    run_p.add_argument("--out", default="", help="explicit run-file path (.jsonl)")
    run_p.add_argument(
        "--fail-under",
        type=float,
        default=None,
        metavar="PCT",
        help="exit non-zero when the graded pass rate falls below PCT, rather than on "
        "any single failure -- this is the form to use as a regression gate",
    )
    run_p.add_argument(
        "--baseline",
        nargs="?",
        const=str(BASELINE_PATH),
        default="",
        metavar="PATH",
        help=f"compare the pass rate against a committed baseline (default {BASELINE_PATH.name})",
    )
    run_p.add_argument(
        "--tolerance",
        type=float,
        default=2.0,
        metavar="PTS",
        help="percentage points the pass rate may fall below the baseline (default 2)",
    )
    run_p.add_argument(
        "--update-baseline",
        action="store_true",
        help="record this run's pass rate as the new baseline for its classifier mode",
    )
    _add_selectors(run_p)
    run_p.set_defaults(func=_cmd_run)

    rep_p = sub.add_parser("report", help="re-render reports from a run file")
    rep_p.add_argument("run", help="path to a run .jsonl file")
    rep_p.set_defaults(func=_cmd_report)

    list_p = sub.add_parser("list", help="list the corpus without running")
    _add_selectors(list_p)
    list_p.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
