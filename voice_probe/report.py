"""Turn a probe run into a useful artifact: summary, weak spots, priorities.

Consumes a JSONL run file (see :mod:`voice_probe.runner`) and produces a
Markdown report and a self-contained HTML report. Both answer the questions the
harness exists to answer: what passed/failed/partialed, which failure modes
recur, where latency hurts, and -- most important -- the ranked list of
concrete prompts to fix first, worst (unsafe) first.
"""

from __future__ import annotations

import html
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from voice_probe.schema import (
    FAILURE_FAMILIES,
    FAILURE_SEVERITY,
    VERDICT_FAIL,
    VERDICT_INFO,
    VERDICT_PARTIAL,
    VERDICT_PASS,
)


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[idx]


def summarize(meta: dict, rows: list[dict]) -> dict:
    """Compute every roll-up the report renders, from raw result rows."""
    verdicts = Counter(r["verdict"] for r in rows)
    graded = [r for r in rows if r["verdict"] != VERDICT_INFO]
    graded_total = len(graded) or 1
    pass_rate = 100.0 * verdicts.get(VERDICT_PASS, 0) / graded_total

    # Failure modes: count real fails/partials by their diagnostic label.
    failure_counts: Counter = Counter()
    for r in rows:
        if r["verdict"] in (VERDICT_FAIL, VERDICT_PARTIAL) and r.get("failure_kind"):
            failure_counts[r["failure_kind"]] += 1

    # Per-category breakdown.
    by_category: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_category[r["category"]][r["verdict"]] += 1

    latencies = [int(r.get("latency_ms", 0)) for r in rows]
    latency = {
        "count": len(latencies),
        "p50": _percentile(latencies, 50),
        "p95": _percentile(latencies, 95),
        "max": max(latencies) if latencies else 0,
        "mean": int(statistics.mean(latencies)) if latencies else 0,
    }
    slowest = sorted(rows, key=lambda r: int(r.get("latency_ms", 0)), reverse=True)[:5]

    # Prioritized issue list: every fail, ranked by severity then category.
    fails = [r for r in rows if r["verdict"] == VERDICT_FAIL]
    fails.sort(key=lambda r: (FAILURE_SEVERITY.get(r.get("failure_kind", ""), 99), r["case_id"]))

    return {
        "meta": meta,
        "total": len(rows),
        "graded": len(graded),
        "verdicts": dict(verdicts),
        "pass_rate": pass_rate,
        "failure_counts": failure_counts.most_common(),
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
        "latency": latency,
        "slowest": slowest,
        "fails": fails,
        "partials": [r for r in rows if r["verdict"] == VERDICT_PARTIAL],
    }


def _fmt_case(r: dict) -> str:
    return f'`{r["case_id"]}` [{r["category"]}/{r["difficulty"]}] "{r["prompt"][:90]}"'


