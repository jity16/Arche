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

## 2026-07-06 (plot bridge)

- Root-cause finding:
  - `plot_tools.py` only accepted `input_file_path` + `output_image_path`, but `ExecutionAgent._build_real_tool_call_context` had no branch for that tool at all.
  - The planner is allowed to emit `plot_tools` steps using parsed JSON inputs such as `CO2_results.json` or multiple `*_results.json` files, so the real import backend was failing immediately with `缺少必需参数: input_file_path, output_image_path`.
- Real fixes added:
  - `output_parser.py` now exposes `ir_intensities` from `cclib.vibirs`, so parsed Gaussian JSON can carry both frequencies and IR intensities.
  - `plot_tools.py` now provides a real JSON-based IR plotting path:
    - single parsed Gaussian JSON → one broadened IR spectrum
    - multiple parsed Gaussian JSON files → overlaid comparison plot
    - non-JSON input still falls back to the existing `draw_spectrum_from_file` / Multiwfn pipeline
  - `ExecutionAgent._build_real_tool_call_context` now bridges `plot_tools.py` correctly by resolving JSON/log inputs and output image paths from planner-style step metadata.
- Verification:
  - `python -m pytest tests/test_arche_workflow_fixes.py -q`
    - Result: `42 passed, 13 skipped`
  - Targeted regression:
    - `test_plot_tools_accepts_run_local_json_input`
  - Runtime stack evidence:
    - `.venv/bin/python` confirms `cclib.parser.data.ccData` includes `vibirs`
    - direct local invocation of `plot_tools.plot_tools()` with a parsed-JSON sample now generates a real PNG
- Remaining blocker after this milestone:
  - Benchmark Gaussian execution is still limited by the external Gaussian API returning `502`; once that service is healthy, the plotting step should no longer fail on missing argument bridging.

## 2026-07-06 (status semantics)

- Root-cause finding:
  - The server persisted run `status` from coarse stdout parsing, while the frontend history list mostly treated `exitCode == 0` as unconditional success.
  - Real benchmark runs with structured `overall_status = partial_success` and `workflow_outcome = partially_supported` were therefore shown as plain success in dashboard history, which contradicted the stored result body and the Scientific Conclusion limitations.
- Real fixes added:
  - `server.py` now derives persisted terminal run status from the structured result first, using:
    - `final_conclusion.workflow_outcome.overall_status`
    - `final_conclusion.workflow_outcome.workflow_outcome`
    - `final_conclusion.final_decision`
    - validation/unresolved issue presence
  - stdout parsing is now only a fallback when structured result status is unavailable.
  - frontend run classification now treats `status = partial_success` as a distinct warning/partial state rather than collapsing everything with `exitCode = 0` into success.
  - `diagnose()` now surfaces `partial_success` as `工作流部分成功`.
- Verification:
  - `.venv/bin/python -m pytest tests/test_server_cancel.py::RunStatusDerivationTests -q`
    - Result: `2 passed`
  - `node frontend/src/lib/parse.test.mjs`
    - Result: `2 passed`
  - `node frontend/src/lib/historyState.test.mjs`
    - Result: `1 passed`
- Remaining blocker after this milestone:
  - Existing persisted run records are historical and keep their previous stored status until rerun.
  - New benchmark reruns on the patched server will now surface partial status correctly, but full benchmark success is still blocked by the external Gaussian API `502`.

## 2026-07-06 (planner + simple-species targeting)

- Fresh real-pipeline evidence:
  - Reaction-enthalpy benchmark before planner/parser fix:
    - run id `fb19a413381a474889cf4703a3c2aba4`
    - dashboard status still `success` on the old server build
    - planner collapsed to zero executable steps after `JSON对象解析失败`
    - `total_original_steps = 0`, `total_optimized_steps = 0`
  - Reaction-enthalpy benchmark after planner parser + species targeting fix on the restarted server:
    - run id `117d81a8a9484e8c90d928d52daa7e12`
    - dashboard-visible status now `partial_success`
    - planner recovered to `65` original / optimized steps instead of `0`
    - execution success rate improved to `56.92%`
- Root-cause findings:
  - `PlannerAgent._extract_json_object()` used a greedy `{.*}` regex and could swallow extra brace-bearing tail text after a valid JSON object, turning recoverable LLM output into an empty protocol.
  - Deterministic Gaussian fallback could not resolve simple species identifiers like `N2` / `H2` from step file hints, so when upstream `.gjf` chains broke it risked reusing the wrong latest geometry.
