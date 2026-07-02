# Quickstart

This quickstart targets conservative, reproducible usage for external readers.

## 1) Environment

From repository root:
```bash
export ARCHE_PROJECT_ROOT="$(pwd)"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Note:
- This installs only a minimal baseline.
- Advanced chemistry workflows require extra dependencies and external software.
- `/home/lidong/ChemistryAgent` is the server deployment path; a local checkout may be named `ARCHE`. Use `ARCHE_PROJECT_ROOT` so scripts work in both places.
- Current first-version goal is expert trial / live demo readiness, not production-grade deployment.

## 2) Verify CLI Surface

Controller CLI entry point:
```bash
PYTHONPATH=src python3 -m chemistry_multiagent.controllers.chemistry_multiagent_controller --help
```

Current argument constraints (enforced by CLI):
- `--resume-state` cannot be combined with `--mock`
- if `--resume-state` is not provided, `--question` is required

Preflight note:
- If startup logs include import warnings such as `导入Agent模块警告` or show `Agents可用: False`, advanced agent-driven workflows are not fully active.

## 3) Least-Fragile Run (Mock)

```bash
PYTHONPATH=src python3 -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --mock \
  --question "Propose a plausible TS validation workflow" \
  --work-dir "$(pwd)"
```

## 4) Advanced Research Run (Replay Mode)

Prefer explicit paths to avoid default-path ambiguity:
```bash
PYTHONPATH=src python3 -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --question "Evaluate a candidate TS workflow for nucleophilic addition" \
  --work-dir "$(pwd)" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json" \
  --gaussian-execution-mode replay \
  --pdf-dir "tests/inputs/papers" \
  --index-dir "tests/temp/index"
```

Equivalent server setup:

```bash
export ARCHE_PROJECT_ROOT=/home/lidong/ChemistryAgent
cd "$ARCHE_PROJECT_ROOT"
PYTHONPATH=src python3 -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --question "Evaluate a candidate TS workflow for nucleophilic addition" \
  --work-dir "$ARCHE_PROJECT_ROOT" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json" \
  --gaussian-execution-mode replay \
  --pdf-dir "tests/inputs/papers" \
  --index-dir "tests/temp/index"
```

Caveats:
- mock mode is the least fragile public baseline
- advanced workflows depend on additional agent imports, Python packages, local toolchain availability, and runtime configuration
- advanced runs may degrade or fail if those components are unavailable
- controller defaults expect `work_dir/toolpool/toolpool.json`; the checked-in toolpool file is `src/chemistry_multiagent/tools/toolpool.json`, so pass `--toolpool` explicitly

## 5) Resume From Waiting State

If a run pauses with `waiting_for_gaussian_jobs`:
```bash
PYTHONPATH=src python3 -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --resume-state "outputs/multiagent/waiting_gaussian_jobs_state.json" \
  --work-dir "$(pwd)" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json"
```

## 6) Dependency Scope (Current Known)

Minimal baseline for CLI/mock mode:
- `requirements.txt` (`openai`, `requests`, `tenacity`)

Additional Python dependencies for retrieval/advanced workflows (representative, not guaranteed complete):
- `numpy`
- `PyMuPDF` (`fitz`)
- `faiss`/`faiss-cpu`
- `sentence-transformers`
- `paperscraper`

External non-Python dependencies (environment-specific):
- Gaussian executables/runtime (`g16` or equivalent)
- optional scheduler/runtime integration (for example Slurm tools in `slurm` mode)
- local chemistry toolchain and model runtime configuration required by selected tools

## 7) Internal Scripts (Not Public API)

`tests/scripts/` contains internal validation harnesses.
Many scripts there are machine/environment-specific and should not be treated as stable public entry points.