def render_markdown(summary: dict) -> str:
    """Render the full Markdown report."""
    meta = summary["meta"]
    v = summary["verdicts"]
    lines: list[str] = []
    add = lines.append

    add("# Voice-mediator text probe -- run report")
    add("")
    add(f"- Generated: {meta.get('generated_at', datetime.now().isoformat())}")
    add(
        f"- Classifier mode: **{meta.get('classifier_mode', '?')}** "
        f"(intent model: `{meta.get('intent_model', '?')}`)"
    )
    add(f"- Default backend: `{meta.get('default_backend', '?')}`")
    add(f"- Cases: {meta.get('case_count', '?')}  |  Results: {summary['total']}")
    if meta.get("classifier_mode") != "live":
        add("")
        add(
            "> **Note:** not a `live` run. Classifier-dependent cases (tier-6 semantic) "
            "are recorded as `info`, not graded. Run `--classifier live` against Ollama "
            "for the real end-to-end verdict on those."
        )
    add("")

    add("## Scoreboard")
    add("")
    add("| Verdict | Count |")
    add("|---|---:|")
    add(f"| pass | {v.get(VERDICT_PASS, 0)} |")
    add(f"| partial | {v.get(VERDICT_PARTIAL, 0)} |")
    add(f"| **fail** | **{v.get(VERDICT_FAIL, 0)}** |")
    add(f"| info (ungraded) | {v.get(VERDICT_INFO, 0)} |")
    add("")
    add(
        f"**Pass rate (graded): {summary['pass_rate']:.0f}%**  "
        f"({v.get(VERDICT_PASS, 0)}/{summary['graded']})"
    )
    add("")

    add("## Common failure modes")
    add("")
    if summary["failure_counts"]:
        add("| Failure kind | Family | Count |")
        add("|---|---|---:|")
        for kind, count in summary["failure_counts"]:
            add(f"| `{kind}` | {FAILURE_FAMILIES.get(kind, '-')} | {count} |")
    else:
        add("_No failures or partials recorded._")
    add("")

    add("## By category")
    add("")
    add("| Category | pass | partial | fail | info |")
    add("|---|---:|---:|---:|---:|")
    for cat, counts in summary["by_category"].items():
        add(
            f"| {cat} | {counts.get(VERDICT_PASS, 0)} | {counts.get(VERDICT_PARTIAL, 0)} "
            f"| {counts.get(VERDICT_FAIL, 0)} | {counts.get(VERDICT_INFO, 0)} |"
        )
    add("")

    lat = summary["latency"]
    add("## Latency")
    add("")
    add(
        f"- p50: {lat['p50']} ms | p95: {lat['p95']} ms | max: {lat['max']} ms "
        f"| mean: {lat['mean']} ms"
    )
    add("")
    add("Slowest decisions:")
    for r in summary["slowest"]:
        add(f"- {r['latency_ms']} ms -- {_fmt_case(r)}")
    add("")

    add("## Highest-priority issues to fix")
    add("")
    add(
        "Ranked worst-first: unsafe (missing confirmation) > dropped/ungrounded "
        "> hallucinated > over-caution > tier quality."
    )
    add("")
    if summary["fails"]:
        for r in summary["fails"]:
            kind = r.get("failure_kind", "")
            add(f"### {_fmt_case(r)}")
            add(
                f"- **{kind}** ({FAILURE_FAMILIES.get(kind, '-')}) -- {r.get('failure_detail', '')}"
            )
            add(
                f"- routed: outcome=`{r['outcome']}` via tier=`{r['source']}` "
                f"agent=`{r['agent']}` conf={r['confidence']} risk=`{r['risk']}`"
            )
            if r.get("task"):
                add(f'- task: "{r["task"][:120]}"')
            if r.get("note"):
                add(f"- probe note: {r['note']}")
            add("")
    else:
        add("_No hard failures. Review partials below for quality gaps._")
    add("")

    if summary["partials"]:
        add("## Partial passes (right call, quality gap)")
        add("")
        for r in summary["partials"]:
            add(f"- {_fmt_case(r)} -- `{r.get('failure_kind', '')}`: {r.get('failure_detail', '')}")
        add("")

    add("## Recommended improvements")
    add("")
    add(_recommendations(summary))
    add("")
    return "\n".join(lines)


