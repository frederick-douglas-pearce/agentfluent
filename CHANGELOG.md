# Changelog

## [0.3.0](https://github.com/frederick-douglas-pearce/agentfluent/compare/v0.2.0...v0.3.0) (2026-04-25)


### Features

* **cli:** add --claude-config-dir flag and CLAUDE_CONFIG_DIR env var ([#125](https://github.com/frederick-douglas-pearce/agentfluent/issues/125)) ([81c65b9](https://github.com/frederick-douglas-pearce/agentfluent/commit/81c65b96ecce5f6ee88670fb5283693a32020ab4))
* **config:** discover MCP servers from ~/.claude.json and .mcp.json ([#156](https://github.com/frederick-douglas-pearce/agentfluent/issues/156)) ([2e56a35](https://github.com/frederick-douglas-pearce/agentfluent/commit/2e56a35ee1d803f3e3e8f4c0f1dabc66609f7179))
* **diagnostics:** add delegation clustering + subagent draft generation ([#110](https://github.com/frederick-douglas-pearce/agentfluent/issues/110)) ([#141](https://github.com/frederick-douglas-pearce/agentfluent/issues/141)) ([a274b3f](https://github.com/frederick-douglas-pearce/agentfluent/commit/a274b3f1db14385283b0f81b63d42f47bb8d4a84))
* **diagnostics:** add MCP audit rules for unused + missing servers ([#118](https://github.com/frederick-douglas-pearce/agentfluent/issues/118)) ([#158](https://github.com/frederick-douglas-pearce/agentfluent/issues/158)) ([985d1a4](https://github.com/frederick-douglas-pearce/agentfluent/commit/985d1a4b565f7ff95972b4fdefa1989a3713485b))
* **diagnostics:** add model-routing complexity + mismatch detection ([#111](https://github.com/frederick-douglas-pearce/agentfluent/issues/111)) ([#144](https://github.com/frederick-douglas-pearce/agentfluent/issues/144)) ([a37c172](https://github.com/frederick-douglas-pearce/agentfluent/commit/a37c172d5ffc9ea0b34802485d5c2ffc6d957784))
* **diagnostics:** add trace-level signal extraction and correlation rules ([#107](https://github.com/frederick-douglas-pearce/agentfluent/issues/107)) ([#135](https://github.com/frederick-douglas-pearce/agentfluent/issues/135)) ([d2c040d](https://github.com/frederick-douglas-pearce/agentfluent/commit/d2c040d6ec187f3c6f8f969dc7157c9224a156fe))
* **diagnostics:** aggregate duplicate recommendations ([#165](https://github.com/frederick-douglas-pearce/agentfluent/issues/165)) ([#173](https://github.com/frederick-douglas-pearce/agentfluent/issues/173)) ([2fe7f65](https://github.com/frederick-douglas-pearce/agentfluent/commit/2fe7f651264e5eae4dd39dacf3f064a5a52c8393))
* **diagnostics:** cross-reference model-mismatch into deduped delegation notes ([#113](https://github.com/frederick-douglas-pearce/agentfluent/issues/113)) ([#146](https://github.com/frederick-douglas-pearce/agentfluent/issues/146)) ([3147314](https://github.com/frederick-douglas-pearce/agentfluent/commit/31473147aa0f6194bad8e095551df0ab7b1959c0))
* **diagnostics:** extract MCP tool usage from traces + parent sessions ([#116](https://github.com/frederick-douglas-pearce/agentfluent/issues/116)) ([#157](https://github.com/frederick-douglas-pearce/agentfluent/issues/157)) ([b1b63a5](https://github.com/frederick-douglas-pearce/agentfluent/commit/b1b63a5e804ceb32a614051e3d37e76779add94a))
* **diagnostics:** integrate trace diagnostics into analyze CLI ([#108](https://github.com/frederick-douglas-pearce/agentfluent/issues/108)) ([#136](https://github.com/frederick-douglas-pearce/agentfluent/issues/136)) ([942578e](https://github.com/frederick-douglas-pearce/agentfluent/commit/942578e89085f3983b601224745a3b2da86873d8))
* **diagnostics:** surface copy-paste-ready YAML subagent draft ([#168](https://github.com/frederick-douglas-pearce/agentfluent/issues/168)) ([#177](https://github.com/frederick-douglas-pearce/agentfluent/issues/177)) ([0ccc759](https://github.com/frederick-douglas-pearce/agentfluent/commit/0ccc759df0d977e8d143aec0b5f5f5d7393fdfd6))
* **diagnostics:** tailor recommendations for built-in agents ([#166](https://github.com/frederick-douglas-pearce/agentfluent/issues/166)) ([#176](https://github.com/frederick-douglas-pearce/agentfluent/issues/176)) ([a9502fb](https://github.com/frederick-douglas-pearce/agentfluent/commit/a9502fb5288d5d383cb3174a8f6e5b4dceea2a40))
* **diagnostics:** wire MCP audit into run_diagnostics pipeline ([#119](https://github.com/frederick-douglas-pearce/agentfluent/issues/119)) ([#159](https://github.com/frederick-douglas-pearce/agentfluent/issues/159)) ([e850082](https://github.com/frederick-douglas-pearce/agentfluent/commit/e850082ee48cf8b196048198e71958a9fff834b5))
* **scripts:** add calibration notebook for diagnostics thresholds ([#140](https://github.com/frederick-douglas-pearce/agentfluent/issues/140)) ([#152](https://github.com/frederick-douglas-pearce/agentfluent/issues/152)) ([c742b2b](https://github.com/frederick-douglas-pearce/agentfluent/commit/c742b2bf6a86d8fbafaa5dc61b1b2398b1ce5bcf))
* **traces:** define SubagentTrace, SubagentToolCall, RetrySequence models ([#127](https://github.com/frederick-douglas-pearce/agentfluent/issues/127)) ([30e6ddf](https://github.com/frederick-douglas-pearce/agentfluent/commit/30e6ddf32f99bd4d066af4f347e3e01feb61a71a))
* **traces:** detect retry sequences during subagent trace parsing ([#131](https://github.com/frederick-douglas-pearce/agentfluent/issues/131)) ([8e0b5fe](https://github.com/frederick-douglas-pearce/agentfluent/commit/8e0b5fe5d695b98ea7c510b5acb14d9b7774bced))
* **traces:** implement subagent directory discovery ([#129](https://github.com/frederick-douglas-pearce/agentfluent/issues/129)) ([c246625](https://github.com/frederick-douglas-pearce/agentfluent/commit/c2466255269ba9c55170f67d1febc2a54fe9d256))
* **traces:** implement subagent trace parser ([#130](https://github.com/frederick-douglas-pearce/agentfluent/issues/130)) ([3a81554](https://github.com/frederick-douglas-pearce/agentfluent/commit/3a81554a83cf3360869e40473cbe5f9e8b359c60))
* **traces:** link subagent traces to parent AgentInvocation ([#132](https://github.com/frederick-douglas-pearce/agentfluent/issues/132)) ([4cb3b8f](https://github.com/frederick-douglas-pearce/agentfluent/commit/4cb3b8f8b317192a83c69d461862fa8df8dac218))
* **traces:** retain model on SubagentTrace and use as model-routing fallback ([#142](https://github.com/frederick-douglas-pearce/agentfluent/issues/142)) ([#151](https://github.com/frederick-douglas-pearce/agentfluent/issues/151)) ([d5a7471](https://github.com/frederick-douglas-pearce/agentfluent/commit/d5a7471a139b7df619611df99e2cefe5e217da1f))


### Bug Fixes

* **agents:** default to general-purpose when subagent_type missing ([#169](https://github.com/frederick-douglas-pearce/agentfluent/issues/169)) ([#180](https://github.com/frederick-douglas-pearce/agentfluent/issues/180)) ([4ad8a7e](https://github.com/frederick-douglas-pearce/agentfluent/commit/4ad8a7ef36c54cc30bf2d06c718aeb3c4e23f38e))
* **diagnostics:** name signal type in aggregated message prefix ([#181](https://github.com/frederick-douglas-pearce/agentfluent/issues/181)) ([#182](https://github.com/frederick-douglas-pearce/agentfluent/issues/182)) ([33c52e1](https://github.com/frederick-douglas-pearce/agentfluent/commit/33c52e1e142fcaa34fee291a4ee6560f90fad332))
* **diagnostics:** recalibrate cluster confidence thresholds ([#167](https://github.com/frederick-douglas-pearce/agentfluent/issues/167)) ([#178](https://github.com/frederick-douglas-pearce/agentfluent/issues/178)) ([d51734d](https://github.com/frederick-douglas-pearce/agentfluent/commit/d51734da8bb93f2437dcb578f31a463ef775e08b))
* **parser:** merge content blocks across same-message_id fragments ([#153](https://github.com/frederick-douglas-pearce/agentfluent/issues/153)) ([#154](https://github.com/frederick-douglas-pearce/agentfluent/issues/154)) ([e4d8589](https://github.com/frederick-douglas-pearce/agentfluent/commit/e4d85891975c6b0723fe0532fef091bfd45a4c50))

## [0.2.0](https://github.com/frederick-douglas-pearce/agentfluent/compare/v0.1.0...v0.2.0) (2026-04-20)


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
