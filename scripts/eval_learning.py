#!/usr/bin/env python3
"""Eval harness for the learning curator (§7.4).

Runs five scenarios, each a synthetic 4-message session with one
explicit user correction, then judges whether the promoted lesson
generalizes correctly to a same-class probe and stays quiet on a
different-class probe.

Pass criteria for the shadow → live flip:

  G1 = 5/5    every scenario produces a verified lesson (Day 2
              carryover: 5/5 is the bar, not ≥4/5)
  G2 = 5/5    every evidence string verifies verbatim
  G3 ≥ 4/5    lessons APPLY to same-class probes
  G4 = 5/5    lessons DOES_NOT_APPLY (or APPLIES_BENIGNLY) on
              different-class probes — anything WRONGLY_APPLIES
              counts as a G4 failure

Usage:
    ./venv-python scripts/eval_learning.py [--out report.md]

Costs roughly 15 LLM calls (5 reviews + 5 same-class judges +
5 different-class judges) — cheap. Run it after every prompt
change. The script prints a one-line summary of each scenario as
it goes plus a final pass/fail summary, and writes a full markdown
report to ``--out`` (default
``~/.vexis/logs/learning-eval/<utc>/REPORT.md``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow `./venv-python scripts/eval_learning.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.learning_review import run_review  # noqa: E402
from core.transcripts import (  # noqa: E402
    SessionMeta,
    claude_session_jsonl_dir,
    iter_messages,
)


# --------------------------------------------------------------------
# Scenarios (§7.4) — each is one synthetic 4-message session with one
# explicit user correction, plus a same-class probe (G3) and a
# different-class probe (G4).
# --------------------------------------------------------------------


SCENARIOS: list[dict] = [
    {
        "name": "time-bound-listing",
        "summary": "Filter time-bound options by current time",
        "transcript": [
            ("user", "List my upcoming meetings for today."),
            ("assistant",
             "Here are today's meetings: 9am standup, 11am design review, "
             "2pm 1:1 with manager, 4pm (already past) retro debrief, "
             "5:30pm interview."),
            ("user",
             "you included the 4pm retro which already finished. "
             "only show what's still upcoming."),
            ("assistant",
             "You're right, my apologies. Upcoming: 5:30pm interview."),
        ],
        "same_class_probe": "list my calendar events for tonight",
        "different_class_probe": "list all the .py files modified in this repo",
    },
    {
        "name": "format-brevity",
        "summary": "Return only what was asked, not adjacent context",
        "transcript": [
            ("user", "what's the disk usage percentage on the main drive?"),
            ("assistant",
             "Disk: 73% used. CPU: 12% idle. Memory: 8.2GB free. "
             "Uptime: 3 days. Network: stable."),
            ("user",
             "I asked for the disk usage percentage, not also CPU + "
             "memory + uptime + network. Just answer what I asked."),
            ("assistant", "Got it. 73%."),
        ],
        "same_class_probe": "what's the current battery percentage?",
        "different_class_probe": "give me a step-by-step tutorial on git rebase",
    },
    {
        "name": "destructive-confirm",
        "summary": "Confirm before irreversible operations on user data",
        "transcript": [
            ("user", "clean up the old draft notes folder"),
            ("assistant",
             "Deleted 47 files from ~/notes/drafts/. Done."),
            ("user",
             "you just deleted my draft notes without asking first. "
             "always confirm before deleting user files."),
            ("assistant",
             "I should not have done that without confirmation. I'll "
             "restate paths and ask y/n before any deletion going forward."),
        ],
        "same_class_probe": "remove the old test branches from the repo",
        "different_class_probe": "summarize this article for me",
    },
    {
        "name": "unit-conventions",
        "summary": "Respect locale/unit preferences durably",
        "transcript": [
            ("user", "how much flour for the bread recipe?"),
            ("assistant", "About 17.6 ounces."),
            ("user",
             "I'm in metric, give me grams not ounces. always use metric "
             "for me."),
            ("assistant", "500 grams."),
        ],
        "same_class_probe": "how far is the nearest grocery store from here?",
        "different_class_probe": "list my recent emails",
    },
    {
        "name": "code-review-brevity",
        "summary": "Code review commentary should add information, not restate visible code",
        "transcript": [
            ("user",
             "review this PR diff: it replaces the rate limiter from "
             "token bucket to leaky bucket"),
            ("assistant",
             "This change replaces the token bucket implementation with "
             "a leaky bucket. The token bucket allowed bursts up to N "
             "requests; the leaky bucket processes at a steady rate. "
             "The diff modifies the RateLimiter class in src/limiter.py. "
             "The new implementation uses a queue. The constructor takes "
             "a rate parameter. The acquire method now blocks instead "
             "of returning immediately."),
            ("user",
             "stop explaining what the code does, I can read it. tell me "
             "what's wrong with it or that it's fine."),
            ("assistant",
             "Looks fine overall. One concern: the queue is unbounded, "
             "so a burst of requests will accumulate memory. Consider a "
             "max queue size."),
        ],
        "same_class_probe": "review this auth handler diff that adds JWT verification",
        "different_class_probe": "explain how SQLite's WAL mode works",
    },
]


# --------------------------------------------------------------------
# Judge prompt — per §7.4 of the research doc.
# --------------------------------------------------------------------


_JUDGE_PROMPT_TEMPLATE = """\
A learning curator promoted this lesson into Vexis's long-term memory:

  LESSON: {lesson}
  SCOPE:  {scope}

