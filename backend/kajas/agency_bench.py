"""Agency benchmark — tool-use scenarios in a fixed company sandbox.

This sub-suite is an original adaptation inspired by the "Agency
Benchmark" overview by Luke's Dev Lab
(https://github.com/lukesdevlab/youtube/blob/main/agency-benchmark-overview.html):
a fixed set of prompts run against a controlled company sandbox with a
fixed clock, so pass rates reflect genuine agency rather than luck.

The sandbox is deterministic and in-process: tool calls are answered by
Python functions over a small company dataset. Four capabilities are
supported — staff lookup, room booking, incident reporting, and currency
conversion. A ``kb_search`` tool is advertised as a decoy in some
scenarios so we can score *restraint* and *focus*.

Each scenario declares its own skill area, a fixed user prompt, and a
pass checker. Scenarios never persist global state; they score
independently so results can be grouped by skill area.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

# Fixed sandbox clock so "tomorrow" / "this Friday" resolve deterministically.
SANDBOX_CLOCK = datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)

SYSTEM_PROMPT = (
    "You are an assistant operating inside Halcyon Systems. Use tools to act on the user's behalf. "
    "Only four capabilities are supported: staff lookup, room booking, incident reporting, and "
    "currency conversion. Anything else (for example weather forecasts or knowledge-base search) is "
    "out of scope — politely decline instead of calling a tool. "
    "When you book a room, always provide ISO-8601 UTC start and end times. "
    "When you convert currency, use the official Halcyon exchange rates via the provided tools "
    "and state the final numeric amount plainly (no thousands separators, e.g. 29850.75 not 29,850.75)."
)

# ---------------------------------------------------------------------------
# Company dataset (original names — not copied from any external suite)
# ---------------------------------------------------------------------------

EMPLOYEES: list[dict[str, Any]] = [
    {"id": "S01", "name": "Mira Voss", "email": "mira.voss@halcyon.example", "department": "Platform", "team": "Runway", "role": "Engineer", "active": True, "manager_id": "S07"},
    {"id": "S02", "name": "Tomas Iyer", "email": "tomas.iyer@halcyon.example", "department": "Platform", "team": "Runway", "role": "Engineer", "active": True, "manager_id": "S07"},
    {"id": "S03", "name": "Nadia Brar", "email": "nadia.brar@halcyon.example", "department": "Platform", "team": "Telemetry", "role": "Engineer", "active": True, "manager_id": "S07"},
    {"id": "S04", "name": "Oren Piper", "email": "oren.piper@halcyon.example", "department": "Support", "team": "On-call", "role": "Agent", "active": True, "manager_id": "S09"},
    {"id": "S05", "name": "Lena Ford", "email": "lena.ford@halcyon.example", "department": "Growth", "team": "Direct", "role": "Rep", "active": True, "manager_id": "S10"},
    {"id": "S06", "name": "Rashid Cole", "email": "rashid.cole@halcyon.example", "department": "Platform", "team": "Runway", "role": "Engineer", "active": False, "manager_id": "S07"},
    {"id": "S07", "name": "Priya Sloan", "email": "priya.sloan@halcyon.example", "department": "Platform", "team": "Runway", "role": "Manager", "active": True, "manager_id": "S10"},
    {"id": "S08", "name": "Kai Mendez", "email": "kai.mendez@halcyon.example", "department": "Platform", "team": "Telemetry", "role": "Engineer", "active": True, "manager_id": "S07"},
    {"id": "S09", "name": "Dana Roth", "email": "dana.roth@halcyon.example", "department": "Support", "team": "On-call", "role": "Manager", "active": True, "manager_id": "S10"},
    {"id": "S10", "name": "Ivo Marchetti", "email": "ivo.marchetti@halcyon.example", "department": "Executive", "team": "Leadership", "role": "CEO", "active": True, "manager_id": None},
]

EMPLOYEE_BY_ID = {e["id"]: e for e in EMPLOYEES}
EMPLOYEE_BY_NAME = {e["name"].lower(): e for e in EMPLOYEES}

# The sandbox booking/incident state. Scenarios reset this to a clean state
# so they score independently; the agency driver does not persist across
# scenarios unless a scenario explicitly carries state.
BOOKINGS: list[dict[str, Any]] = []
INCIDENTS: list[dict[str, Any]] = []

# Official Halcyon exchange rates (relative to USD).
RATES_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "JPY": 0.0067,
    "GBP": 1.27,
}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _staff_lookup(staff_id: str | None = None, name: str | None = None, email: str | None = None) -> dict[str, Any] | None:
    if staff_id:
        return EMPLOYEE_BY_ID.get(staff_id)
    if email:
        for e in EMPLOYEES:
            if e["email"].lower() == (email or "").lower():
                return e
        return None
    if name:
        return EMPLOYEE_BY_NAME.get(name.lower())
    return None


def _directory_search(department: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    active_only = bool(active_only)
    out = []
    for e in EMPLOYEES:
        if department and e["department"].lower() != department.lower():
            continue
        if active_only and not e["active"]:
            continue
        out.append(e)
    return out


def _check_availability(room: str, start_iso: str, end_iso: str) -> dict[str, Any]:
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    for b in BOOKINGS:
        if b["room"].lower() != room.lower():
            continue
        bstart = _parse_iso(b["start"])
        bend = _parse_iso(b["end"])
        if start < bend and end > bstart:
            return {"room": room, "available": False, "conflict": b}
    return {"room": room, "available": True, "conflict": None}


def _book_room(room: str, start_iso: str, end_iso: str, purpose: str | None = None) -> dict[str, Any]:
    avail = _check_availability(room, start_iso, end_iso)
    if not avail["available"]:
        return {"booked": False, "error": "room is already booked for that time", "conflict": avail["conflict"]}
    booking = {"room": room, "start": start_iso, "end": end_iso, "purpose": purpose}
    BOOKINGS.append(booking)
    return {"booked": True, "booking": booking}


def _create_incident(title: str, severity: str, assignee_id: str | None = None) -> dict[str, Any]:
    if severity.lower() not in {"low", "medium", "high"}:
        return {"created": False, "error": "severity must be low, medium, or high"}
    assignee = EMPLOYEE_BY_ID.get(assignee_id) if assignee_id else None
    incident = {"id": f"INC-{len(INCIDENTS) + 2001}", "title": title, "severity": severity.lower(),
                "assignee_id": assignee_id, "assignee": assignee}
    INCIDENTS.append(incident)
    return {"created": True, "incident": incident}


def _get_exchange_rate(from_currency: str, to_currency: str) -> dict[str, Any]:
    # RATES_TO_USD[c] is the USD value of one unit of c. The rate "to per
    # from" is therefore usd_value(from) / usd_value(to).
    f = RATES_TO_USD.get(from_currency.upper())
    t = RATES_TO_USD.get(to_currency.upper())
    if f is None or t is None:
        return {"error": f"unknown currency pair {from_currency}->{to_currency}"}
    rate = round(f / t, 6)
    return {"from": from_currency.upper(), "to": to_currency.upper(), "rate": rate}


def _convert_currency(amount: float, from_currency: str, to_currency: str) -> dict[str, Any]:
    f = RATES_TO_USD.get(from_currency.upper())
    t = RATES_TO_USD.get(to_currency.upper())
    if f is None or t is None:
        return {"error": f"unknown currency pair {from_currency}->{to_currency}"}
    converted = round(amount * (f / t), 2)
    return {"amount": amount, "from": from_currency.upper(), "to": to_currency.upper(),
            "converted": converted, "rate": round(f / t, 6)}


def _usd_to_eur(amount: float) -> dict[str, Any]:
    """Convert USD to EUR. Returns unrounded converted value so chaining stays exact."""
    rate = 1.0 / RATES_TO_USD["EUR"]  # 1 USD = 1/1.08 EUR
    converted = amount * rate
    return {"amount": amount, "from": "USD", "to": "EUR",
            "converted": converted, "rate": round(rate, 6)}


def _eur_to_jpy(amount: float) -> dict[str, Any]:
    """Convert EUR to JPY. Returns unrounded converted value so chaining stays exact."""
    rate = RATES_TO_USD["EUR"] / RATES_TO_USD["JPY"]  # 1 EUR = 1.08/0.0067 JPY
    converted = amount * rate
    return {"amount": amount, "from": "EUR", "to": "JPY",
            "converted": converted, "rate": round(rate, 6)}


def _kb_search(query: str) -> dict[str, Any]:
    # Decoy tool: always returns irrelevant content. Supported capabilities
    # never include KB search, so any call is a restraint failure.
    return {"query": query, "results": [], "note": "knowledge-base search is not a supported Halcyon capability"}


def _reset_sandbox() -> None:
    BOOKINGS.clear()
    INCIDENTS.clear()


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

def tool_schemas(include_kb_decoy: bool = True, exclude: list[str] | None = None) -> list[dict[str, Any]]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "staff_lookup",
                "description": "Look up a single Halcyon staff member by id, name, or email.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "staff_id": {"type": "string", "description": "Halcyon staff id, e.g. S03"},
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "directory_search",
                "description": "List or filter staff by department and/or active status.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "department": {"type": "string", "description": "Platform, Support, Growth, Executive"},
                        "active_only": {"type": "boolean"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_availability",
                "description": "See existing bookings for a room between ISO-8601 UTC start/end times.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["room", "start", "end"],
                    "properties": {
                        "room": {"type": "string"},
                        "start": {"type": "string", "description": "ISO-8601 UTC, e.g. 2026-05-29T14:00:00Z"},
                        "end": {"type": "string", "description": "ISO-8601 UTC"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "book_room",
                "description": "Reserve a room with precise ISO-8601 UTC start and end times.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["room", "start", "end"],
                    "properties": {
                        "room": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "purpose": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_incident",
                "description": "Log an incident with a severity and an assignee.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "severity"],
                    "properties": {
                        "title": {"type": "string"},
                        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                        "assignee_id": {"type": "string", "description": "Halcyon staff id"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_exchange_rate",
                "description": "Look up the official Halcyon exchange rate between two currencies.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["from_currency", "to_currency"],
                    "properties": {
                        "from_currency": {"type": "string"},
                        "to_currency": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "convert_currency",
                "description": "Convert an amount of currency using official Halcyon rates, including chained cross-rates.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["amount", "from_currency", "to_currency"],
                    "properties": {
                        "amount": {"type": "number"},
                        "from_currency": {"type": "string"},
                        "to_currency": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "usd_to_eur",
                "description": "Convert a USD amount to EUR using the official Halcyon rate.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["amount"],
                    "properties": {
                        "amount": {"type": "number", "description": "Amount in USD to convert"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "eur_to_jpy",
                "description": "Convert an EUR amount to JPY using the official Halcyon rate.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["amount"],
                    "properties": {
                        "amount": {"type": "number", "description": "Amount in EUR to convert"},
                    },
                },
            },
        },
    ]
    if include_kb_decoy:
        tools.append({
            "type": "function",
            "function": {
                "name": "kb_search",
                "description": "Search the internal knowledge base.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}},
                },
            },
        })
    if exclude:
        tools = [t for t in tools if t["function"]["name"] not in exclude]
    return tools


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "staff_lookup":
        result = _staff_lookup(**arguments)
        return {"error": "staff member not found"} if result is None else result
    if name == "directory_search":
        return {"staff": _directory_search(arguments.get("department"), arguments.get("active_only"))}
    if name == "check_availability":
        return _check_availability(arguments["room"], arguments["start"], arguments["end"])
    if name == "book_room":
        return _book_room(arguments["room"], arguments["start"], arguments["end"], arguments.get("purpose"))
    if name == "create_incident":
        return _create_incident(arguments["title"], arguments["severity"], arguments.get("assignee_id"))
    if name == "get_exchange_rate":
        return _get_exchange_rate(arguments["from_currency"], arguments["to_currency"])
    if name == "convert_currency":
        return _convert_currency(float(arguments["amount"]), arguments["from_currency"], arguments["to_currency"])
    if name == "usd_to_eur":
        return _usd_to_eur(float(arguments["amount"]))
    if name == "eur_to_jpy":
        return _eur_to_jpy(float(arguments["amount"]))
    if name == "kb_search":
        return _kb_search(arguments["query"])
    return {"error": f"unknown tool {name}"}


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

class Scenario:
    def __init__(
        self,
        id: str,
        area: str,
        prompt: str,
        check: Callable[[AgencyTrace], tuple[bool, str]],
        *,
        include_kb_decoy: bool = True,
        exclude_tools: list[str] | None = None,
        only_tools: list[str] | None = None,
        max_rounds: int = 8,
    ) -> None:
        self.id = id
        self.area = area
        self.prompt = prompt
        self._check = check
        self.include_kb_decoy = include_kb_decoy
        self.exclude_tools = exclude_tools or []
        self.only_tools = only_tools
        self.max_rounds = max_rounds

    def reset(self) -> None:
        _reset_sandbox()

    def check(self, trace: "AgencyTrace") -> tuple[bool, str]:
        return self._check(trace, self) if _takes_scenario(self._check) else self._check(trace)


# A trace summarises one scenario run for the pass-checker.
class AgencyTrace:
    def __init__(self) -> None:
        self.tool_calls: list[dict[str, Any]] = []  # ordered: {name, args}
        self.results: list[dict[str, Any]] = []     # ordered tool outputs
        self.final_text: str = ""

    def called(self, name: str) -> list[dict[str, Any]]:
        return [c for c in self.tool_calls if c["name"] == name]

    @property
    def tool_names(self) -> list[str]:
        return [c["name"] for c in self.tool_calls]

    @property
    def used_kb(self) -> bool:
        return "kb_search" in self.tool_names


def _takes_scenario(check: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(check).parameters
        return len(params) >= 2 and "scenario" in params
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Date helpers (resolving "tomorrow" / "this Friday" from the sandbox clock)
# ---------------------------------------------------------------------------

def _parse_iso(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def tomorrow() -> datetime:
    return SANDBOX_CLOCK + timedelta(days=1)


def this_friday() -> datetime:
    # 2026-05-28 is a Thursday; Friday is +1 day.
    return SANDBOX_CLOCK + timedelta(days=(4 - SANDBOX_CLOCK.weekday()) % 7 or 7)


def _at(dt: datetime, hour: int, minute: int = 0) -> datetime:
    return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Pass checkers. Each returns (ok, detail).
# ---------------------------------------------------------------------------

def _staff_department_is(name: str, department: str):
    def _check(t: AgencyTrace) -> tuple[bool, str]:
        emp = _staff_lookup(name=name)
        if not emp:
            return False, f"no staff member named {name}"
        calls = t.called("staff_lookup")
        if not calls:
            return False, "did not call staff_lookup"
        args = calls[0]["args"]
        got = None
        if args.get("name"):
            got = _staff_lookup(name=args["name"])
        elif args.get("staff_id"):
            got = EMPLOYEE_BY_ID.get(args["staff_id"])
        elif args.get("email"):
            got = _staff_lookup(email=args["email"])
        if not got:
            return False, f"lookup did not resolve: {args}"
        ok = got["department"] == department and department.lower() in t.final_text.lower()
        return (ok, f"department={got['department']}") if ok else (False, f"expected {department}, got {got['department']}")
    return _check


def _directory_lists(department: str, *, active_only: bool, expected_count: int | None = None):
    def _check(t: AgencyTrace) -> tuple[bool, str]:
        calls = t.called("directory_search")
        if not calls:
            lookups = t.called("staff_lookup")
            if lookups and expected_count is None:
                return True, f"resolved via {len(lookups)} staff_lookup calls (inefficient)"
            return False, "did not call directory_search"
        args = calls[0]["args"]
        if args.get("department", "").lower() != department.lower():
            return False, f"wrong department filter: {args.get('department')}"
        if active_only and not args.get("active_only"):
            return False, "missing active_only=true"
        result = t.results[-1] if t.results else {}
        staff = result.get("staff") if isinstance(result, dict) else None
        if staff is None:
            return True, "directory_search called with correct filters"
        if expected_count is not None and len(staff) != expected_count:
            return False, f"expected {expected_count} staff, tool saw {len(staff)}"
        return True, f"listed {len(staff)} {department} staff"
    return _check


def _booking_correct(room: str, start: datetime, end: datetime, *, purpose_substring: str | None = None, allow_check_only: bool = False):
    def _check(t: AgencyTrace) -> tuple[bool, str]:
        book = t.called("book_room")
        if not book:
            if allow_check_only and t.called("check_availability"):
                return True, "answered with an availability check"
            return False, "did not call book_room"
        if len(book) > 1:
            return False, f"made {len(book)} bookings (expected 1)"
        args = book[0]["args"]
        if args.get("room", "").lower() != room.lower():
            return False, f"wrong room: {args.get('room')}"
        try:
            got_start = _parse_iso(args["start"])
            got_end = _parse_iso(args["end"])
        except Exception as exc:
            return False, f"bad times: {exc}"
        if abs((got_start - start).total_seconds()) > 60:
            return False, f"start {got_start.isoformat()} != {start.isoformat()}"
        if abs((got_end - end).total_seconds()) > 60:
            return False, f"end {got_end.isoformat()} != {end.isoformat()}"
        if purpose_substring and purpose_substring.lower() not in str(args.get("purpose", "")).lower():
            return False, f"purpose missing '{purpose_substring}': {args.get('purpose')}"
        if not any(b["room"].lower() == room.lower() and _parse_iso(b["start"]) == got_start for b in BOOKINGS):
            return False, "booking not recorded in sandbox"
        return True, f"booked {room} {start.isoformat()}–{end.isoformat()}"
    return _check


def _incident_correct(title: str, severity: str, assignee_id: str | None = None, *, assignee_name: str | None = None, require_title: bool = True):
    def _check(t: AgencyTrace) -> tuple[bool, str]:
        calls = t.called("create_incident")
        if not calls:
            return False, "did not call create_incident"
        if len(calls) > 1:
            return False, f"made {len(calls)} incidents (expected 1)"
        args = calls[0]["args"]
        if require_title and args.get("title", "").strip().lower() != title.lower():
            return False, f"title '{args.get('title')}' != '{title}'"
        if str(args.get("severity", "")).lower() != severity.lower():
            return False, f"severity {args.get('severity')} != {severity}"
        got_assignee = args.get("assignee_id")
        if assignee_id and got_assignee != assignee_id:
            return False, f"assignee {got_assignee} != {assignee_id}"
        if assignee_name and got_assignee:
            emp = EMPLOYEE_BY_ID.get(got_assignee)
            if not emp or emp["name"] != assignee_name:
                return False, f"assignee resolves to {emp['name'] if emp else None} != {assignee_name}"
        return True, f"incident severity={severity} assignee={got_assignee}"
    return _check


def _currency_chain_tools(amount: float, from_c: str, to_c: str, via: str, tools: list[str]):
    """Requires chaining two dedicated conversion tools (e.g. usd_to_eur + eur_to_jpy)."""
    expected = _convert_currency(amount, from_c, to_c)["converted"]

    def _check(t: AgencyTrace) -> tuple[bool, str]:
        # Both chain tools must have been called exactly once
        for tool in tools:
            calls = t.called(tool)
            if not calls:
                return False, f"did not call {tool}"
            if len(calls) > 1:
                return False, f"called {tool} {len(calls)} times (expected 1)"

        # No other tools should have been called (only chain tools available)
        other = [c for c in t.tool_calls if c["name"] not in tools]
        if other:
            names = ", ".join(c["name"] for c in other)
            return False, f"unexpected tool calls: {names}"

        # Chain must flow: from_c -> via -> to_c
        first_args = t.called(tools[0])[0]["args"]
        second_args = t.called(tools[1])[0]["args"]
        try:
            first_amt = float(first_args["amount"])
            second_amt = float(second_args["amount"])
        except Exception as exc:
            return False, f"bad chain args: {exc}"
        # First tool receives the original amount; second receives the first tool's output
        if abs(first_amt - amount) > 0.001:
            return False, f"first tool amount {first_amt} != {amount}"
        # The second tool's input should match the first tool's converted output
        first_result = t.results[t.tool_calls.index(t.called(tools[0])[0])]
        first_converted = first_result.get("converted", 0) if isinstance(first_result, dict) else 0
        if abs(second_amt - first_converted) > 0.001:
            return False, f"second tool amount {second_amt} != first result {first_converted}"

        # Final text must contain the expected value
        plain = t.final_text.replace(",", "")
        if str(expected) not in t.final_text and str(expected) not in plain and f"{expected:g}" not in t.final_text and f"{expected:g}" not in plain:
            return False, f"final answer missing {expected} (got: {t.final_text[:120]!r})"

        return True, f"chained {amount} {from_c}->{via}->{to_c} via {' + '.join(tools)} = {expected}"
    return _check


def _currency_exact(amount: float, from_c: str, to_c: str):
    expected = _convert_currency(amount, from_c, to_c)["converted"]

    def _check(t: AgencyTrace) -> tuple[bool, str]:
        conv = t.called("convert_currency")
        if conv:
            args = conv[0]["args"]
            try:
                ok = (abs(float(args["amount"]) - amount) < 0.001
                      and args["from_currency"].upper() == from_c.upper()
                      and args["to_currency"].upper() == to_c.upper())
            except Exception as exc:
                return False, f"bad convert args: {exc}"
            if not ok:
                return False, f"convert args {args} != {amount} {from_c}->{to_c}"
        else:
            rates = t.called("get_exchange_rate")
            if not rates:
                return False, "did not call convert_currency or get_exchange_rate"
            args = rates[0]["args"]
            if args["from_currency"].upper() != from_c.upper() or args["to_currency"].upper() != to_c.upper():
                return False, f"rate pair {args} != {from_c}->{to_c}"
        if str(expected) not in t.final_text and f"{expected:g}" not in t.final_text:
            return False, f"final answer missing {expected} (got: {t.final_text[:120]!r})"
        return True, f"converted {amount} {from_c}->{to_c} = {expected}"
    return _check


def _restraint_no_tool():
    def _check(t: AgencyTrace) -> tuple[bool, str]:
        if t.tool_calls:
            names = ", ".join(t.tool_names)
            return False, f"called tools when it should decline: {names}"
        if not t.final_text.strip():
            return False, "declined but gave no answer text"
        return True, "correctly declined without calling tools"
    return _check


def _focus_ignore_kb(expected_tool: str):
    def _check(t: AgencyTrace) -> tuple[bool, str]:
        if t.used_kb:
            return False, "called kb_search decoy (focus failure)"
        if expected_tool not in t.tool_names:
            return False, f"did not call {expected_tool}"
        return True, f"used {expected_tool}, ignored kb decoy"
    return _check


# ---------------------------------------------------------------------------
# Scenario catalogue (a representative, deterministic subset of the suite).
# Prompts and dataset are original to Kajas.
# ---------------------------------------------------------------------------

SCENARIOS: list[Scenario] = [
    # --- Tool selection ---
    Scenario("agency.sel.mira_dept", "tool_selection", "Which department does Mira Voss belong to?",
             _staff_department_is("Mira Voss", "Platform")),
    Scenario("agency.sel.s03_dept", "tool_selection", "Pull up staff record S03 and report the department.",
             _staff_department_is("Nadia Brar", "Platform")),
    # --- Single step ---
    Scenario("agency.single.tomas_dept", "single_step", "What department is Tomas Iyer in?",
             _staff_department_is("Tomas Iyer", "Platform")),
    Scenario("agency.single.active_platform", "single_step", "Show me every active person in the Platform department.",
             _directory_lists("Platform", active_only=True, expected_count=5)),
    # active Platform = Mira, Tomas, Nadia, Priya, Kai = 5 (Rashid inactive)
    Scenario("agency.single.platform_count", "single_step", "How many staff records exist under Platform?",
             _directory_lists("Platform", active_only=False, expected_count=6)),
    # --- Scheduling ---
    Scenario("agency.sched.foxglove_tomorrow", "scheduling",
             "Reserve the Foxglove room tomorrow from 14:00 to 15:00 UTC for a daily standup.",
             _booking_correct("Foxglove", _at(tomorrow(), 14), _at(tomorrow(), 15),
                              purpose_substring="standup")),
    Scenario("agency.sched.north_friday", "scheduling",
             "Book the North Lab this Friday 09:00-10:00 UTC to plan the next sprint.",
             _booking_correct("North Lab", _at(this_friday(), 9), _at(this_friday(), 10),
                              purpose_substring="sprint")),
    Scenario("agency.sched.foxglove_avail", "scheduling",
             "Can I get the Foxglove room tomorrow between 14:00 and 15:00 UTC?",
             _booking_correct("Foxglove", _at(tomorrow(), 14), _at(tomorrow(), 15), allow_check_only=True)),
    # --- Multi-step / incidents & chains ---
    Scenario("agency.chain.tomas_manager_incident", "multi_step",
             "Raise a medium incident titled 'Access review' against Tomas Iyer's manager.",
             _incident_correct("Access review", "medium", assignee_id="S07")),
    Scenario("agency.chain.support_lead_incident", "multi_step",
             "Open a high incident and route it to whoever runs the On-call team.",
             _incident_correct("", "high", assignee_name="Dana Roth", require_title=False)),
    Scenario("agency.chain.runway_incident", "multi_step",
             "Create a medium incident and put it on someone from the Runway team.",
             _incident_correct("", "medium", assignee_id="S07", require_title=False)),
    # --- Currency ---
    Scenario("agency.cur.eur_usd", "currency", "Convert 750 EUR to USD at the official Halcyon rate.",
             _currency_exact(750, "EUR", "USD")),
    Scenario("agency.cur.usd_jpy", "currency",
             "What is 200 USD worth in JPY, using the official Halcyon rate? "
             "There is no direct USD-to-JPY conversion available — you must chain the provided tools to get the answer.",
             _currency_chain_tools(200, "USD", "JPY", "EUR", ["usd_to_eur", "eur_to_jpy"]),
             only_tools=["usd_to_eur", "eur_to_jpy"]),
    # --- Restraint & focus ---
    Scenario("agency.restraint.weather", "restraint",
             "Will it rain in Lisbon tomorrow afternoon?",
             _restraint_no_tool(), include_kb_decoy=True),
    Scenario("agency.restraint.greeting", "restraint",
             "Got it, that's exactly what I needed. Cheers!",
             _restraint_no_tool(), include_kb_decoy=True),
    Scenario("agency.focus.mira_team_direct", "focus",
             "Which team is Mira Voss on? Use the staff directory directly.",
             _focus_ignore_kb("staff_lookup"), include_kb_decoy=True),
    Scenario("agency.focus.mira_team_plain", "focus",
             "Which team is Mira Voss on?",
             _focus_ignore_kb("staff_lookup"), include_kb_decoy=True),
]


