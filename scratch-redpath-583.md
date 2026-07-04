# Red-path exercise (#583)

Throwaway artifact for the release-loop red-path recovery exercise (#583). This branch is
**DO NOT MERGE** — it exists only to observe the loop's two red-recovery paths execute.

## AC-verifier path

Exercises SKILL §7. The AC-verifier runs in a fresh context against `git diff main...HEAD`
and the verbatim acceptance criteria. If any criterion is not met, the orchestrator fixes
the gap and re-verifies, bounded to a maximum of two rounds before escalating. This section
documents the FAIL → fix → re-verify loopback that auto-merge for the `docs`/`research`
routes depends on.
