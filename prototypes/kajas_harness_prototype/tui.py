#!/usr/bin/env python3
"""PROTOTYPE - terminal driver for the Kajás harness state model.

Run with:
  python3 prototypes/kajas_harness_prototype/tui.py
"""

import json
import sys

from harness_model import initial_state, reduce


BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CLEAR = "\033[2J\033[H"


def main():
    state = initial_state()
    while True:
        render(state)
        choice = input("> ").strip().lower()
        if choice == "q":
            break
        if choice == "n":
            state = reduce(state, {"type": "select_next"})
        elif choice == "r":
            state = reduce(state, {"type": "auto_route"})
        elif choice == "c":
            state = reduce(state, {"type": "assign", "tool": "codex"})
        elif choice == "p":
            state = reduce(state, {"type": "assign", "tool": "pi"})
        elif choice == "t":
            state = reduce(state, {"type": "tick"})
        elif choice == "a":
            state = reduce(state, {"type": "approve"})
        elif choice == "b":
            state = reduce(
                state,
                {"type": "request_approval", "reason": "manual review requested"},
            )
        elif choice == "h":
            state = reduce(state, {"type": "handoff"})
        elif choice == "f":
            state = reduce(state, {"type": "fail"})
        elif choice == "1":
            state = reduce(
                state,
                {
                    "type": "add_task",
                    "title": "Refactor payment module",
                    "intent": "implementation",
                    "risk": "high",
                    "required_capability": "repo edits",
                },
            )
        elif choice == "2":
            state = reduce(
                state,
                {
                    "type": "add_task",
                    "title": "Draft migration plan",
                    "intent": "planning",
                    "risk": "medium",
                    "required_capability": "planning",
                },
            )
        else:
            state = reduce(state, {"type": "unknown"})

    print("bye")


def render(state):
    print(CLEAR, end="")
    print(f"{BOLD}Kajás Harness Prototype{RESET}")
    print(f"{DIM}Throwaway logic prototype. State is in memory only.{RESET}\n")
    print(f"{BOLD}State{RESET}")
    print(json.dumps(state, indent=2))
    print()
    print(f"{BOLD}Actions{RESET}")
    print(
        f"{BOLD}n{RESET} next task   "
        f"{BOLD}r{RESET} route   "
        f"{BOLD}c{RESET} assign Codex   "
        f"{BOLD}p{RESET} assign Pi   "
        f"{BOLD}t{RESET} tick"
    )
    print(
        f"{BOLD}a{RESET} approve   "
        f"{BOLD}b{RESET} block   "
        f"{BOLD}h{RESET} handoff   "
        f"{BOLD}f{RESET} fail   "
        f"{BOLD}1{RESET} add high-risk edit   "
        f"{BOLD}2{RESET} add plan   "
        f"{BOLD}q{RESET} quit"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nbye")