A new user has now asked Vexis: "{probe}"

Question: should this lesson influence Vexis's behavior on this prompt?

Answer with EXACTLY one of these tokens on the first line, then a
one-sentence explanation on the second line. Nothing else:

  APPLIES         — the lesson is relevant and should shape the behavior
  DOES_NOT_APPLY  — the lesson is unrelated to this prompt
  WRONGLY_APPLIES — the lesson is being triggered by surface words but
                    should NOT affect this prompt

Be strict on WRONGLY_APPLIES: a lesson that 'kind of fits' but would
produce surprising behavior counts as wrong here. We're testing
whether the rule generalizes cleanly.
"""

# Three judge tokens. WRONGLY_APPLIES is the G4 failure case.
_JUDGE_TOKEN_RE = re.compile(
    r"^\s*(APPLIES|DOES_NOT_APPLY|WRONGLY_APPLIES)\b",
    re.MULTILINE,
)


# --------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    g1_promoted: bool = False
    g2_evidence_verbatim: bool = False
    g3_applies_same_class: bool | None = None
    g4_does_not_misfire: bool | None = None
    lesson: dict | None = None
    rejected: list[tuple[dict, str]] = field(default_factory=list)
    same_class_judge: str = ""
    different_class_judge: str = ""
    raw_response: str = ""
    review_error: str | None = None
    judge_errors: list[str] = field(default_factory=list)
    transcript_chars: int = 0


@dataclass
class EvalReport:
    started_at: datetime
    finished_at: datetime
    scenarios: list[ScenarioResult]

    @property
    def g1(self) -> tuple[int, int]:
        return sum(s.g1_promoted for s in self.scenarios), len(self.scenarios)

    @property
    def g2(self) -> tuple[int, int]:
        return sum(s.g2_evidence_verbatim for s in self.scenarios), len(self.scenarios)

    @property
    def g3(self) -> tuple[int, int]:
        n = sum(1 for s in self.scenarios if s.g3_applies_same_class is True)
        d = sum(1 for s in self.scenarios if s.g3_applies_same_class is not None)
        return n, d

    @property
    def g4(self) -> tuple[int, int]:
        n = sum(1 for s in self.scenarios if s.g4_does_not_misfire is True)
        d = sum(1 for s in self.scenarios if s.g4_does_not_misfire is not None)
        return n, d

    @property
    def passes_bar(self) -> bool:
        """Pass criteria from §7.4 (Day 2 carryover applied: G1 = 5/5)."""
        n_total = len(self.scenarios)
        return (
            self.g1[0] == n_total
            and self.g2[0] == n_total
            and self.g3[0] >= 4 and self.g3[1] == n_total
            and self.g4[0] == n_total and self.g4[1] == n_total
        )


# --------------------------------------------------------------------
# Synthetic-session staging
# --------------------------------------------------------------------


def _stage_session(workspace: Path, scenario: dict) -> tuple[Path, SessionMeta]:
    """Write a synthetic JSONL for the scenario and return (path, meta)."""
    pdir = claude_session_jsonl_dir(workspace)
    pdir.mkdir(parents=True, exist_ok=True)
    uuid = f"eval-{scenario['name']}"
    path = pdir / f"{uuid}.jsonl"

    now = datetime.now(timezone.utc)
    base = now - timedelta(minutes=60)
    lines: list[dict] = []
    for i, (role, text) in enumerate(scenario["transcript"]):
        ts = (base + timedelta(seconds=i * 30)).isoformat().replace("+00:00", "Z")
        if role == "user":
            lines.append({
                "type": "user",
                "uuid": f"m-{i}",
                "timestamp": ts,
                "message": {"role": "user", "content": text},
            })
        else:
            lines.append({
                "type": "assistant",
                "uuid": f"m-{i}",
                "timestamp": ts,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            })

    path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )
    meta = SessionMeta(
        session_uuid=uuid,
        jsonl_path=path,
        last_message_timestamp=base + timedelta(seconds=(len(lines) - 1) * 30),
        message_count_estimate=len(lines),
    )
    return path, meta


# --------------------------------------------------------------------
# Judge LLM call
# --------------------------------------------------------------------


def _call_judge(lesson: dict, probe: str, *, timeout_s: int = 120) -> tuple[str, str]:
    """Run one judge LLM call. Returns ``(verdict, raw)`` where verdict
    is one of APPLIES / DOES_NOT_APPLY / WRONGLY_APPLIES / 'UNPARSED'.
    """
    prompt = _JUDGE_PROMPT_TEMPLATE.format(
        lesson=lesson.get("lesson", ""),
        scope=lesson.get("scope", ""),
        probe=probe,
    )
    try:
        cp = subprocess.run(
            ["claude", "-p", prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return "UNPARSED", "(timed out)"
    except OSError as exc:
        return "UNPARSED", f"(spawn failed: {exc})"
    if cp.returncode != 0:
        body = (cp.stderr or cp.stdout or b"").decode("utf-8", errors="replace")
        return "UNPARSED", f"(exit {cp.returncode}: {body[:200]})"
    text = (cp.stdout or b"").decode("utf-8", errors="replace").strip()
    m = _JUDGE_TOKEN_RE.search(text)
    if not m:
        return "UNPARSED", text
    return m.group(1), text


# --------------------------------------------------------------------
# Per-scenario runner
# --------------------------------------------------------------------


def run_one_scenario(scenario: dict, workspace: Path) -> ScenarioResult:
    result = ScenarioResult(name=scenario["name"])
    jsonl_path, meta = _stage_session(workspace, scenario)
    messages = list(iter_messages(jsonl_path))
    output = run_review(workspace, meta, messages)

    result.transcript_chars = output.transcript_chars
    result.raw_response = output.raw_response
    result.rejected = output.rejected
    result.review_error = output.error

    # G1: did the curator emit a verified lesson?
    if output.verified_lessons:
        result.g1_promoted = True
        result.lesson = output.verified_lessons[0]
        result.g2_evidence_verbatim = True  # verifier already checked
    elif output.parsed_lessons:
        # Parsed but rejected — G1 fails. Still useful to grade
        # G2 on what was rejected.
        first = output.parsed_lessons[0]
        result.lesson = first
        # Whether evidence verbatim'd: walk the rejected list for
        # an evidence-related rejection.
        result.g2_evidence_verbatim = not any(
            "verbatim" in reason for _, reason in output.rejected
            if _ is first
        )
    # else G1 fails, no candidates at all.

    if not result.lesson:
        return result

    # G3: same-class probe should APPLY.
    g3_verdict, g3_raw = _call_judge(result.lesson, scenario["same_class_probe"])
    result.same_class_judge = f"{g3_verdict}\n{g3_raw}"
    if g3_verdict == "UNPARSED":
        result.judge_errors.append(f"same-class judge: {g3_raw[:200]}")
        result.g3_applies_same_class = None
    else:
        result.g3_applies_same_class = (g3_verdict == "APPLIES")

    # G4: different-class probe should NOT misfire.
    g4_verdict, g4_raw = _call_judge(result.lesson, scenario["different_class_probe"])
    result.different_class_judge = f"{g4_verdict}\n{g4_raw}"
    if g4_verdict == "UNPARSED":
        result.judge_errors.append(f"different-class judge: {g4_raw[:200]}")
        result.g4_does_not_misfire = None
    else:
        # WRONGLY_APPLIES = G4 fail. APPLIES on a "different-class"
        # probe is also a fail (that's the whole point of G4).
        # Only DOES_NOT_APPLY counts as pass.
        result.g4_does_not_misfire = (g4_verdict == "DOES_NOT_APPLY")

    return result


# --------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------


def _grade(result: ScenarioResult) -> str:
    """One-line per-scenario summary."""
    g1 = "G1✓" if result.g1_promoted else "G1✗"
    g2 = "G2✓" if result.g2_evidence_verbatim else "G2✗"
    if result.g3_applies_same_class is True:
        g3 = "G3✓"
    elif result.g3_applies_same_class is False:
        g3 = "G3✗"
    else:
        g3 = "G3?"
    if result.g4_does_not_misfire is True:
        g4 = "G4✓"
    elif result.g4_does_not_misfire is False:
        g4 = "G4✗"
    else:
        g4 = "G4?"
    return f"  {result.name:<22} {g1} {g2} {g3} {g4}"


def _build_markdown(report: EvalReport) -> str:
    g1n, g1d = report.g1
    g2n, g2d = report.g2
    g3n, g3d = report.g3
    g4n, g4d = report.g4
    lines = [
        f"# Learning curator eval — {report.started_at.strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        f"- started_at: {report.started_at.isoformat()}",
        f"- finished_at: {report.finished_at.isoformat()}",
        "",
        "## Summary",
        "",
        f"- G1 (promoted at all): **{g1n}/{g1d}** (target 5/5)",
        f"- G2 (evidence verbatim): **{g2n}/{g2d}** (target 5/5)",
        f"- G3 (same-class APPLIES): **{g3n}/{g3d}** (target ≥4/5)",
        f"- G4 (different-class quiet): **{g4n}/{g4d}** (target 5/5)",
        "",
        f"**Verdict: {'PASS — clear to flip shadow → live' if report.passes_bar else 'FAIL — do not flip live yet'}**",
        "",
    ]
    for r in report.scenarios:
        lines.append("---")
        lines.append("")
        lines.append(f"## {r.name}")
        lines.append("")
        lines.append(f"- transcript_chars: {r.transcript_chars}")
        if r.review_error:
            lines.append(f"- review_error: `{r.review_error}`")
        if r.lesson:
            lines.append("")
            lines.append("**Promoted lesson:**")
            lines.append("")
            lines.append(f"- lesson:   {r.lesson.get('lesson', '?')}")
            lines.append(f"- scope:    {r.lesson.get('scope', '?')}")
            lines.append(f"- evidence: {r.lesson.get('evidence', '?')}")
        else:
            lines.append("")
            lines.append("**No verified lesson produced.**")
        if r.rejected:
            lines.append("")
            lines.append("**Rejected candidates:**")
            for cand, reason in r.rejected:
                lines.append(f"- {reason}: {(cand.get('lesson') or '')[:120]}")
        if r.same_class_judge:
            lines.append("")
            lines.append("**Same-class judge (G3):**")
            lines.append("```")
            lines.append(r.same_class_judge)
            lines.append("```")
        if r.different_class_judge:
            lines.append("")
            lines.append("**Different-class judge (G4):**")
            lines.append("```")
            lines.append(r.different_class_judge)
            lines.append("```")
        if r.judge_errors:
            lines.append("")
            lines.append("**Judge errors:**")
            for err in r.judge_errors:
                lines.append(f"- {err}")
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------


def run_eval() -> EvalReport:
    """Programmatic entry. Stages scenarios in a tmpdir, runs all
    five with the real LLM pipeline, and returns the report.
    Caller is responsible for printing or persisting."""
    started_at = datetime.now(timezone.utc)
    workspace_root = Path(tempfile.mkdtemp(prefix="vexis-eval-"))
    workspace = workspace_root / "vexis-workspace"
    (workspace / "memories").mkdir(parents=True)

    # Patch HOME so the staged JSONLs land under tmpdir's
    # ``~/.claude/projects`` (claude_session_jsonl_dir uses Path.home()).
    # We don't touch os.environ HOME — the subprocess (claude binary)
    # needs the real $HOME to find its credentials.
    import pathlib
    real_home = pathlib.Path.home
    pathlib.Path.home = classmethod(lambda cls: workspace_root)

    try:
        results: list[ScenarioResult] = []
        for s in SCENARIOS:
            print(f"running {s['name']!r} ...", flush=True)
            r = run_one_scenario(s, workspace)
            print(_grade(r), flush=True)
            results.append(r)
        finished_at = datetime.now(timezone.utc)
        return EvalReport(
            started_at=started_at,
            finished_at=finished_at,
            scenarios=results,
        )
    finally:
        pathlib.Path.home = real_home
        shutil.rmtree(workspace_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Markdown report path. Default: ~/.vexis/logs/learning-eval/<utc>/REPORT.md",
    )
    args = parser.parse_args()

    report = run_eval()

    if args.out is None:
        folder = (
            Path.home()
            / ".vexis" / "logs" / "learning-eval"
            / report.started_at.strftime("%Y-%m-%dT%H%M%SZ")
        )
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / "REPORT.md"
    else:
        out = args.out
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_build_markdown(report), encoding="utf-8")

    print()
    g1n, g1d = report.g1
    g2n, g2d = report.g2
    g3n, g3d = report.g3
    g4n, g4d = report.g4
    print(f"G1 promoted        : {g1n}/{g1d}")
    print(f"G2 evidence verbatim: {g2n}/{g2d}")
    print(f"G3 same-class apply: {g3n}/{g3d}")
    print(f"G4 quiet on others : {g4n}/{g4d}")
    print(f"report: {out}")
    print()
    if report.passes_bar:
        print("PASS — clear to flip shadow → live (after the soak)")
        return 0
    print("FAIL — do not flip live yet")
    return 1


if __name__ == "__main__":
    sys.exit(main())