- Real fixes added:
  - `PlannerAgent._extract_json_object()` now delegates to the shared `extract_json_from_response()` utility from `llm_api.py`, which uses `raw_decode` and ignores trailing text after the first valid JSON object.
  - `ExecutionAgent` now recognizes simple species tokens from input-file hints and maps them to real SMILES:
    - `N2 -> N#N`
    - `H2 -> [H][H]`
    - plus built-in fallback geometries for `N#N`, `[H][H]`, and `N` when RDKit is unavailable
  - This preserves the strict mechanism guard while allowing thermochemistry workflows that identify the active species through filenames like `N2_optfreq.gjf`.
- Verification:
  - `python -m pytest tests/test_arche_workflow_fixes.py -q`
    - Result: `44 passed, 13 skipped`
  - New targeted regressions cover:
    - planner JSON extraction with trailing brace text
    - deterministic rejection of mismatched `NH3.sdf` when the step target is `N2`
- External backend probe status:
  - Current configured Gaussian endpoint still returns bare `502`.
  - Alternate authenticated commented worker path also returns `502` for `/v1/gaussian/run`.
  - No working replacement Gaussian backend has been verified in this environment yet.

## 2026-07-06 (retrieval noise suppression)

- Root-cause findings:
  - Non-fatal PDF/indexing problems were still polluting real benchmark stderr with raw `MuPDF error: library error: zlib error: incorrect header check` lines.
  - Third-party `paperscraper` download failures could still leak stderr/traceback noise even though ARCHE already treated paper download as best-effort rather than a hard failure.
- Real fixes added:
  - `retrieval_agent.py` now disables PyMuPDF/MuPDF library error and warning emission via `fitz.TOOLS.mupdf_display_errors(False)` and `mupdf_display_warnings(False)`.
  - The paperscraper download worker now wraps `paperscraper.search_papers()` in local stdout/stderr redirection so third-party traceback spam does not contaminate benchmark run stderr while Python-level failures are still captured and handled honestly.
- Verification:
  - `.venv/bin/python -m pytest tests/test_arche_workflow_fixes.py::RetrievalChemistryContextTests::test_disable_pdf_library_noise_turns_off_pymupdf_messages -q`
    - Result: `1 passed`
  - `.venv/bin/python -m pytest tests/test_arche_workflow_fixes.py::RetrievalChemistryContextTests::test_search_papers_suppresses_third_party_stderr_noise -q`
    - Result: `1 passed`
  - `python -m pytest tests/test_arche_workflow_fixes.py -q`
    - Result: `44 passed, 15 skipped`
- Scope note:
  - This milestone improves the honesty and usefulness of real benchmark logs without suppressing fatal ARCHE failures.
  - A fresh live rerun on a restarted server is still needed for the cleaned retrieval stderr to appear in persisted dashboard records.

## 2026-07-06 (local PySCF fallback scaffold)

- New evidence:
  - `.venv` now has working local quantum chemistry packages:
    - `pyscf 2.13.1`
    - `geometric 1.1.1`
  - Direct smoke tests in the project venv succeeded for:
    - B3LYP/6-31G* water single-point energy
    - B3LYP/6-31G* water geometry optimization via `geometric`
    - harmonic frequencies + thermochemistry extraction for water
    - benzene HOMO/LUMO at B3LYP/6-31G*
    - MP2 total energy for N2
    - CCSD(T)-level correction for H2
- Real fixes added:
  - Added `src/chemistry_multiagent/utils/pyscf_runner.py` as a real local backend helper for:
    - Gaussian-style `.gjf` parsing
    - DFT/HF optimizations + harmonic frequencies
    - MP2 single points
    - CCSD(T) single-point corrections
  - `execution_agent.py` now:
    - detects local PySCF availability
    - can fall back from retry-exhausted remote Gaussian API failures to a real local `local_pyscf` backend
    - writes local backend results into the existing Gaussian job artifact path
  - `output_parser.py` now accepts JSON-backed “log” payloads, so downstream parse / geometry-extraction steps can consume local PySCF results through the same pipeline shape.
