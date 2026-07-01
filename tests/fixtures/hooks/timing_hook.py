#!/usr/bin/env python3
"""Fixture: a PostToolUse timing hook that reads duration_ms (covered=True)."""
import json
import sys


def main() -> None:
    event = json.load(sys.stdin)
    duration_ms = event.get("tool_response", {}).get("duration_ms")
    if duration_ms is not None and duration_ms > 5000:
        print(json.dumps({"systemMessage": f"slow tool call: {duration_ms}ms"}))


if __name__ == "__main__":
    main()
