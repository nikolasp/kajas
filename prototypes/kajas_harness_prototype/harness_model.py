"""PROTOTYPE - pure state model for the Kajás agentic tool harness.

Question: does this control model feel right for delegating coding tasks
across tools such as Codex and Pi while keeping task state, approvals,
handoffs, and completion criteria visible?

This module is intentionally portable: no terminal I/O, no subprocesses,
and no persistence. Delete or absorb after the prototype answers the question.
"""

from copy import deepcopy


def initial_state():
    return {
        "next_task_id": 3,
        "active_task_id": 1,
        "policy": {
            "autonomy": "reviewed",
            "max_parallel_runs": 2,
            "require_approval_for": ["network", "destructive", "outside_workspace"],
        },
        "tools": {
            "codex": {
                "status": "idle",
                "strengths": ["repo edits", "tests", "reviews"],
                "current_task_id": None,
            },
            "pi": {
                "status": "idle",
                "strengths": ["research", "planning", "explanations"],
                "current_task_id": None,
            },
        },
        "tasks": [
            {
                "id": 1,
                "title": "Add OAuth login",
                "status": "queued",
                "intent": "implementation",
                "risk": "medium",
                "required_capability": "repo edits",
                "assigned_tool": None,
                "approval": None,
                "history": ["queued by user"],
                "artifacts": [],
            },
            {
                "id": 2,
                "title": "Compare vector DB options",
                "status": "queued",
                "intent": "research",
                "risk": "low",
                "required_capability": "research",
                "assigned_tool": None,
                "approval": None,
                "history": ["queued by user"],
                "artifacts": [],
            },
        ],
        "events": ["harness booted with two sample tasks"],
    }


def reduce(state, action):
    next_state = deepcopy(state)
    kind = action["type"]

    if kind == "add_task":
        task = {
            "id": next_state["next_task_id"],
            "title": action["title"],
            "status": "queued",
            "intent": action["intent"],
            "risk": action["risk"],
            "required_capability": action["required_capability"],
            "assigned_tool": None,
            "approval": None,
            "history": ["queued by user"],
            "artifacts": [],
        }
        next_state["next_task_id"] += 1
        next_state["tasks"].append(task)
        next_state["active_task_id"] = task["id"]
        return remember(next_state, f"added task #{task['id']}: {task['title']}")

    task = active_task(next_state)
    if task is None:
        return remember(next_state, "no active task")

    if kind == "select_next":
        ids = [task["id"] for task in next_state["tasks"]]
        current_index = ids.index(next_state["active_task_id"])
        next_state["active_task_id"] = ids[(current_index + 1) % len(ids)]
        return remember(next_state, f"selected task #{next_state['active_task_id']}")

    if kind == "auto_route":
        tool_name = choose_tool(next_state, task)
        if tool_name is None:
            task["history"].append("no idle tool can handle required capability")
            return remember(next_state, f"could not route task #{task['id']}")
        return assign(next_state, task, tool_name, "auto-routed")

    if kind == "assign":
        return assign(next_state, task, action["tool"], "manually assigned")

    if kind == "request_approval":
        task["approval"] = {
            "status": "pending",
            "reason": action["reason"],
        }
        task["status"] = "blocked"
        task["history"].append(f"approval requested: {action['reason']}")
        release_tool(next_state, task)
        return remember(next_state, f"task #{task['id']} is blocked on approval")

    if kind == "approve":
        if task["approval"] is None:
            task["history"].append("approve ignored: no pending approval")
            return remember(next_state, f"task #{task['id']} has no approval request")
        task["approval"]["status"] = "approved"
        task["status"] = "queued"
        task["history"].append("approval granted; task returned to queue")
        return remember(next_state, f"approved task #{task['id']}")

    if kind == "tick":
        return advance_task(next_state, task)

    if kind == "handoff":
        other_tool = "pi" if task["assigned_tool"] == "codex" else "codex"
        if task["assigned_tool"] is None:
            task["history"].append("handoff ignored: task is not assigned")
            return remember(next_state, f"task #{task['id']} is not assigned")
        if next_state["tools"][other_tool]["status"] != "idle":
            task["history"].append(f"handoff blocked: {other_tool} is busy")
            return remember(next_state, f"{other_tool} is busy")
        old_tool = task["assigned_tool"]
        release_tool(next_state, task)
        return assign(next_state, task, other_tool, f"handed off from {old_tool}")

    if kind == "fail":
        task["status"] = "failed"
        task["history"].append("marked failed; needs user decision")
        release_tool(next_state, task)
        return remember(next_state, f"failed task #{task['id']}")

    return remember(next_state, f"unknown action: {kind}")


def active_task(state):
    for task in state["tasks"]:
        if task["id"] == state["active_task_id"]:
            return task
    return None


def choose_tool(state, task):
    for tool_name, tool in state["tools"].items():
        if tool["status"] == "idle" and task["required_capability"] in tool["strengths"]:
            return tool_name
    return None


def assign(state, task, tool_name, reason):
    tool = state["tools"].get(tool_name)
    if tool is None:
        task["history"].append(f"assignment ignored: unknown tool {tool_name}")
        return remember(state, f"unknown tool: {tool_name}")
    if tool["status"] != "idle":
        task["history"].append(f"assignment ignored: {tool_name} is busy")
        return remember(state, f"{tool_name} is busy")
    if task["status"] in ["done", "failed"]:
        task["history"].append("assignment ignored: terminal task")
        return remember(state, f"task #{task['id']} is terminal")

    task["assigned_tool"] = tool_name
    task["status"] = "running"
    task["history"].append(f"{reason} to {tool_name}")
    tool["status"] = "running"
    tool["current_task_id"] = task["id"]
    return remember(state, f"{reason} task #{task['id']} to {tool_name}")


def release_tool(state, task):
    tool_name = task["assigned_tool"]
    if tool_name is None:
        return
    state["tools"][tool_name]["status"] = "idle"
    state["tools"][tool_name]["current_task_id"] = None
    task["assigned_tool"] = None


def advance_task(state, task):
    if task["status"] == "queued":
        return reduce(state, {"type": "auto_route"})

    if task["status"] == "blocked":
        task["history"].append("tick ignored: task blocked")
        return remember(state, f"task #{task['id']} remains blocked")

    if task["status"] != "running":
        task["history"].append(f"tick ignored: status is {task['status']}")
        return remember(state, f"task #{task['id']} is {task['status']}")

    if task["risk"] == "high" and task["approval"] is None:
        return reduce(state, {"type": "request_approval", "reason": "high-risk task"})

    task["status"] = "done"
    task["artifacts"].append(f"{task['assigned_tool']} result summary")
    task["history"].append("completed by assigned tool")
    release_tool(state, task)
    return remember(state, f"completed task #{task['id']}")


def remember(state, event):
    state["events"].append(event)
    state["events"] = state["events"][-8:]
    return state