def _recommendations(summary: dict) -> str:
    """Translate the recurring failure kinds into concrete suggestions."""
    kinds = {k for k, _ in summary["failure_counts"]}
    recs: list[str] = []
    if "missing_confirmation" in kinds:
        recs.append(
            "- **Unsafe dispatch found.** The destructive-word list "
            "(`config.AGENT_DESTRUCTIVE_WORDS`) is verb-based and substring-matched; "
            "actions like *empty the recycle bin*, *kill process*, *disable firewall*, or "
            "*format* slip through. Consider a risk classifier signal (the router already "
            "computes `category`) so `system_control`/destructive categories gate regardless "
            "of exact wording."
        )
    if "over_confirmation" in kinds:
        recs.append(
            "- **Over-confirmation found.** Read-only requests whose text merely mentions a "
            "destructive verb (*search the web for how to delete an account*) are gated "
            "unnecessarily. Scope the destructive check to the *action* rather than any "
            "occurrence of the word in the task text."
        )
    if "dropped_request" in kinds or "dropped_risky_request" in kinds:
        recs.append(
            "- **Dropped requests.** Real tasks fell through to chat. Widen the keyword net "
            "(`voice_commands._ACTION_VERBS` / `_TASK_KEYWORDS`) for the missed verbs/objects, "
            "or lean on the semantic tier for the phrasings deterministic rules cannot cover."
        )
    if "hallucinated_dispatch" in kinds or "hallucinated_confirm" in kinds:
        recs.append(
            "- **Hallucinated tasks.** The router invented work from conversation. Tighten the "
            "grounding guard and the smalltalk/noise gates; check the classifier isn't reciting "
            "few-shot examples (`_example_echo`)."
        )
    if "grounding_leak" in kinds:
        recs.append(
            "- **Ungrounded dispatch.** A task with no lexical tie to the utterance was "
            "dispatched. Extend `_grounding_gap` coverage or lower the dispatch confidence band."
        )
    if "latency" in kinds:
        recs.append(
            "- **Latency.** Some decisions exceeded the budget. The classifier timeout "
            "(`INTENT_TIMEOUT_SECS`) bounds it; confirm the model stays resident "
            "(`keep_alive`) and warmup ran."
        )
    if not recs:
        recs.append(
            "- No systemic failure mode dominates this run. Keep expanding the corpus "
            "and run `--classifier live` regularly to catch semantic-tier regressions."
        )
    return "\n".join(recs)


# -- HTML -------------------------------------------------------------------

_VERDICT_COLOR = {
    VERDICT_PASS: "#1a7f37",
    VERDICT_PARTIAL: "#9a6700",
    VERDICT_FAIL: "#cf222e",
    VERDICT_INFO: "#57606a",
}


