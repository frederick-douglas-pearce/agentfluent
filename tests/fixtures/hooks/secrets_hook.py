#!/usr/bin/env python3
"""Fixture: a PostToolUse hook that reads tool_response only (no timing field).

Used to assert a not-covered result when searching for the timing field.
"""
import json
import sys


def main() -> None:
    event = json.load(sys.stdin)
    output = event.get("tool_response", {}).get("stdout", "")
    if "sk-ant-" in output:
        print(json.dumps({"decision": "block", "reason": "secret detected"}))


if __name__ == "__main__":
    main()
