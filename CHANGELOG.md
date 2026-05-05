# Changelog

## [0.5.1](https://github.com/frederick-douglas-pearce/agentfluent/compare/v0.5.0...v0.5.1) (2026-05-05)

Documentation catch-up release. v0.5.0 shipped to GitHub but the PyPI publish was deferred so the docs could land alongside it; v0.5.1 closes that gap.


### Documentation

* **README:** roadmap restructured into v0.4 (shipped), v0.5 (shipped — "Trustworthy Diagnostics"), v0.6 (planned — "Quality Axis: Tier 1"), and Future. The v0.6 section frames the new third diagnostics axis (quality alongside cost and speed) and links the Tier-1 epic ([#268](https://github.com/frederick-douglas-pearce/agentfluent/issues/268)) plus stories ([#269](https://github.com/frederick-douglas-pearce/agentfluent/issues/269)–[#275](https://github.com/frederick-douglas-pearce/agentfluent/issues/275)). ([#280](https://github.com/frederick-douglas-pearce/agentfluent/issues/280), [#283](https://github.com/frederick-douglas-pearce/agentfluent/issues/283))
* **README:** JSON envelope example bumped to schema v2 — `token_metrics.by_model` is now a list of `{model, origin, ...}` rows, top-line `total_cost` / `total_tokens` are comprehensive (parent + subagent), and `agentfluent diff` reads both v1 and v2 envelopes via a compat shim. ([#280](https://github.com/frederick-douglas-pearce/agentfluent/issues/280))
* **README:** configuration table adds `--top-n`, `--min-severity`, and `--fail-on`. ([#280](https://github.com/frederick-douglas-pearce/agentfluent/issues/280))
* **README:** `agentfluent analyze` section now describes the Top-N priority fixes summary, the Offload Candidates section, and the parent/subagent origin breakout in Cost by Model. Features list adds Comparison Workflow, Priority Ranking, Offload Candidates, and Comprehensive Cost Attribution. ([#280](https://github.com/frederick-douglas-pearce/agentfluent/issues/280))
* **README screenshots:** all four existing SVGs regenerated to show v0.4 / v0.5 surfaces (Top-N priority fixes summary, Offload Candidates table, Cost by Model Origin column). New `demo-diff.svg` captures `agentfluent diff` output. Caption corrections: Execution Analytics shot is now correctly labeled `--no-diagnostics` since v0.4 turned diagnostics on by default. ([#282](https://github.com/frederick-douglas-pearce/agentfluent/issues/282), [#284](https://github.com/frederick-douglas-pearce/agentfluent/issues/284))
* **GLOSSARY:** two new categories — *Diagnostics output fields* and *Comparison row status* — with seven new entries: `priority_score`, `offload_candidate`, `delegation_suggestions_skipped_reason`, `origin`, `new`, `resolved`, `persisting`. ([#280](https://github.com/frederick-douglas-pearce/agentfluent/issues/280))

## [0.5.0](https://github.com/frederick-douglas-pearce/agentfluent/compare/v0.4.0...v0.5.0) (2026-05-05)


### Features

* **agents:** add tester agent for fixing existing pytest failures ([#228](https://github.com/frederick-douglas-pearce/agentfluent/issues/228)) ([09c62d7](https://github.com/frederick-douglas-pearce/agentfluent/commit/09c62d7e618d0229177805fbc3c74c7c60ccb89d))
* **analytics:** Cost by Model includes subagent tokens ([#227](https://github.com/frederick-douglas-pearce/agentfluent/issues/227)) ([#279](https://github.com/frederick-douglas-pearce/agentfluent/issues/279)) ([08707b4](https://github.com/frederick-douglas-pearce/agentfluent/commit/08707b4dbdc36b359daad8f2ae1d35327e30f32b))
* **cli:** --min-severity filter on analyze recommendations ([#205](https://github.com/frederick-douglas-pearce/agentfluent/issues/205)) ([#277](https://github.com/frederick-douglas-pearce/agentfluent/issues/277)) ([56554bf](https://github.com/frederick-douglas-pearce/agentfluent/commit/56554bff9b4ec4f9d0c526346e16f852e66b211d))
* **cli:** agentfluent diff -- compare two analyze runs ([#199](https://github.com/frederick-douglas-pearce/agentfluent/issues/199)) ([#267](https://github.com/frederick-douglas-pearce/agentfluent/issues/267)) ([bf1e97e](https://github.com/frederick-douglas-pearce/agentfluent/commit/bf1e97e63b17098bea1169543f7d8c066d410ce3))
* **cli:** distribution context on outlier signals under --verbose ([#187](https://github.com/frederick-douglas-pearce/agentfluent/issues/187)) ([#237](https://github.com/frederick-douglas-pearce/agentfluent/issues/237)) ([88fd9e9](https://github.com/frederick-douglas-pearce/agentfluent/commit/88fd9e9f48bd6fe8b1ee48a04f11c81ffd796fc4))
* **diagnostics:** 'Offload Candidates' CLI section + calibration sweep ([#260](https://github.com/frederick-douglas-pearce/agentfluent/issues/260)) ([#261](https://github.com/frederick-douglas-pearce/agentfluent/issues/261)) ([bfb198f](https://github.com/frederick-douglas-pearce/agentfluent/commit/bfb198fb10fce85e8d998541a6fff70c89a419ee))
* **diagnostics:** cluster parent-thread bursts and synthesize OffloadCandidate drafts ([#256](https://github.com/frederick-douglas-pearce/agentfluent/issues/256)) ([#257](https://github.com/frederick-douglas-pearce/agentfluent/issues/257)) ([9fd9aa9](https://github.com/frederick-douglas-pearce/agentfluent/commit/9fd9aa9f4d5a14f72a27861e2f9ccd130368eaec))
* **diagnostics:** cost estimation for parent-thread tool-bursts ([#249](https://github.com/frederick-douglas-pearce/agentfluent/issues/249)) ([#250](https://github.com/frederick-douglas-pearce/agentfluent/issues/250)) ([f52de56](https://github.com/frederick-douglas-pearce/agentfluent/commit/f52de5666ec1dc3dd95358da71a7310077fa40b2))
* **diagnostics:** explain empty delegation_suggestions in JSON ([#215](https://github.com/frederick-douglas-pearce/agentfluent/issues/215)) ([#276](https://github.com/frederick-douglas-pearce/agentfluent/issues/276)) ([4013a48](https://github.com/frederick-douglas-pearce/agentfluent/commit/4013a4804d8a63e338358cc13130a725a3ab90cd))
* **diagnostics:** frequency-filter delegation draft tools list ([#184](https://github.com/frederick-douglas-pearce/agentfluent/issues/184)) ([#253](https://github.com/frederick-douglas-pearce/agentfluent/issues/253)) ([b01cbbd](https://github.com/frederick-douglas-pearce/agentfluent/commit/b01cbbd9069cb3415bc1cd1a7b2b6090f36c3de0))
* **diagnostics:** parent-thread tool-burst extractor ([#247](https://github.com/frederick-douglas-pearce/agentfluent/issues/247)) ([#248](https://github.com/frederick-douglas-pearce/agentfluent/issues/248)) ([718595c](https://github.com/frederick-douglas-pearce/agentfluent/commit/718595caab3e32d759323cc67af1cd07db47c795))
* **diagnostics:** priority ranking + Top-N priority fixes summary ([#172](https://github.com/frederick-douglas-pearce/agentfluent/issues/172)) ([#266](https://github.com/frederick-douglas-pearce/agentfluent/issues/266)) ([4170d12](https://github.com/frederick-douglas-pearce/agentfluent/commit/4170d12d44c7bb50cebd8e7eee78b7e3c61961a9))
* **diagnostics:** subtract idle gaps from duration for outlier detection ([#230](https://github.com/frederick-douglas-pearce/agentfluent/issues/230)) ([#234](https://github.com/frederick-douglas-pearce/agentfluent/issues/234)) ([d4ba779](https://github.com/frederick-douglas-pearce/agentfluent/commit/d4ba7799f6e74e67f38a1d6f84a2be078bb9e4f6))
* **diagnostics:** wire offload candidates into pipeline + dedup ([#258](https://github.com/frederick-douglas-pearce/agentfluent/issues/258)) ([#259](https://github.com/frederick-douglas-pearce/agentfluent/issues/259)) ([c1c7b58](https://github.com/frederick-douglas-pearce/agentfluent/commit/c1c7b58ef0769680e5697304ea08263cea1626a9))
* **signals:** IQR-based outlier detection ([#186](https://github.com/frederick-douglas-pearce/agentfluent/issues/186)) + extractor consolidation ([#235](https://github.com/frederick-douglas-pearce/agentfluent/issues/235)) ([#236](https://github.com/frederick-douglas-pearce/agentfluent/issues/236)) ([c5fdc80](https://github.com/frederick-douglas-pearce/agentfluent/commit/c5fdc8045fe7681d7cb156082e690d01596d149f))


### Bug Fixes

* **diagnostics:** bound is_error regex fallback to leading 200 chars ([#238](https://github.com/frederick-douglas-pearce/agentfluent/issues/238)) ([#240](https://github.com/frederick-douglas-pearce/agentfluent/issues/240)) ([a9dee60](https://github.com/frederick-douglas-pearce/agentfluent/commit/a9dee60032e61d4519b379cf932ad7c96d8b38f5))
* **diagnostics:** bound MCP is_error regex fallback to leading window ([#241](https://github.com/frederick-douglas-pearce/agentfluent/issues/241)) ([#278](https://github.com/frederick-douglas-pearce/agentfluent/issues/278)) ([1927b28](https://github.com/frederick-douglas-pearce/agentfluent/commit/1927b28d335d70f6e231cf1cc01b73ac439964e8))
* **diagnostics:** filter PERMISSION_FAILURE false positives ([#231](https://github.com/frederick-douglas-pearce/agentfluent/issues/231)) ([#239](https://github.com/frederick-douglas-pearce/agentfluent/issues/239)) ([5afe6cf](https://github.com/frederick-douglas-pearce/agentfluent/commit/5afe6cfab79700555d8fbe2f195c24a6f5c77631))

## [0.4.0](https://github.com/frederick-douglas-pearce/agentfluent/compare/v0.3.0...v0.4.0) (2026-04-30)


### Features

* **analytics:** surface total_cost_usd + avg_cost_per_invocation_usd on by_agent_type ([#200](https://github.com/frederick-douglas-pearce/agentfluent/issues/200)) ([#217](https://github.com/frederick-douglas-pearce/agentfluent/issues/217)) ([270e585](https://github.com/frederick-douglas-pearce/agentfluent/commit/270e58550dabc0d47625ddebd5a80f0229e30a8a))
* **cli:** accept --json as alias for --format json ([#196](https://github.com/frederick-douglas-pearce/agentfluent/issues/196)) ([#212](https://github.com/frederick-douglas-pearce/agentfluent/issues/212)) ([4660c28](https://github.com/frederick-douglas-pearce/agentfluent/commit/4660c28ab2ad506f33ba2e1b47539241b9de037a))
* **cli:** default --diagnostics on for analyze ([#202](https://github.com/frederick-douglas-pearce/agentfluent/issues/202)) ([#214](https://github.com/frederick-douglas-pearce/agentfluent/issues/214)) ([15547f1](https://github.com/frederick-douglas-pearce/agentfluent/commit/15547f12fef25c169c361db10d28dcbd4e653420))
* **diagnostics:** populate invocation_id on contributing recommendations ([#197](https://github.com/frederick-douglas-pearce/agentfluent/issues/197)) ([#220](https://github.com/frederick-douglas-pearce/agentfluent/issues/220)) ([18e1ca4](https://github.com/frederick-douglas-pearce/agentfluent/commit/18e1ca47792c5d307dfcb5314e8ce10d60199ca5))
* **glossary:** structured YAML source + agentfluent explain CLI ([#191](https://github.com/frederick-douglas-pearce/agentfluent/issues/191)) ([#224](https://github.com/frederick-douglas-pearce/agentfluent/issues/224)) ([ef05715](https://github.com/frederick-douglas-pearce/agentfluent/commit/ef05715f0730d04f49ce0778a2e5cb9c3b786e03))


### Bug Fixes

* **cli:** route parse warnings to stderr with WARNING prefix ([#206](https://github.com/frederick-douglas-pearce/agentfluent/issues/206)) ([#213](https://github.com/frederick-douglas-pearce/agentfluent/issues/213)) ([18248b3](https://github.com/frederick-douglas-pearce/agentfluent/commit/18248b37eefe87ea4c063705e40b94ea74d1fad5))
* **schema:** agent_type None instead of "" for cross-cutting findings ([#207](https://github.com/frederick-douglas-pearce/agentfluent/issues/207)) ([#218](https://github.com/frederick-douglas-pearce/agentfluent/issues/218)) ([8f90cbf](https://github.com/frederick-douglas-pearce/agentfluent/commit/8f90cbf0c3dc093c3b9f66f70ba6511438313e8d))

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
