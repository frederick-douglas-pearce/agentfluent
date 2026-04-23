"""Tests for subagent trace data models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from agentfluent.core.session import Usage
from agentfluent.traces.models import (
    INPUT_SUMMARY_MAX_CHARS,
    RESULT_SUMMARY_MAX_CHARS,
    UNKNOWN_AGENT_TYPE,
    RetrySequence,
    SubagentToolCall,
    SubagentTrace,
)


def _tool_call(name: str = "Read") -> SubagentToolCall:
    return SubagentToolCall(
        tool_name=name, input_summary="", result_summary="",
    )


class TestConstants:
    def test_truncation_constants_exposed(self) -> None:
        assert INPUT_SUMMARY_MAX_CHARS == 200
        assert RESULT_SUMMARY_MAX_CHARS == 500

    def test_unknown_agent_type_sentinel(self) -> None:
        assert UNKNOWN_AGENT_TYPE == "unknown"


class TestSubagentToolCall:
    def test_with_all_fields(self) -> None:
        usage = Usage(input_tokens=10, output_tokens=5)
        ts = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)
        tc = SubagentToolCall(
            tool_name="Bash",
            input_summary="ls -la",
            result_summary="total 4\n-rw-r--r-- 1 user user 0 file.txt",
            is_error=False,
            usage=usage,
            timestamp=ts,
        )
        assert tc.tool_name == "Bash"
        assert tc.input_summary == "ls -la"
        assert tc.result_summary.startswith("total 4")
        assert tc.is_error is False
        assert tc.usage.input_tokens == 10
        assert tc.timestamp == ts

    def test_minimal_required_fields(self) -> None:
        tc = SubagentToolCall(
            tool_name="Read",
            input_summary="path",
            result_summary="contents",
        )
        assert tc.is_error is False
        assert tc.usage.input_tokens == 0
        assert tc.usage.output_tokens == 0
        assert tc.timestamp is None

    def test_json_round_trip(self) -> None:
        tc = SubagentToolCall(
            tool_name="Grep",
            input_summary="pattern",
            result_summary="match",
            is_error=True,
            usage=Usage(input_tokens=1, output_tokens=2),
            timestamp=datetime(2026, 4, 21, tzinfo=UTC),
        )
        restored = SubagentToolCall.model_validate_json(tc.model_dump_json())
        assert restored == tc

    def test_extra_fields_ignored(self) -> None:
        tc = SubagentToolCall.model_validate(
            {
                "tool_name": "Read",
                "input_summary": "x",
                "result_summary": "y",
                "future_field": 42,
            },
        )
        assert tc.tool_name == "Read"
        assert not hasattr(tc, "future_field")


class TestRetrySequence:
    def test_with_all_fields(self) -> None:
        seq = RetrySequence(
            tool_name="Bash",
            attempts=3,
            first_error_message="permission denied",
            last_error_message="permission denied",
            eventual_success=False,
            tool_call_indices=[4, 5, 6],
        )
        assert seq.tool_name == "Bash"
        assert seq.attempts == 3
        assert seq.tool_call_indices == [4, 5, 6]
        assert seq.eventual_success is False

    def test_attempts_ge_1_validator(self) -> None:
        with pytest.raises(ValidationError):
            RetrySequence(tool_name="Bash", attempts=0)
        with pytest.raises(ValidationError):
            RetrySequence(tool_name="Bash", attempts=-1)

    def test_default_empty_indices(self) -> None:
        seq = RetrySequence(tool_name="Read", attempts=2)
        assert seq.tool_call_indices == []

    def test_optional_error_messages(self) -> None:
        seq = RetrySequence(
            tool_name="Read",
            attempts=2,
            eventual_success=True,
        )
        assert seq.first_error_message is None
        assert seq.last_error_message is None

    def test_json_round_trip(self) -> None:
        seq = RetrySequence(
            tool_name="Bash",
            attempts=3,
            first_error_message="first",
            last_error_message="last",
            eventual_success=True,
            tool_call_indices=[1, 2, 3],
        )
        restored = RetrySequence.model_validate_json(seq.model_dump_json())
        assert restored == seq

    def test_extra_fields_ignored(self) -> None:
        seq = RetrySequence.model_validate(
            {"tool_name": "Read", "attempts": 2, "future_field": "x"},
        )
        assert seq.tool_name == "Read"


class TestSubagentTrace:
    def _minimal(self) -> SubagentTrace:
        return SubagentTrace(
            agent_id="uuid-abc",
            delegation_prompt="Review backlog",
        )

    def test_minimal_construction(self) -> None:
        trace = self._minimal()
        assert trace.agent_id == "uuid-abc"
        assert trace.agent_type == UNKNOWN_AGENT_TYPE
        assert trace.delegation_prompt == "Review backlog"
        assert trace.tool_calls == []
        assert trace.retry_sequences == []
        assert trace.total_errors == 0
        assert trace.total_retries == 0
        assert trace.usage.input_tokens == 0
        assert trace.duration_ms is None
        assert trace.source_file is None

    def test_with_tool_calls_and_retries(self) -> None:
        tc1 = SubagentToolCall(
            tool_name="Bash", input_summary="a", result_summary="x",
        )
        tc2 = SubagentToolCall(
            tool_name="Read", input_summary="b", result_summary="y",
        )
        retry = RetrySequence(
            tool_name="Bash", attempts=2, tool_call_indices=[0],
        )
        trace = SubagentTrace(
            agent_id="uuid-1",
            agent_type="pm",
            delegation_prompt="Do work",
            tool_calls=[tc1, tc2],
            retry_sequences=[retry],
            total_errors=1,
            total_retries=2,
            duration_ms=5000,
        )
        assert len(trace.tool_calls) == 2
        assert len(trace.retry_sequences) == 1
        assert trace.total_errors == 1
        assert trace.duration_ms == 5000

    def test_agent_type_defaults_to_unknown(self) -> None:
        trace = self._minimal()
        assert trace.agent_type == UNKNOWN_AGENT_TYPE

    def test_source_file_optional(self) -> None:
        assert self._minimal().source_file is None
        with_file = SubagentTrace(
            agent_id="u",
            delegation_prompt="p",
            source_file=Path("/tmp/agent-u.jsonl"),
        )
        assert with_file.source_file == Path("/tmp/agent-u.jsonl")

    def test_duration_ms_optional(self) -> None:
        assert self._minimal().duration_ms is None

    def test_model_field_defaults_to_none(self) -> None:
        # Model is populated by the parser from the first assistant
        # message's `model` field; programmatically-constructed traces
        # default to None. Downstream diagnostics.model_routing uses
        # this as a fallback when AgentConfig.model is absent.
        assert self._minimal().model is None
        with_model = SubagentTrace(
            agent_id="u", delegation_prompt="p", model="claude-sonnet-4-6",
        )
        assert with_model.model == "claude-sonnet-4-6"

    def test_unique_tool_names_property(self) -> None:
        trace = SubagentTrace(
            agent_id="u",
            delegation_prompt="p",
            tool_calls=[_tool_call("Bash"), _tool_call("Read"), _tool_call("Bash")],
        )
        assert trace.unique_tool_names == {"Bash", "Read"}

    def test_unique_tool_names_empty_for_no_tool_calls(self) -> None:
        assert self._minimal().unique_tool_names == set()

    def test_unique_tool_names_is_cached(self) -> None:
        trace = SubagentTrace(
            agent_id="u",
            delegation_prompt="p",
            tool_calls=[_tool_call()],
        )
        # Identity check: the cached_property returns the same object on re-access.
        assert trace.unique_tool_names is trace.unique_tool_names

    def test_unique_tool_names_not_in_json(self) -> None:
        trace = SubagentTrace(
            agent_id="u",
            delegation_prompt="p",
            tool_calls=[_tool_call()],
        )
        # Access once to populate the cache.
        _ = trace.unique_tool_names
        dumped = trace.model_dump()
        assert "unique_tool_names" not in dumped
        assert "unique_tool_names" not in trace.model_dump_json()

    def test_json_round_trip_minimal(self) -> None:
        trace = self._minimal()
        restored = SubagentTrace.model_validate_json(trace.model_dump_json())
        assert restored == trace

    def test_json_round_trip_full(self) -> None:
        trace = SubagentTrace(
            agent_id="uuid-1",
            agent_type="pm",
            delegation_prompt="Do work",
            tool_calls=[
                SubagentToolCall(
                    tool_name="Bash",
                    input_summary="ls",
                    result_summary="file.txt",
                    is_error=False,
                    usage=Usage(input_tokens=5, output_tokens=3),
                    timestamp=datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC),
                ),
            ],
            retry_sequences=[
                RetrySequence(
                    tool_name="Bash", attempts=2, tool_call_indices=[0],
                ),
            ],
            total_errors=0,
            total_retries=2,
            usage=Usage(input_tokens=100, output_tokens=50),
            duration_ms=1234,
            source_file=Path("/tmp/agent-uuid-1.jsonl"),
        )
        restored = SubagentTrace.model_validate_json(trace.model_dump_json())
        assert restored == trace

    def test_path_serializes_as_string(self) -> None:
        trace = SubagentTrace(
            agent_id="u",
            delegation_prompt="p",
            source_file=Path("/tmp/x.jsonl"),
        )
        payload = json.loads(trace.model_dump_json())
        assert isinstance(payload["source_file"], str)
        assert payload["source_file"] == "/tmp/x.jsonl"

    def test_extra_fields_ignored(self) -> None:
        trace = SubagentTrace.model_validate(
            {
                "agent_id": "u",
                "delegation_prompt": "p",
                "future_field": {"nested": True},
            },
        )
        assert trace.agent_id == "u"

    def test_serializes_inside_list_wrapper(self) -> None:
        """Proves the nesting chain works without #105's AgentInvocation.trace."""

        class _Wrapper(BaseModel):
            traces: list[SubagentTrace] = []

        wrapper = _Wrapper(
            traces=[
                SubagentTrace(agent_id="a", delegation_prompt="p1"),
                SubagentTrace(agent_id="b", delegation_prompt="p2", agent_type="pm"),
            ],
        )
        restored = _Wrapper.model_validate_json(wrapper.model_dump_json())
        assert restored == wrapper
        assert restored.traces[1].agent_type == "pm"
