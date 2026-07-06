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

## 2026-07-06 (later)

- Authoritative benchmark source confirmed:
  - `frontend/src/components/QuestionForm.tsx`
  - The four predefined tasks are:
    - `预测 \ce{H2O} 在 \text{B3LYP/6-31G}^* 下的优化几何构型`
    - `计算苯 \ce{C6H6} 的 HOMO–LUMO 能隙`
    - `分析 \ce{CO2} 的振动光谱（IR）吸收峰归属`
    - `求反应 \ce{N2 + 3H2 <=> 2NH3} 的反应焓`
- Fresh real-pipeline evidence:
  - CO2 run before path/state isolation fix:
    - run id `3aa3c8799c7f45758b2a561a6150543a`
    - execution success rate `40.54%`
    - the first Gaussian execution step incorrectly recovered stale repo-root `water.gjf`
  - CO2 run after patch + server restart:
    - run id `6a4217b1f1f94e87b3580c23270364c2`
    - execution success rate `58.62%`
    - Gaussian inputs/logs/state files now stay inside the per-run temp dir, e.g. `.../arche-run-zay7hnup/CO2_opt_freq.gjf`
    - state files are now isolated per input stem, e.g. `.gaussian_job_state_CO2_opt_freq_5_run_gaussian_deterministic.json`
- Root-cause findings:
  - Relative `.gjf` names were being resolved against the repo root instead of the controller run directory.
  - Gaussian job-state files were keyed only by `step_id` + tool name, so different workflows/runs could recover stale state from earlier molecules.
  - After fixing those internal bugs, remaining Gaussian failures still come back as upstream `502` from the configured Gaussian service.
  - `plot_tools.py` still lacks argument bridging in the execution agent; current failures are `missing required params: input_file_path, output_image_path` followed by CLI fallback `unsupported_subprocess_cli`.
- Real fixes added:
  - Relative extracted file paths now resolve against `step.working_directory` / `execution_agent.work_dir` instead of the repo root.
  - Gaussian job-state filenames now include the input stem, preventing stale-state recovery collisions across different `.gjf` jobs that share a step number.
- Verification:
  - `python -m pytest tests/test_arche_workflow_fixes.py -q`
    - Result: `41 passed, 13 skipped`
  - New targeted regressions cover:
    - run-local relative `.gjf` resolution
    - distinct Gaussian state files for distinct `.gjf` inputs with the same step id
- External-state evidence:
  - Direct probe of the configured Gaussian API using `water_opt.gjf` returned `HTTP 502` outside of ARCHE as well.
  - This means the remaining Gaussian-step failures are not currently explained by ARCHE request construction alone.