- Fresh real-pipeline evidence:
  - H2O benchmark rerun on the restarted server:
    - run id `86f254aa032f448d83be8efa58669e01`
    - dashboard-visible status `partial_success`
    - execution success rate `91.18%`
    - local artifacts now include real local-compute logs such as:
      - `water.log`
      - `monomer_opt.log`
      - `dimer_b3lyp_631g.log`
      - `det_26231c06.log`
    - stderr explicitly shows remote `502` fallback into `local_pyscf`
  - This is the first proof inside ARCHE that a benchmark can continue real execution despite the dead remote Gaussian proxy.
- Remaining blocker after this milestone:
  - The local PySCF path is not yet complete enough to make all benchmark workflows fully clean:
    - some later `xyz_to_gjf` steps still fail with `unsupported_subprocess_cli`
    - unsupported functionals like `wb97x-d` still need routing or substitution logic
    - CO2 IR intensities are still not proven end-to-end from the local backend

## 2026-07-06 (first fully clean benchmark)

- Fresh real-pipeline evidence:
  - H2O benchmark rerun after the `xyz_to_gjf` subprocess bridge + recent-JSON plot bridge:
    - run id `01a6454f3f1b4c089c31e2b6f0628a80`
    - dashboard status `success`
    - execution success rate `100.00%`
    - reflection decision `accept`
    - `validation_gaps` empty in `final_conclusion`
- Real fixes added:
  - `execution_agent.py` now remembers recent parsed Gaussian JSON files (`_recent_gaussian_jsons`) alongside recent logs.
  - `plot_tools` bridge now prefers those recent JSON artifacts when a planner step only says “parsed frequencies and intensities” instead of giving an explicit file path.
  - `xyz2gjf.py` now has a real subprocess CLI mapping in the execution agent, and tool subprocesses prefer the repo’s `.venv/bin/python` so they see project-installed dependencies like `ase`.
- Verification:
  - `python -m pytest tests/test_arche_workflow_fixes.py -q`
    - Result: `53 passed, 15 skipped`
  - New targeted regressions cover:
    - plot tool using recent parsed JSON when the step only references parsed data
    - `xyz_to_gjf` subprocess backend mapping
- Remaining blocker after this milestone:
  - At least one predefined benchmark (H2O geometry) is now fully clean through the real dashboard-backed pipeline.
  - The remaining tasks still depend on expanding the local PySCF path or restoring a working remote Gaussian backend.

## 2026-07-06 (planner/execution integrity hardening)

- Fresh integrity finding:
  - The earlier benzene dashboard run `cd47540b5a9f4043b13d49e20d6e50fc` was not a valid end-state proof even though it showed `status = success`.
  - New inspection of the real execution path showed two integrity problems:
    - planner outputs could still include placeholder tools such as `Other: Python script` and `PySCF (standard software)`
    - `ExecutionAgent.execute_tool_step()` treated any tool name containing `python script` as `manual_input` success and surfaced the planner's expected output as if it were real execution
- Root-cause findings:
  - `PlannerAgent._protocol_is_executable()` only rejected truly unknown tool names; recognized families without a concrete registered mapping still passed validation and could reach execution.
  - Planner tool resolution only used the raw tool name, not the step description / input / output context, so parser-like pseudo-tools could not be salvaged while unsupported custom-software steps were not rejected early.
  - Execution then converted those unresolved `python script` steps into fake successes instead of failing honestly.
- Real fixes added:
  - `PlannerAgent._resolve_tool_status()` now accepts step context and can map pseudo-tools to real registered tools only when the step intent is explicit, including:
    - parser-like Gaussian log readers → `parse_gaussian_output`
    - log-to-geometry extraction → `get_gjf_from_log`
    - IR plotting steps → `plot_tools`
    - conversion steps such as `SMILES→SDF`, `SDF→XYZ`, `XYZ→GJF`
  - Planner validation now marks any recognized software-family placeholder that still has no concrete registered mapping as non-executable.
  - `ExecutionAgent` no longer treats `python script` tool names as implicit manual-success steps; such steps now either resolve to a real tool or fail honestly.
- Verification:
  - `.venv/bin/python -m pytest tests/test_arche_workflow_fixes.py tests/test_final_conclusion_summary.py -q`
    - Result: `80 passed, 25 subtests passed`
  - New targeted regressions cover:
    - parser-like `Other: Python script` steps mapping to `parse_gaussian_output`
    - unmapped `PySCF (standard software)` steps making a protocol non-executable
    - `python script` execution steps no longer short-circuiting as `manual_input`
