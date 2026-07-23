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
import sys
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
    return 1 if summary["verdicts"].get(VERDICT_FAIL, 0) else 0


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
