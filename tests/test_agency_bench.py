"""Tests for the agency benchmark suite (offline — no network calls).

Covers the deterministic Halcyon Systems sandbox, the currency cross-rate
math, scenario checkers against constructed traces, and the multi-round
``_agency_tests`` driver loop with an in-process fake chat client.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from kajas import agency_bench
from kajas.agency_bench import AgencyTrace, SCENARIOS, dispatch_tool
from kajas.benchmarks import BenchmarkRun, _agency_tests, _distributed_scores


# ---------------------------------------------------------------------------
# Sandbox + dispatcher
# ---------------------------------------------------------------------------

def test_staff_lookup_by_name_id_email_and_missing():
    assert dispatch_tool("staff_lookup", {"name": "Mira Voss"})["id"] == "S01"
    assert dispatch_tool("staff_lookup", {"staff_id": "S03"})["name"] == "Nadia Brar"
    assert dispatch_tool("staff_lookup", {"email": "dana.roth@halcyon.example"})["id"] == "S09"
    assert dispatch_tool("staff_lookup", {"name": "Nobody Here"}) == {"error": "staff member not found"}


def test_directory_search_active_filter_count():
    active = dispatch_tool("directory_search", {"department": "Platform", "active_only": True})["staff"]
    assert len(active) == 5  # Mira, Tomas, Nadia, Priya, Kai (Rashid inactive)
    all_platform = dispatch_tool("directory_search", {"department": "Platform"})["staff"]
    assert len(all_platform) == 6
    assert all_platform == dispatch_tool("directory_search", {"department": "platform"})["staff"]


def test_unknown_tool_is_handled():
    assert "error" in dispatch_tool("nope", {})


def test_kb_search_is_present_only_as_decoy():
    schemas = {t["function"]["name"] for t in agency_bench.tool_schemas()}
    assert "kb_search" in schemas
    schemas_no_decoy = {t["function"]["name"] for t in agency_bench.tool_schemas(include_kb_decoy=False)}
    assert "kb_search" not in schemas_no_decoy


# ---------------------------------------------------------------------------
# Currency math (the cross-rate correctness we fixed)
# ---------------------------------------------------------------------------

def test_currency_math_matches_usd_denomination():
    # RATES_TO_USD[c] is the USD value of one unit of c.
    assert dispatch_tool("convert_currency", {"amount": 750, "from_currency": "EUR", "to_currency": "USD"})["converted"] == 810.0
    # 200 USD / 0.0067 = 29850.74.. rounded to 29850.75
    assert dispatch_tool("convert_currency", {"amount": 200, "from_currency": "USD", "to_currency": "JPY"})["converted"] == 29850.75


def test_get_exchange_rate_direction():
    rate = dispatch_tool("get_exchange_rate", {"from_currency": "EUR", "to_currency": "USD"})["rate"]
    assert rate == round(1.08 / 1.0, 6)  # 1 EUR = 1.08 USD


def test_currency_unknown_pair_errors():
    assert "error" in dispatch_tool("convert_currency", {"amount": 1, "from_currency": "USD", "to_currency": "XXX"})


# ---------------------------------------------------------------------------
# Scenario checkers against constructed traces
# ---------------------------------------------------------------------------

def _with_call(name, args, final_text=""):
    t = AgencyTrace()
    t.tool_calls.append({"name": name, "args": args})
    t.results.append(dispatch_tool(name, args))
    t.final_text = final_text
    return t


def test_checker_tool_selection_passes_and_fails():
    sc = next(s for s in SCENARIOS if s.id == "agency.sel.mira_dept")
    sc.reset()
    t = _with_call("staff_lookup", {"name": "Mira Voss"}, "Mira is in the Platform department.")
    assert sc.check(t) == (True, "department=Platform")
    t2 = _with_call("staff_lookup", {"name": "Mira Voss"}, "Mira is in Sales.")
    ok, detail = sc.check(t2)
    assert ok is False
    assert "expected Platform" in detail


def test_checker_restraint_requires_no_tools_and_text():
    sc = next(s for s in SCENARIOS if s.id == "agency.restraint.weather")
    t = AgencyTrace()
    t.final_text = "I can't look up weather forecasts."
    assert sc.check(t)[0] is True
    t.tool_calls.append({"name": "kb_search", "args": {"query": "lisbon rain"}})
    assert sc.check(t)[0] is False


def test_checker_focus_penalises_kb_decoy():
    sc = next(s for s in SCENARIOS if s.id == "agency.focus.mira_team_direct")
    t_ok = _with_call("staff_lookup", {"name": "Mira Voss"})
    assert sc.check(t_ok)[0] is True
    t_decoy = AgencyTrace()
    t_decoy.tool_calls.append({"name": "kb_search", "args": {"query": "Mira team"}})
    assert sc.check(t_decoy)[0] is False


def test_checker_booking_validates_iso_times():
    sc = next(s for s in SCENARIOS if s.id == "agency.sched.foxglove_tomorrow")
    sc.reset()
    start = agency_bench.tomorrow().replace(hour=14, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    siso = start.isoformat().replace("+00:00", "Z")
    eiso = end.isoformat().replace("+00:00", "Z")
    t = _with_call("book_room", {"room": "Foxglove", "start": siso, "end": eiso, "purpose": "daily standup"})
    assert sc.check(t)[0] is True
    # wrong room -> fail
    t2 = _with_call("book_room", {"room": "Atrium", "start": siso, "end": eiso, "purpose": "standup"})
    assert sc.check(t2)[0] is False


def test_checker_currency_requires_final_answer_to_contain_converted_amount():
    sc = next(s for s in SCENARIOS if s.id == "agency.cur.eur_usd")
    t = _with_call("convert_currency", {"amount": 750, "from_currency": "EUR", "to_currency": "USD"},
                   final_text="That's 810.0 USD.")
    assert sc.check(t) == (True, "converted 750 EUR->USD = 810.0")
    # missing the number in prose -> fail
    t2 = _with_call("convert_currency", {"amount": 750, "from_currency": "EUR", "to_currency": "USD"},
                    final_text="It's about eight hundred and ten dollars.")
    assert sc.check(t2)[0] is False


def test_checker_currency_chain_requires_two_rate_calls():
    """agency.cur.usd_jpy requires chaining usd_to_eur + eur_to_jpy (no direct conversion)."""
    sc = next(s for s in SCENARIOS if s.id == "agency.cur.usd_jpy")
    assert sc.only_tools == ["usd_to_eur", "eur_to_jpy"]

    # Valid chain: usd_to_eur then eur_to_jpy, correct final text
    t_ok = AgencyTrace()
    t_ok.tool_calls.append({"name": "usd_to_eur", "args": {"amount": 200}})
    t_ok.results.append(dispatch_tool("usd_to_eur", {"amount": 200}))
    t_ok.tool_calls.append({"name": "eur_to_jpy", "args": {"amount": t_ok.results[0]["converted"]}})
    t_ok.results.append(dispatch_tool("eur_to_jpy", {"amount": t_ok.results[0]["converted"]}))
    t_ok.final_text = "200 USD = 29850.75 JPY"
    assert sc.check(t_ok)[0] is True

    # Missing first tool -> fail
    t_missing_first = AgencyTrace()
    t_missing_first.tool_calls.append({"name": "eur_to_jpy", "args": {"amount": 185.19}})
    t_missing_first.results.append(dispatch_tool("eur_to_jpy", {"amount": 185.19}))
    t_missing_first.final_text = "200 USD = 29850.75 JPY"
    ok, detail = sc.check(t_missing_first)
    assert ok is False
    assert "usd_to_eur" in detail

    # Wrong chain order (eur_to_jpy first, then usd_to_eur) -> fail
    t_wrong_order = AgencyTrace()
    t_wrong_order.tool_calls.append({"name": "eur_to_jpy", "args": {"amount": 200}})
    t_wrong_order.results.append(dispatch_tool("eur_to_jpy", {"amount": 200}))
    t_wrong_order.tool_calls.append({"name": "usd_to_eur", "args": {"amount": 200}})
    t_wrong_order.results.append(dispatch_tool("usd_to_eur", {"amount": 200}))
    t_wrong_order.final_text = "200 USD = 29850.75 JPY"
    ok, detail = sc.check(t_wrong_order)
    assert ok is False
    assert "first tool amount" in detail or "second tool amount" in detail


def test_scenarios_are_deterministic_and_have_unique_ids():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert len(SCENARIOS) >= 17
    # restart of the sandbox clears stale state so scenarios stay independent
    booking = next(s for s in SCENARIOS if s.id == "agency.sched.foxglove_tomorrow")
    booking.reset()
    _with_call("book_room", {"room": "Foxglove", "start": "2026-05-29T14:00:00Z", "end": "2026-05-29T15:00:00Z"})
    assert agency_bench.BOOKINGS  # booking landed
    booking.reset()
    assert not agency_bench.BOOKINGS  # reset cleared it


# ---------------------------------------------------------------------------
# Driver loop (_agency_tests) with a fake chat client
# ---------------------------------------------------------------------------

class _FakeChat:
    """Scripted OpenAI-compatible client.

    A single policy: if the latest user message asks for weather or is a
    standalone greeting (restraint scenarios), reply in text with no tool
    calls. Otherwise emit a staff_lookup tool call once, then a text reply.
    Enough to exercise the loop and to make the two restraint scenarios
    pass while everything else exercises the tool-call branch.
    """

    async def chat(self, *, model, messages, kind, tools=None, temperature=0,
                   max_tokens=1024, response_format=None):
        # Restraint scenarios must decline without calling any tool.
        if kind.startswith("agency.restraint"):
            return {"choices": [{"message": {"role": "assistant", "content": "Sorry, that's outside what I can do here.", "tool_calls": None}}]}
        is_tool_reply = messages and messages[-1]["role"] == "tool"
        if is_tool_reply:
            return {"choices": [{"message": {"role": "assistant", "content": "Done.", "tool_calls": None}}]}
        tools_by_name = {t["function"]["name"] for t in (tools or [])}
        call_fn = "staff_lookup" if "staff_lookup" in tools_by_name else next(iter(tools_by_name)) if tools_by_name else None
        if call_fn:
            return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": call_fn, "arguments": json.dumps({"name": "Mira Voss"})}}]}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "Not sure.", "tool_calls": None}}]}


def test_agency_tests_driver_appends_one_entry_per_scenario_and_scores_in_budget():
    run = BenchmarkRun(id="t", status="running", created_at="x", updated_at="x", base_url="b")
    score = asyncio.run(_agency_tests(_FakeChat(), run, "fake-model"))
    assert len(run.tests) == len(SCENARIOS)
    assert 0.0 <= score <= 35.0
    # The two restraint scenarios should pass with this fake client.
    restraint = [t for t in run.tests if "restraint" in t["name"]]
    assert restraint and all(t["ok"] for t in restraint)
    # At least the restraint points contribute.
    assert score > 0.0


def test_distributed_scores_sum_to_agency_budget_at_cent_precision():
    scores = _distributed_scores(35.0, len(SCENARIOS))
    assert len(scores) == len(SCENARIOS)
    assert round(sum(scores), 2) == 35.0
    assert all(round(score, 2) == score for score in scores)
