"""Local model benchmark runner.

Benchmarks target OpenAI-compatible local endpoints such as llama.cpp,
Ollama, and LM Studio. The runner is intentionally self-contained: it
discovers a model, executes a fixed set of probes, scores the result,
and persists every raw response for later inspection.
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field

from . import agency_bench
from .paths import data_dir


BenchmarkStatus = Literal["running", "completed", "failed", "cancelled"]
CONTEXT_PROMPT_VERSION = "2026-06-26.strict-output-v2"
SCORING_VERSION = "2026-06-26.tool50-context25-coding10-latency15"


class BenchmarkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    api_key: str | None = None
    custom_headers: dict[str, str] = Field(default_factory=dict)
    model: str | None = None
    max_context_tokens: int | None = Field(default=32768, ge=1024, le=262144)
    coding_judge_tool: Literal["codex", "pi"] = "codex"
    coding_judge_model: str = "gpt-5.5"


class BenchmarkRun(BaseModel):
    id: str
    status: BenchmarkStatus
    created_at: str
    updated_at: str
    base_url: str
    model: str | None = None
    configured_model: str | None = None
    context_window: int | None = None
    effective_context_window: int | None = None
    max_context_tokens: int | None = None
    coding_judge_tool: Literal["codex", "pi"] = "codex"
    coding_judge_model: str = "gpt-5.5"
    scoring_version: str | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    total_score: float = 0.0
    usable: bool = False
    summary: str | None = None
    error: str | None = None
    tests: list[dict[str, Any]] = Field(default_factory=list)
    raw: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: list[float] = Field(default_factory=list)


class BenchmarkStore:
    def root(self) -> Path:
        p = data_dir() / "benchmarks"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def path(self, run_id: str) -> Path:
        return self.root() / f"{run_id}.json"

    def create(self, payload: BenchmarkInput) -> BenchmarkRun:
        now = _now()
        run = BenchmarkRun(
            id=f"bench-{uuid.uuid4().hex[:12]}",
            status="running",
            created_at=now,
            updated_at=now,
            base_url=payload.base_url.rstrip("/"),
            configured_model=payload.model or None,
            max_context_tokens=payload.max_context_tokens,
            coding_judge_tool=payload.coding_judge_tool,
            coding_judge_model=payload.coding_judge_model,
            scoring_version=SCORING_VERSION,
        )
        self.save(run)
        return run

    def save(self, run: BenchmarkRun) -> None:
        run.updated_at = _now()
        self.path(run.id).write_text(
            json.dumps(run.model_dump(mode="json"), indent=2), encoding="utf-8"
        )

    def read(self, run_id: str) -> BenchmarkRun | None:
        path = self.path(run_id)
        if not path.exists():
            return None
        return _normalize_legacy_scoring(
            BenchmarkRun.model_validate_json(path.read_text(encoding="utf-8"))
        )

    def delete(self, run_id: str) -> bool:
        path = self.path(run_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list(self) -> list[BenchmarkRun]:
        runs: list[BenchmarkRun] = []
        for path in sorted(self.root().glob("*.json")):
            try:
                runs.append(
                    _normalize_legacy_scoring(
                        BenchmarkRun.model_validate_json(path.read_text(encoding="utf-8"))
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs

    def mark_running_failed_on_startup(self) -> list[str]:
        failed: list[str] = []
        for run in self.list():
            if run.status != "running":
                continue
            run.status = "failed"
            run.error = "Server restarted while benchmark was running."
            self.save(run)
            failed.append(run.id)
        return failed


DEFAULT_BENCHMARK_STORE = BenchmarkStore()
_BENCHMARK_TASKS: dict[str, asyncio.Task[None]] = {}


async def run_benchmark(
    run_id: str,
    payload: BenchmarkInput,
    *,
    store: BenchmarkStore = DEFAULT_BENCHMARK_STORE,
) -> None:
    run = store.read(run_id)
    if run is None:
        return
    client = OpenAICompatClient(payload, run)
    try:
        model, metadata = await client.discover_model()
        run.model = model
        run.context_window = _extract_context_window(metadata)
        run.effective_context_window = _effective_context_window(
            run.context_window, payload.max_context_tokens
        )
        store.save(run)

        tool_score = await _tooling_tests(client, run, model)
        run.scores["tool_calling"] = round(tool_score, 2)
        store.save(run)
        context_score = await _context_tests(client, run, model)
        run.scores["context_retrieval"] = round(context_score, 2)
        store.save(run)
        coding_score = await _coding_test(client, run, model)
        run.scores["coding"] = round(coding_score, 2)
        store.save(run)
        latency_score = _latency_score(run)

        run.scores = {
            "tool_calling": round(tool_score, 2),
            "context_retrieval": round(context_score, 2),
            "coding": round(coding_score, 2),
            "latency_reliability": round(latency_score, 2),
        }
        run.total_score = round(sum(run.scores.values()), 2)
        run.usable = run.total_score >= 70.0
        run.summary = _summary(run)
        run.status = "completed"
        store.save(run)
    except asyncio.CancelledError:
        run.status = "cancelled"
        run.error = "Benchmark run was cancelled."
        run.scores["latency_reliability"] = round(_latency_score(run), 2)
        run.total_score = round(sum(run.scores.values()), 2)
        run.usable = False
        store.save(run)
        raise
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)
        run.scores["latency_reliability"] = round(_latency_score(run), 2)
        run.total_score = round(sum(run.scores.values()), 2)
        run.usable = False
        store.save(run)
    finally:
        await client.close()


class OpenAICompatClient:
    def __init__(self, payload: BenchmarkInput, run: BenchmarkRun) -> None:
        self.payload = payload
        self.run = run
        self.base_url = payload.base_url.rstrip("/")
        headers = dict(payload.custom_headers)
        if payload.api_key:
            headers.setdefault("authorization", f"Bearer {payload.api_key}")
        self.client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(180.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def discover_model(self) -> tuple[str, dict[str, Any]]:
        data = await self.get("/models", "model_list")
        models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(models, list) or not models:
            raise RuntimeError("/models did not return any models")
        ids = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
        model = self.payload.model or (ids[0] if ids else None)
        if not model:
            raise RuntimeError("could not determine model id from /models")
        metadata = next((m for m in models if isinstance(m, dict) and m.get("id") == model), {})
        try:
            detail = await self.get(f"/models/{quote(model, safe='')}", "model_detail")
            if isinstance(detail, dict):
                metadata = {**metadata, **detail}
        except Exception as exc:  # noqa: BLE001
            self.run.raw.append({"kind": "model_detail_error", "error": str(exc)})
        return model, metadata if isinstance(metadata, dict) else {}

    async def get(self, path: str, kind: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        start = time.perf_counter()
        response = await self.client.get(url)
        elapsed = (time.perf_counter() - start) * 1000
        self.run.latency_ms.append(elapsed)
        text = response.text
        self.run.raw.append(
            {
                "kind": kind,
                "request": {"method": "GET", "url": url},
                "status_code": response.status_code,
                "latency_ms": elapsed,
                "response": _json_or_text(text),
            }
        )
        response.raise_for_status()
        return response.json()

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        kind: str,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, str] | None = None,
        temperature: float = 0,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools is not None:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format is not None:
            payload["response_format"] = response_format
        start = time.perf_counter()
        response = await self.client.post(f"{self.base_url}/chat/completions", json=payload)
        elapsed = (time.perf_counter() - start) * 1000
        self.run.latency_ms.append(elapsed)
        text = response.text
        self.run.raw.append(
            {
                "kind": kind,
                "request": _redact_payload(payload),
                "status_code": response.status_code,
                "latency_ms": elapsed,
                "response": _json_or_text(text),
            }
        )
        response.raise_for_status()
        return response.json()


async def _tooling_tests(client: OpenAICompatClient, run: BenchmarkRun, model: str) -> float:
    score = 0.0

    schema_ok = await _schema_tool_test(client, run, model)
    score += 4.0 if schema_ok else 0.0

    multi_ok = await _multi_step_tool_test(client, run, model)
    score += 6.0 if multi_ok else 0.0

    json_ok = await _json_only_test(client, run, model)
    score += 5.0 if json_ok else 0.0

    agency_score = await _agency_tests(client, run, model)
    score += agency_score
    return score


async def _agency_tests(client: OpenAICompatClient, run: BenchmarkRun, model: str) -> float:
    """Run the agency scenario suite and score pass-rate within a 35pt budget.

    Each scenario runs its own multi-round tool-calling loop against the
    deterministic Halcyon Systems sandbox. Tool calls are dispatched in
    process and their results fed back so models can chain steps.
    """
    scenarios = agency_bench.SCENARIOS
    total = len(scenarios)
    max_score = 35.0
    awarded_scores = _distributed_scores(max_score, total)
    earned_score = 0.0
    for index, sc in enumerate(scenarios):
        sc.reset()
        tools = agency_bench.tool_schemas(include_kb_decoy=sc.include_kb_decoy, exclude=sc.exclude_tools)
        if sc.only_tools:
            tools = [t for t in tools if t["function"]["name"] in sc.only_tools]
        trace = agency_bench.AgencyTrace()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": agency_bench.SYSTEM_PROMPT},
            {"role": "user", "content": sc.prompt},
        ]
        try:
            finished = False
            last_assistant_message: dict[str, Any] | None = None
            for _rnd in range(sc.max_rounds):
                data = await client.chat(
                    model=model,
                    kind=sc.id,
                    tools=tools,
                    messages=messages,
                    temperature=0,
                    max_tokens=2048,
                )
                message = data["choices"][0]["message"]
                messages.append(message)
                last_assistant_message = message
                calls = message.get("tool_calls") or []
                if not calls:
                    trace.final_text = message.get("content") or ""
                    finished = True
                    break
                for call in calls:
                    fn = call.get("function", {}).get("name")
                    raw_args = call.get("function", {}).get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args)
                    except Exception:  # noqa: BLE001
                        args = {}
                    result = agency_bench.dispatch_tool(fn, args)
                    trace.tool_calls.append({"name": fn, "args": args})
                    trace.results.append(result)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(result, default=str),
                        }
                    )
            if not finished:
                # Hit the round budget without a no-tool terminal turn; keep
                # whatever final assistant content we last saw, if any.
                trace.final_text = (last_assistant_message or {}).get("content", "")
            ok, detail = sc.check(trace)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)
        if ok:
            earned_score += awarded_scores[index]
        run.tests.append({
            "name": sc.id,
            "ok": ok,
            "score": awarded_scores[index] if ok else 0.0,
            "detail": f"[{sc.area}] {detail}",
        })
    return round(earned_score, 2)


def _distributed_scores(max_score: float, count: int) -> list[float]:
    """Split a score budget into cent-precision rows that sum exactly."""
    if count <= 0:
        return []
    total_cents = int(round(max_score * 100))
    base, remainder = divmod(total_cents, count)
    return [
        (base + (1 if index < remainder else 0)) / 100
        for index in range(count)
    ]


def _normalize_legacy_scoring(run: BenchmarkRun) -> BenchmarkRun:
    """Project old stored benchmark runs onto the current score weights.

    Benchmark JSON is persisted on disk, including per-test scores. Older
    builds used a 10-point agency budget and a 35-point coding bucket, while
    the current UI labels tool/coding as 50/10. Without this adapter, old runs
    render as internally inconsistent rows such as agency=0.59 under a 50pt
    tool-calling bucket.
    """
    if run.scoring_version == SCORING_VERSION:
        return run
    if not run.tests:
        return run

    tests_by_name = {str(test.get("name") or ""): test for test in run.tests}

    tool_score = 0.0
    for name, points in {
        "tool_schema": 4.0,
        "tool_multistep": 6.0,
        "json_only": 5.0,
    }.items():
        test = tests_by_name.get(name)
        if not test:
            continue
        score = points if test.get("ok") else 0.0
        test["score"] = score
        tool_score += score

    agency_points = _distributed_scores(35.0, len(agency_bench.SCENARIOS))
    for scenario, points in zip(agency_bench.SCENARIOS, agency_points):
        test = tests_by_name.get(scenario.id)
        if not test:
            continue
        score = points if test.get("ok") else 0.0
        test["score"] = score
        tool_score += score

    context_score = 0.0
    for test in run.tests:
        if not str(test.get("name") or "").startswith("context_"):
            continue
        score = 6.25 if test.get("ok") else 0.0
        test["score"] = score
        context_score += score
    context_score = min(25.0, context_score)

    coding_score = 0.0
    coding_test = tests_by_name.get("coding_flappy_game")
    if coding_test:
        old_score = _float_score(coding_test.get("score"))
        if old_score > 10.0:
            # Legacy coding was judged on a 35pt budget. Preserve relative
            # quality while fitting the current 10pt bucket.
            coding_score = round(min(10.0, old_score * (10.0 / 35.0)), 2)
        else:
            coding_score = round(min(10.0, old_score), 2)
        if not coding_test.get("ok"):
            coding_score = min(coding_score, 6.99)
        coding_test["score"] = coding_score

    run.scores = {
        "tool_calling": round(tool_score, 2),
        "context_retrieval": round(context_score, 2),
        "coding": round(coding_score, 2),
        "latency_reliability": round(_latency_score(run), 2),
    }
    run.total_score = round(sum(run.scores.values()), 2)
    run.usable = run.total_score >= 70.0
    run.scoring_version = SCORING_VERSION
    return run


def _float_score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def _schema_tool_test(client: OpenAICompatClient, run: BenchmarkRun, model: str) -> bool:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "record_measurement",
                "description": "Record one benchmark measurement.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sample_id", "value", "unit", "passed"],
                    "properties": {
                        "sample_id": {"type": "string"},
                        "value": {"type": "number"},
                        "unit": {"type": "string", "enum": ["ms", "tokens", "score"]},
                        "passed": {"type": "boolean"},
                    },
                },
            },
        }
    ]
    ok = False
    detail = ""
    try:
        data = await client.chat(
            model=model,
            kind="tool_schema",
            tools=tools,
            messages=[
                {"role": "system", "content": "Use the provided tool. Do not answer in text."},
                {
                    "role": "user",
                    "content": (
                        "Record sample alpha-7 with value 42.5 milliseconds. "
                        "The measurement passed."
                    ),
                },
            ],
        )
        calls = _tool_calls(data)
        args = _tool_args(calls, "record_measurement")
        ok = (
            args.get("sample_id") == "alpha-7"
            and abs(float(args.get("value", -1)) - 42.5) < 0.001
            and args.get("unit") == "ms"
            and args.get("passed") is True
        )
        detail = "tool call matched schema" if ok else f"unexpected tool args: {args}"
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
    run.tests.append({"name": "tool_schema", "ok": ok, "score": 4.0 if ok else 0.0, "detail": detail})
    return ok


async def _multi_step_tool_test(client: OpenAICompatClient, run: BenchmarkRun, model: str) -> bool:
    tools = [
        _function_tool("add", {"a": "number", "b": "number"}),
        _function_tool("multiply", {"a": "number", "b": "number"}),
        _function_tool("submit_answer", {"answer": "number"}),
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Use tools to solve the task. Submit the final numeric answer with submit_answer. "
                "You may need multiple tool calls."
            ),
        },
        {"role": "user", "content": "Add 17 and 25. Multiply that result by 3. Submit the answer."},
    ]
    ok = False
    detail = ""
    try:
        final_answer: float | None = None
        for step in range(5):
            data = await client.chat(
                model=model,
                kind=f"tool_multistep_{step + 1}",
                tools=tools,
                messages=messages,
            )
            message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            messages.append(message)
            if not calls:
                detail = "model stopped without tool calls"
                break
            for call in calls:
                fn = call.get("function", {}).get("name")
                args = json.loads(call.get("function", {}).get("arguments") or "{}")
                result: Any
                if fn == "add":
                    result = float(args["a"]) + float(args["b"])
                elif fn == "multiply":
                    result = float(args["a"]) * float(args["b"])
                elif fn == "submit_answer":
                    final_answer = float(args["answer"])
                    result = {"accepted": True}
                else:
                    result = {"error": f"unknown tool {fn}"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps({"result": result}),
                    }
                )
            if final_answer is not None:
                ok = abs(final_answer - 126.0) < 0.001
                detail = f"submitted {final_answer}"
                break
        if not detail:
            detail = "no final answer submitted"
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
    run.tests.append({"name": "tool_multistep", "ok": ok, "score": 6.0 if ok else 0.0, "detail": detail})
    return ok


def _strip_markdown_fence(content: str) -> tuple[str, bool]:
    """Strip exactly one surrounding markdown code fence, if present.

    Accepts ```` ```json ... ``` ```` and bare ```` ``` ... ``` ````. Only a
    single fence wrapping the whole content is removed; JSON buried inside
    prose is intentionally NOT extracted, so the json_only test still
    measures "returned (only) JSON" rather than "contains JSON".

    Returns the (possibly stripped) text and whether a fence was removed.
    """
    stripped = content.strip()
    if not stripped.startswith("```"):
        return content, False
    # Drop the opening fence and an optional ``json`` language tag.
    first_newline = stripped.find("\n")
    if first_newline == -1:
        return content, False
    opener = stripped[:first_newline].strip()
    if opener not in ("```", "```json", "```JSON"):
        return content, False
    body = stripped[first_newline + 1:].strip()
    if not body.endswith("```"):
        return content, False
    body = body[:-3].strip()
    return body, True


async def _json_only_test(client: OpenAICompatClient, run: BenchmarkRun, model: str) -> bool:
    ok = False
    detail = ""
    try:
        data = await client.chat(
            model=model,
            kind="json_only",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return only valid JSON."},
                {
                    "role": "user",
                    "content": (
                        "Return an object with keys verdict, numbers, and checksum. "
                        "verdict must be 'pass', numbers must be [3, 5, 8], checksum must be 16."
                    ),
                },
            ],
            max_tokens=256,
        )
        content = data["choices"][0]["message"].get("content") or ""
        raw, stripped = _strip_markdown_fence(content)
        parsed = json.loads(raw)
        ok = (
            parsed.get("verdict") == "pass"
            and parsed.get("numbers") == [3, 5, 8]
            and parsed.get("checksum") == 16
        )
        if ok:
            detail = "valid exact JSON"
            if stripped:
                detail += " (after stripping markdown fence)"
        else:
            detail = f"unexpected JSON: {parsed}"
            if stripped:
                detail += " (after stripping markdown fence)"
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
    run.tests.append({"name": "json_only", "ok": ok, "score": 5.0 if ok else 0.0, "detail": detail})
    return ok


async def _context_tests(client: OpenAICompatClient, run: BenchmarkRun, model: str) -> float:
    window = run.effective_context_window or 8192
    targets = [(25, 0.25), (50, 0.5), (75, 0.75), (100, 0.95)]
    score = 0.0
    for label, ratio in targets:
        token_budget = max(512, int(window * ratio))
        needle = f"KJAS-NEEDLE-{label}-{uuid.uuid4().hex[:8]}"
        prompt = _haystack_prompt(token_budget, needle)
        ok = False
        detail = ""
        try:
            data = await client.chat(
                model=model,
                kind=f"context_{label}",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are running an exact extraction benchmark. "
                            "Your final assistant content must contain only the hidden token. "
                            "Do not explain, quote, summarize, or add any other words."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
            )
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = (message.get("content") or "").strip()
            ok = content == needle
            if ok:
                detail = "exact needle retrieved"
            else:
                finish = choice.get("finish_reason")
                reasoning = (message.get("reasoning_content") or "").strip()
                if not content and reasoning:
                    detail = (
                        f"expected {needle}, got empty final content; finish_reason={finish}; "
                        "model spent output budget in reasoning_content"
                    )
                else:
                    detail = f"expected {needle}, got {content[:120]!r}; finish_reason={finish}"
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
        per_test = 6.25 if ok else 0.0
        score += per_test
        run.tests.append(
            {
                "name": f"context_{label}",
                "ok": ok,
                "score": per_test,
                "detail": detail,
                "token_budget": token_budget,
                "prompt_version": CONTEXT_PROMPT_VERSION,
            }
        )
    return score


async def _coding_test(client: OpenAICompatClient, run: BenchmarkRun, model: str) -> float:
    artifact = ""
    generation_ok = False
    judge_score = 0.0
    detail = ""
    try:
        data = await client.chat(
            model=model,
            kind="coding_generation",
            messages=[
                {
                    "role": "system",
                    "content": "You write compact, runnable browser games. Return only one HTML document.",
                },
                {
                    "role": "user",
                    "content": (
                        "Create a single-file Flappy Bird style browser game. It must include HTML, CSS, "
                        "and JavaScript in one document, keyboard controls, collision detection, scoring, "
                        "restart after game over, and no external assets."
                    ),
                },
            ],
            max_tokens=4096,
        )
        artifact = data["choices"][0]["message"].get("content") or ""
        generation_ok = all(s in artifact.lower() for s in ["<html", "<script", "collision", "score"])
        judge = _run_external_coding_judge(
            artifact=artifact,
            tool=run.coding_judge_tool,
            model=run.coding_judge_model,
            run=run,
        )
        parsed = _extract_json_object(judge["text"])
        judge_score = max(0.0, min(10.0, float(parsed.get("score", 0))))
        if not generation_ok:
            judge_score = min(judge_score, 3.5)
        detail = parsed.get("notes") or "judge returned a score"
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
    run.tests.append(
        {
            "name": "coding_flappy_game",
            "ok": judge_score >= 7.0,
            "score": round(judge_score, 2),
            "detail": detail,
            "artifact": artifact,
        }
    )
    return judge_score


def _latency_score(run: BenchmarkRun) -> float:
    total_tests = max(1, len(run.tests))
    reliability = len([t for t in run.tests if t.get("ok")]) / total_tests
    if not run.latency_ms:
        return 0.0
    avg = sum(run.latency_ms) / len(run.latency_ms)
    if avg <= 1500:
        latency = 1.0
    elif avg >= 30000:
        latency = 0.0
    else:
        latency = 1.0 - ((avg - 1500) / 28500)
    return 15.0 * ((0.7 * reliability) + (0.3 * latency))


def _run_external_coding_judge(
    *,
    artifact: str,
    tool: Literal["codex", "pi"],
    model: str,
    run: BenchmarkRun,
) -> dict[str, str]:
    if not shutil.which(tool):
        raise RuntimeError(f"{tool} is not available for coding judge")
    prompt = _coding_judge_prompt(artifact)
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="kajas-benchmark-judge-") as tmp:
        tmp_path = Path(tmp)
        if tool == "codex":
            proc = _run_codex_judge(tmp_path, prompt, model)
            text = _extract_codex_text(proc.stdout)
        else:
            prompt_path = tmp_path / "judge.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            proc = _run_pi_judge(tmp_path, prompt_path, model)
            text = _extract_pi_text(proc.stdout)
    elapsed = (time.perf_counter() - start) * 1000
    run.latency_ms.append(elapsed)
    run.raw.append(
        {
            "kind": "coding_judge_external",
            "request": {
                "tool": tool,
                "model": model,
                "prompt": prompt,
            },
            "status_code": proc.returncode,
            "latency_ms": elapsed,
            "response": {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "text": text,
            },
        }
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{tool} judge failed with code {proc.returncode}: {detail[:500]}")
    if not text.strip():
        raise RuntimeError(f"{tool} judge returned no final text")
    return {"text": text}


def _run_codex_judge(
    cwd: Path,
    prompt: str,
    model: str,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "codex",
        "exec",
        "--json",
        "--cd",
        str(cwd),
        "--skip-git-repo-check",
    ]
    if model and model != "default":
        cmd += ["--model", model]
    cmd.append("-")
    return subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=300,
        check=False,
    )


def _run_pi_judge(
    cwd: Path,
    prompt_path: Path,
    model: str,
) -> subprocess.CompletedProcess[str]:
    cmd = ["pi", "--print", "--mode", "json"]
    if model and model != "default":
        cmd += ["--model", model]
    cmd += [f"@{prompt_path}", "Return only the JSON requested in the attached judge brief."]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=300,
        check=False,
    )


def _coding_judge_prompt(artifact: str) -> str:
    return (
        "You are an external benchmark judge. Evaluate the submitted single-file browser game.\n"
        "Return only valid JSON with this shape:\n"
        '{"score": number, "notes": string}\n\n'
        "Score must be from 0 to 10. Award points for:\n"
        "- completeness as a playable Flappy Bird style game\n"
        "- runnable single-file HTML structure\n"
        "- keyboard controls\n"
        "- game loop and animation\n"
        "- collision detection\n"
        "- scoring\n"
        "- restart after game over\n"
        "- code clarity and maintainability\n\n"
        "Do not reward prose-only answers. If the artifact is not runnable code, score it below 3.\n\n"
        "SUBMITTED ARTIFACT START\n"
        f"{artifact[:50000]}\n"
        "SUBMITTED ARTIFACT END\n"
    )


def _extract_codex_text(stdout: str) -> str:
    texts: list[str] = []
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = data.get("item") or {}
        if item.get("type") != "agent_message":
            continue
        if data.get("type") in {"item.completed", "item.created"}:
            text = _flatten_content(item.get("text") or item.get("content") or "")
            if text:
                texts.append(text)
    return texts[-1] if texts else ""


def _extract_pi_text(stdout: str) -> str:
    texts: list[str] = []
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") != "message_end":
            continue
        msg = data.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        text = _flatten_content(msg.get("content") or "")
        if text:
            texts.append(text)
    return texts[-1] if texts else ""


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item.get("text") or ""))
                elif "content" in item:
                    parts.append(str(item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content) if content is not None else ""


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(stripped[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("judge JSON must be an object")
    return data


def _function_tool(name: str, props: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": list(props),
                "properties": {k: {"type": v} for k, v in props.items()},
            },
        },
    }


def _tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data["choices"][0]["message"].get("tool_calls") or []


def _tool_args(calls: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for call in calls:
        if call.get("function", {}).get("name") == name:
            return json.loads(call.get("function", {}).get("arguments") or "{}")
    return {}


def _haystack_prompt(token_budget: int, needle: str) -> str:
    words_needed = max(150, int(token_budget * 0.92))
    sentence = (
        "calibration vector stable matrix corridor signal archive delta "
        "ledger syntax horizon buffer module "
    )
    filler_words = (sentence.split() * math.ceil(words_needed / len(sentence.split())))[:words_needed]
    insert_at = max(20, int(len(filler_words) * 0.73))
    filler_words.insert(insert_at, f"The hidden token is {needle}.")
    text = " ".join(filler_words)
    return (
        "TASK: Find the hidden token in the haystack below.\n"
        "OUTPUT RULES:\n"
        "- Return only the exact hidden token string.\n"
        "- Do not include quotes.\n"
        "- Do not include punctuation.\n"
        "- Do not write an explanation.\n"
        "- Do not write phrases like 'The hidden token is'.\n"
        "- Your entire response must be one token matching KJAS-NEEDLE-<number>-<hex>.\n\n"
        "HAYSTACK START\n"
        f"{text}\n"
        "HAYSTACK END\n\n"
        "Return the hidden token now. Output only the token:"
    )


def _extract_context_window(metadata: dict[str, Any]) -> int | None:
    candidates: list[Any] = []
    keys = (
        "context_length",
        "context_window",
        "max_context_length",
        "n_ctx",
        "num_ctx",
        "max_position_embeddings",
    )

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in keys:
                    candidates.append(value)
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(metadata)
    for candidate in candidates:
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if value >= 1024:
            return value
    return None


def _effective_context_window(detected: int | None, cap: int | None) -> int:
    fallback = detected or 8192
    return min(fallback, cap) if cap else fallback


def _summary(run: BenchmarkRun) -> str:
    model = run.model or "unknown model"
    verdict = "usable" if run.usable else "below usable threshold"
    return f"{model} scored {run.total_score:.1f}/100 and is {verdict}."


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return text


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Keep prompts and responses by default, but avoid duplicating very large haystacks in list views.
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_benchmark_task(run_id: str, payload: BenchmarkInput) -> None:
    task = asyncio.create_task(run_benchmark(run_id, payload))
    _BENCHMARK_TASKS[run_id] = task
    task.add_done_callback(lambda _task: _BENCHMARK_TASKS.pop(run_id, None))


def cancel_benchmark_task(
    run_id: str,
    *,
    store: BenchmarkStore = DEFAULT_BENCHMARK_STORE,
) -> BenchmarkRun | None:
    run = store.read(run_id)
    if run is None:
        return None
    if run.status != "running":
        return run

    run.status = "cancelled"
    run.error = "Benchmark run was cancelled."
    run.usable = False
    store.save(run)

    task = _BENCHMARK_TASKS.get(run_id)
    if task and not task.done():
        task.cancel()
    return store.read(run_id) or run
