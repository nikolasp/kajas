"""Tests for the adapter interface and the structured-output translators.

The real Codex/Pi adapters shell out, so we don't exercise them here.
We do feed sample JSONL lines through the translators to make sure
they degrade gracefully on unknown / malformed events.
"""

from __future__ import annotations

from pathlib import Path

from kajas.adapters import codex, pi
from kajas.adapters.base import load_registry


def test_codex_translates_final_events() -> None:
    line = '{"type":"item.created","item":{"type":"agent_message","text":"hi"}}'
    ev = codex._translate("planning", line)
    assert ev is not None
    assert ev.type == "message"
    assert ev.text == "hi"


def test_codex_translates_usage_event() -> None:
    line = '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":4}}'
    ev = codex._translate("implementation", line)
    assert ev is not None
    assert ev.type == "usage"
    assert ev.input_tokens == 10
    assert ev.output_tokens == 4


def test_codex_translates_completed_planning_message_to_plan_final() -> None:
    line = (
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"```yaml\\ngoal: build game\\nplan:\\n  - write html\\n```"}}'
    )
    ev = codex._translate("planning", line)
    assert ev is not None
    assert ev.type == "final"
    assert ev.artifact == "plan.md"
    assert ev.extra["plan_yaml"] == "goal: build game\nplan:\n  - write html\n"


def test_codex_translates_error_event() -> None:
    line = '{"type":"error","message":"boom"}'
    ev = codex._translate("planning", line)
    assert ev is not None
    assert ev.type == "error"
    assert ev.message == "boom"


def test_codex_ignores_unknown_event() -> None:
    assert codex._translate("planning", '{"type":"future.event","foo":1}') is None


def test_codex_ignores_non_json() -> None:
    assert codex._translate("planning", "not json") is None


def test_codex_command_skips_git_check_for_non_git_project(tmp_path: Path) -> None:
    cmd = codex._build_command(tmp_path, "gpt-5.5")
    assert "--skip-git-repo-check" in cmd
    assert cmd[-2:] == ["--model", "gpt-5.5"]


def test_codex_command_keeps_git_check_for_git_project(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    cmd = codex._build_command(tmp_path, "default")
    assert "--skip-git-repo-check" not in cmd
    assert "--model" not in cmd


def test_pi_translates_message_update_deltas() -> None:
    pi._accum.clear()
    pi._translate(
        "planning",
        '{"type":"message_start","message":{"role":"assistant"}}',
    )
    pi._translate(
        "planning",
        '{"type":"message_update","assistantMessageEvent":{"type":"text_start"}}',
    )
    pi._translate(
        "planning",
        '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Hel"}}',
    )
    pi._translate(
        "planning",
        '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"lo"}}',
    )
    pi._translate(
        "planning",
        '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Hello"}]}}',
    )
    # After the message_end, the accumulator should have been consumed.
    assert "planning" not in pi._accum


def test_pi_translates_turn_end_usage() -> None:
    line = (
        '{"type":"turn_end","message":{"role":"assistant",'
        '"content":[],"usage":{"input":12,"output":34,"cacheRead":1,"cacheWrite":2,"totalTokens":49},'
        '"toolResults":[]}}'
    )
    evs = pi._translate("implementation", line)
    usage_evs = [e for e in evs if e.type == "usage"]
    assert len(usage_evs) == 1
    assert usage_evs[0].input_tokens == 12
    assert usage_evs[0].output_tokens == 34
    assert usage_evs[0].total_tokens == 49


def test_pi_ignores_unknown_event() -> None:
    assert pi._translate("planning", '{"type":"future.event","foo":1}') == []


def test_pi_ignores_non_json() -> None:
    assert pi._translate("planning", "not json") == []


def test_load_registry_known() -> None:
    reg = load_registry(["codex", "pi", "fake"])
    assert set(reg.keys()) == {"codex", "pi", "fake"}


def test_load_registry_unknown_silently_skipped() -> None:
    reg = load_registry(["nope"])
    assert reg == {}


def test_capabilities_match_design() -> None:
    reg = load_registry(["codex", "pi", "fake"])
    # Codex: working dir, sandbox, approval policy all true.
    assert reg["codex"].capabilities().working_dir is True
    assert reg["codex"].capabilities().sandbox is True
    # Pi: only working dir is supported.
    caps = reg["pi"].capabilities()
    assert caps.working_dir is True
    assert caps.sandbox is False
    assert caps.approval_policy is False
