"""Repo-tracked Agent SDK dogfood-runner (S0 / #590).

Runs AgentFluent's own dogfood analysis over a bounded rolling window of the
local corpus, driving the real ``agentfluent`` CLI. The deterministic core
(:mod:`tools.dogfood_runner.cli_runner`, :mod:`tools.dogfood_runner.paths`) is
SDK-free and unit-tested; the SDK ``query()`` narrative synthesis lives in
:mod:`tools.dogfood_runner.runner` and imports ``claude_agent_sdk`` lazily so
the gate runs even without the ``research`` dependency-group installed.

Not part of the published ``agentfluent`` package. See ``README.md``.
"""
