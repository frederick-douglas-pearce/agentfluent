# Changelog

## [0.2.0](https://github.com/frederick-douglas-pearce/agentfluent/compare/v0.1.0...v0.2.0) (2026-04-19)


### Features

* label cost output as API rate with subscription-plan footnote ([#77](https://github.com/frederick-douglas-pearce/agentfluent/issues/77)) ([8d91e3a](https://github.com/frederick-douglas-pearce/agentfluent/commit/8d91e3ae620731a3c0b4b9de6fa086146fe5fd9f)), closes [#76](https://github.com/frederick-douglas-pearce/agentfluent/issues/76)
* **security:** add secrets-protection hooks for Claude Code sessions ([#72](https://github.com/frederick-douglas-pearce/agentfluent/issues/72)) ([#73](https://github.com/frederick-douglas-pearce/agentfluent/issues/73)) ([c870ad4](https://github.com/frederick-douglas-pearce/agentfluent/commit/c870ad4bba336a3275feafaf44964a09ab79179e))


### Bug Fixes

* add opus-4-7 pricing, filter &lt;synthetic&gt;, quiet unknown-model warning ([#78](https://github.com/frederick-douglas-pearce/agentfluent/issues/78)) ([7ff9f79](https://github.com/frederick-douglas-pearce/agentfluent/commit/7ff9f7936c1b2f0e4df9b74dc295382fa530271f))
* parse agent metadata from user.toolUseResult shape ([#84](https://github.com/frederick-douglas-pearce/agentfluent/issues/84)) ([#85](https://github.com/frederick-douglas-pearce/agentfluent/issues/85)) ([67f2780](https://github.com/frederick-douglas-pearce/agentfluent/commit/67f2780721b115867595a67dc631afe880c39a52))

## 0.1.0 (2026-04-17)


### Features

* add versioned JSON envelope and help examples ([#39](https://github.com/frederick-douglas-pearce/agentfluent/issues/39), [#42](https://github.com/frederick-douglas-pearce/agentfluent/issues/42)) ([#61](https://github.com/frederick-douglas-pearce/agentfluent/issues/61)) ([a8036f2](https://github.com/frederick-douglas-pearce/agentfluent/commit/a8036f2c9f86438b0511f87e59b1f890db375810))
* create CLI skeleton with Typer stub commands ([23e03e1](https://github.com/frederick-douglas-pearce/agentfluent/commit/23e03e187bb74a5d230aefec7c0300e6e2101a72)), closes [#9](https://github.com/frederick-douglas-pearce/agentfluent/issues/9)
* define core data models for parsed JSONL sessions ([a1f16e5](https://github.com/frederick-douglas-pearce/agentfluent/commit/a1f16e5a8994966db5dba991e028a48338fc93db)), closes [#13](https://github.com/frederick-douglas-pearce/agentfluent/issues/13)
* implement --verbose and --quiet output modes ([#40](https://github.com/frederick-douglas-pearce/agentfluent/issues/40)) ([#62](https://github.com/frederick-douglas-pearce/agentfluent/issues/62)) ([12f8b22](https://github.com/frederick-douglas-pearce/agentfluent/commit/12f8b222e69f5b4c54358042cdce6ad414c618c7))
* implement agent configuration assessment ([#28](https://github.com/frederick-douglas-pearce/agentfluent/issues/28)-[#32](https://github.com/frederick-douglas-pearce/agentfluent/issues/32)) ([f2fe0dd](https://github.com/frederick-douglas-pearce/agentfluent/commit/f2fe0ddd91b772d9345ee99f39b4e0065620f24a))
* implement agent configuration assessment (E5) ([88861a2](https://github.com/frederick-douglas-pearce/agentfluent/commit/88861a20ce7a4b3c97b37634839f6e192bfa2394))
* implement agent invocation extraction from sessions ([f74c773](https://github.com/frederick-douglas-pearce/agentfluent/commit/f74c7734ca20a95c3b26690eae72ac24251f24bb))
* implement diagnostics preview pipeline ([#33](https://github.com/frederick-douglas-pearce/agentfluent/issues/33)-[#37](https://github.com/frederick-douglas-pearce/agentfluent/issues/37)) ([c4d8348](https://github.com/frederick-douglas-pearce/agentfluent/commit/c4d8348342387479e02d7d9a52ce7d9176dda27d))
* implement diagnostics preview pipeline (E6) ([22f7f53](https://github.com/frederick-douglas-pearce/agentfluent/commit/22f7f53d58f1535eeb527a0071b4b5178b438f4c))
* implement execution analytics pipeline ([#22](https://github.com/frederick-douglas-pearce/agentfluent/issues/22)-[#27](https://github.com/frederick-douglas-pearce/agentfluent/issues/27)) ([155cb1a](https://github.com/frederick-douglas-pearce/agentfluent/commit/155cb1af9f96133907e42df945e287c8a5e4e79c))
* implement execution analytics pipeline (E4) ([a95db23](https://github.com/frederick-douglas-pearce/agentfluent/commit/a95db23a1bc82e6a66dd57e88073366b3aaddec6))
* implement project discovery and JSONL session parser ([13e567c](https://github.com/frederick-douglas-pearce/agentfluent/commit/13e567c6b40014b1e330af5f131b3aa5a78cf88d))
* initialize Python package with uv and pyproject.toml ([db80861](https://github.com/frederick-douglas-pearce/agentfluent/commit/db8086164ed8f46001ea6095d7028886930b245d)), closes [#8](https://github.com/frederick-douglas-pearce/agentfluent/issues/8)
* wire up agentfluent list command with discovery and parser ([23828a0](https://github.com/frederick-douglas-pearce/agentfluent/commit/23828a0b2dc63e7b2c3d596b82d359db6a06ebd2)), closes [#16](https://github.com/frederick-douglas-pearce/agentfluent/issues/16)


### Bug Fixes

* add message.id dedup for accurate token counting ([1d70a8d](https://github.com/frederick-douglas-pearce/agentfluent/commit/1d70a8d43dba102e820c95c9c3bea282505ef819)), closes [#55](https://github.com/frederick-douglas-pearce/agentfluent/issues/55)
* remove unused imports in test files caught by CI lint ([e1337fc](https://github.com/frederick-douglas-pearce/agentfluent/commit/e1337fc44b4647b1329bc7c5759c6eafe93954bc))


### Miscellaneous Chores

* release as 0.1.0 ([39f0cd5](https://github.com/frederick-douglas-pearce/agentfluent/commit/39f0cd5c28a8e1616f6ce7636e1dd0cf2e043e78))

## Changelog

All notable changes to AgentFluent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases are managed automatically by
[release-please](https://github.com/googleapis/release-please) from
[Conventional Commits](https://www.conventionalcommits.org/).
