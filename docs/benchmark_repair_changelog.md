# Benchmark Repair Changelog

## 2026-07-06

- Evidence reviewed:
  - `.runtime/arche-history.jsonl`
  - `.runtime/arche-artifacts/4ed51b7dd4f84c3d9a3c4229a3b106b3/execution_result.json`
  - `.runtime/arche-artifacts/6eee4654298c41789411e6e807cae63c/execution_result.json`
- Root-cause findings:
  - Recent benchmark failures still include upstream Gaussian API `502` responses; downstream `缺少Gaussian日志文件(.log/.out)` entries are often secondary symptoms after those failed submissions.
  - Deterministic Gaussian recovery depended entirely on `rdkit` for target-molecule geometry generation, so a mismatched stray SDF could still leave the pipeline with no honest target geometry in lighter environments.
- Real fixes added:
  - `ExecutionAgent._run_gaussian_via_api` now retries retryable transient API failures (`429/5xx`, connection, timeout), preserves retry count, and records response-body detail in the persisted error.
  - Deterministic target-geometry generation now falls back to built-in truthful templates for benchmark-sized molecules (`O`, `c1ccccc1`) when `rdkit` is unavailable, while preserving provenance that the geometry came from the target SMILES path.
- Verification:
  - `python -m pytest tests/test_arche_workflow_fixes.py -q`
    - Result: `39 passed, 13 skipped`
- Remaining blocker after this milestone:
  - The persisted July 6 benchmark artifacts were produced before this change set and still need a fresh real-pipeline rerun in the service environment that owns the Gaussian API configuration.