def render_html(summary: dict) -> str:
    """Render a self-contained, theme-light HTML report."""
    meta = summary["meta"]
    v = summary["verdicts"]

    def esc(s: object) -> str:
        return html.escape(str(s))

    def chip(verdict: str, count: int) -> str:
        return (
            f'<span class="chip" style="background:{_VERDICT_COLOR[verdict]}">'
            f"{verdict}: {count}</span>"
        )

    rows_html: list[str] = []
    for r in summary["fails"] + summary["partials"]:
        color = _VERDICT_COLOR.get(r["verdict"], "#57606a")
        rows_html.append(
            "<tr>"
            f'<td><span class="dot" style="background:{color}"></span>{esc(r["verdict"])}</td>'
            f"<td><code>{esc(r['case_id'])}</code></td>"
            f"<td>{esc(r['category'])}/{esc(r['difficulty'])}</td>"
            f"<td>{esc(r['prompt'])}</td>"
            f"<td><b>{esc(r.get('failure_kind', ''))}</b><br><small>{esc(r.get('failure_detail', ''))}</small></td>"
            f"<td>out=<code>{esc(r['outcome'])}</code> tier=<code>{esc(r['source'])}</code><br>"
            f"agent=<code>{esc(r['agent'])}</code> risk=<code>{esc(r['risk'])}</code></td>"
            f"<td>{esc(r['latency_ms'])}ms</td>"
            "</tr>"
        )

    fmodes = (
        "".join(
            f"<tr><td><code>{esc(k)}</code></td><td>{esc(FAILURE_FAMILIES.get(k, '-'))}</td>"
            f"<td style='text-align:right'>{c}</td></tr>"
            for k, c in summary["failure_counts"]
        )
        or "<tr><td colspan=3>None</td></tr>"
    )

    cats = "".join(
        f"<tr><td>{esc(cat)}</td>"
        f"<td style='text-align:right'>{cc.get(VERDICT_PASS, 0)}</td>"
        f"<td style='text-align:right'>{cc.get(VERDICT_PARTIAL, 0)}</td>"
        f"<td style='text-align:right;color:#cf222e'>{cc.get(VERDICT_FAIL, 0)}</td>"
        f"<td style='text-align:right;color:#57606a'>{cc.get(VERDICT_INFO, 0)}</td></tr>"
        for cat, cc in summary["by_category"].items()
    )

    lat = summary["latency"]
    live_note = (
        ""
        if meta.get("classifier_mode") == "live"
        else '<p class="note">Not a <b>live</b> run: classifier-dependent (tier-6) cases are '
        "recorded as <code>info</code>, not graded. Run <code>--classifier live</code> for the "
        "real verdict on those.</p>"
    )
    recs = html.escape(_recommendations(summary)).replace("\n", "<br>")

    return f"""<div class="wrap">
<h1>Voice-mediator text probe</h1>
<p class="meta">Generated {esc(meta.get("generated_at", ""))} &middot;
classifier <b>{esc(meta.get("classifier_mode", "?"))}</b> &middot;
model <code>{esc(meta.get("intent_model", "?"))}</code> &middot;
backend <code>{esc(meta.get("default_backend", "?"))}</code></p>
{live_note}
<div class="chips">
{chip(VERDICT_PASS, v.get(VERDICT_PASS, 0))}
{chip(VERDICT_PARTIAL, v.get(VERDICT_PARTIAL, 0))}
{chip(VERDICT_FAIL, v.get(VERDICT_FAIL, 0))}
{chip(VERDICT_INFO, v.get(VERDICT_INFO, 0))}
<span class="chip" style="background:#0969da">pass rate {summary["pass_rate"]:.0f}%</span>
</div>

<h2>Common failure modes</h2>
<table><thead><tr><th>Kind</th><th>Family</th><th>Count</th></tr></thead><tbody>{fmodes}</tbody></table>

<h2>By category</h2>
<table><thead><tr><th>Category</th><th>pass</th><th>partial</th><th>fail</th><th>info</th></tr></thead>
<tbody>{cats}</tbody></table>

<h2>Latency</h2>
<p>p50 <b>{lat["p50"]}ms</b> &middot; p95 <b>{lat["p95"]}ms</b> &middot; max <b>{lat["max"]}ms</b>
&middot; mean {lat["mean"]}ms</p>

<h2>Issues (worst first)</h2>
<table><thead><tr><th>Verdict</th><th>Case</th><th>Cat</th><th>Prompt</th><th>Failure</th>
<th>Routed</th><th>Lat</th></tr></thead><tbody>{"".join(rows_html) or "<tr><td colspan=7>Clean run</td></tr>"}</tbody></table>

<h2>Recommended improvements</h2>
<p class="recs">{recs}</p>
</div>
<style>
.wrap {{ max-width: 1100px; margin: 0 auto; font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; color:#1f2328; padding: 12px; }}
h1 {{ margin: 0 0 4px; }} .meta,.note {{ color:#57606a; }}
.note {{ background:#fff8c5; padding:8px 12px; border-radius:6px; }}
.chips {{ margin:14px 0; display:flex; gap:8px; flex-wrap:wrap; }}
.chip {{ color:#fff; padding:4px 10px; border-radius:12px; font-weight:600; font-size:13px; }}
table {{ border-collapse: collapse; width:100%; margin:8px 0 20px; display:block; overflow-x:auto; }}
th,td {{ border:1px solid #d0d7de; padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ background:#f6f8fa; }} code {{ background:#eff1f3; padding:1px 4px; border-radius:4px; }}
.dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }}
.recs {{ background:#f6f8fa; padding:12px; border-radius:6px; }}
</style>"""


def write_reports(summary: dict, md_path: Path, html_path: Path) -> None:
    """Write both the Markdown and HTML reports to disk."""
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    html_path.write_text(render_html(summary), encoding="utf-8")