- Current live-state note:
  - The local ARCHE server was restarted from the updated worktree after these fixes.
  - A fresh benzene dashboard run from the restarted server reached retrieval, hypothesis, planner completion, and execution under the patched codebase before being cancelled due long external LLM latency; the cancelled record was removed from dashboard history.

## 2026-07-06 (route sanitizer for local PySCF fallback)

- Fresh real-pipeline evidence:
  - A new benzene dashboard rerun from committed code reached real execution with planner-emitted steps mapped entirely onto registered tools (`smiles2sdf`, `sdf_to_xyz`, `generate_gaussian_code`, `xyz_to_gjf`, `parse_gaussian_output`, `get_gjf_from_log`).
  - That rerun then exposed a new honest backend failure in the deterministic/PySCF chain:
    - generated route line: `# HF/cc-pVTZ)`
    - local PySCF failure: `Unknown basis format or basis name cc-pvtz)`
- Root-cause finding:
  - Route-section normalization preserved unmatched trailing right parentheses from LLM-generated Gaussian keyword strings.
  - The local PySCF `.gjf` parser then propagated the malformed basis token directly into PySCF.
- Real fixes added:
  - `ExecutionAgent._normalize_route_section()` now strips unmatched trailing `)` characters after normalizing whitespace and quotes.
  - `pyscf_runner._normalize_basis()` now applies the same unmatched-parenthesis cleanup as a backend-side defense-in-depth guard.
- Verification:
  - `.venv/bin/python -m pytest tests/test_arche_workflow_fixes.py tests/test_final_conclusion_summary.py -q`
    - Result: `81 passed, 25 subtests passed`
  - New targeted regression:
    - `test_normalize_route_section_strips_unmatched_trailing_parenthesis`

## 2026-07-06 (benzene live rerun: route-line selection + SMILES sanitization)

- Fresh real-pipeline evidence:
  - A new benzene dashboard rerun on the restarted server progressed into execution and exposed two independent live-data integrity problems:
    - the prepared `Benzene_B3LYP_aDZ_opt.gjf` contained a single-point route copied from the second line of a multi-link `generate_gaussian_code` response instead of the first optimization route
    - planner input text such as `"c1ccccc1" (benzene SMILES)` reached `smiles2sdf` with a trailing quote, causing RDKit parse errors in the live run log
- Root-cause findings:
  - `ExecutionAgent._normalize_route_section()` selected the last matching `# ...` line from multi-line codegen output, which favored post-processing `geom=checkpoint guess=read` routes over the initial `opt` route.
  - `_normalize_smiles_list()` only stripped quotes from the ends of the whole string, so quoted SMILES followed by explanatory text survived as invalid tokens like `c1ccccc1"`.
- Real fixes added:
  - Route normalization now prefers the first executable route line in multi-link output, preserving the intended optimization/frequency job instead of the later analysis route.
  - SMILES normalization now recovers valid leading tokens from quoted/annotated planner text before RDKit validation.
- Verification:
  - `.venv/bin/python -m pytest tests/test_arche_workflow_fixes.py tests/test_final_conclusion_summary.py -q`
    - Result: `84 passed, 25 subtests passed`
  - New targeted regressions:
    - `test_normalize_route_section_prefers_first_route_in_multilink_output`
    - `test_normalize_smiles_list_strips_quotes_and_descriptive_suffix`

## 2026-07-06 (literature review prompt-echo cleanup)

- Root-cause finding:
  - Some generated literature reviews echoed the retrieval prompt wrapper itself (for example “作为一名经验丰富的计算化学家…” / “根据您提供的文献节选…”), and that boilerplate then leaked into the Scientific Conclusion evidence-source section.
- Real fixes added:
  - `RetrievalAgent` now strips prompt-echo wrappers from generated literature reviews before persisting them.
  - `ChemistryMultiAgentController` now applies the same sanitization defensively when composing the final evidence-source text.
- Verification:
  - Covered by the focused suite:
    - `.venv/bin/python -m pytest tests/test_arche_workflow_fixes.py tests/test_final_conclusion_summary.py -q`
    - Result: `84 passed, 25 subtests passed`
  - Targeted regression:
    - `test_evidence_source_strips_literature_review_prompt_echo`
