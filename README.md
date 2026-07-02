# ARCHE

Research-oriented multi-agent computational chemistry workflow prototype.

## Status

This repository is **research software** under active development. It is shared for transparency and reproducibility of ongoing work, not as a polished end-user product.

Current focus includes:
- multi-agent workflow orchestration (retrieval, hypothesis, planning, execution, reflection)
- Gaussian-related workflow execution with pause/wait behavior for long-running jobs
- controller-level resume from saved waiting state snapshots

## What This Repository Is / Is Not

This repository is:
- useful for readers who want to inspect architecture and experiment with the controller workflow
- suitable for mock-mode quickstart and controlled replay-style runs
- intended for first-version expert trial / live demo workflows, not production-grade deployment yet

This repository is not:
- a production-ready package with one-command installation across environments
- fully standardized for all external toolchains (Gaussian, local chemistry tools, HPC schedulers)

## Project Root and Demo Paths

The server deployment path is:
- `/home/lidong/ChemistryAgent`

The local renamed checkout may be:
- `ARCHE`

Do not hard-code either path in tests or demo scripts. For stable execution across both environments, set:

```bash
export ARCHE_PROJECT_ROOT=/home/lidong/ChemistryAgent
```

or, on a local checkout:

```bash
export ARCHE_PROJECT_ROOT="$(pwd)"
```

If `ARCHE_PROJECT_ROOT` is not set, test/demo scripts infer the project root by walking upward from the script location until `src/chemistry_multiagent` is found.

## Public Entry Point

Primary CLI entry point:
- `chemistry_multiagent.controllers.chemistry_multiagent_controller`

Least-fragile quickstart path (mock mode):
```bash
PYTHONPATH=src python3 -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --mock \
  --question "Propose a plausible TS validation workflow" \
  --work-dir "$(pwd)"
```

Preflight note (important):
- If startup logs include import warnings such as `导入Agent模块警告` or show `Agents可用: False`, advanced agent-driven workflows are not fully active in the current environment.

## Advanced (Research) Path

Use explicit paths instead of relying on defaults:
```bash
PYTHONPATH=src python3 -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --question "Evaluate a candidate TS workflow for nucleophilic addition" \
  --work-dir "$(pwd)" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json" \
  --gaussian-execution-mode replay
```

Resume from waiting-state snapshot:
```bash
PYTHONPATH=src python3 -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --resume-state "outputs/multiagent/waiting_gaussian_jobs_state.json" \
  --work-dir "$(pwd)" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json"
```

## Dependency Scope (Current Known)

- Minimal baseline (`requirements.txt`): enough for controller CLI and mock-mode validation.
- Additional Python packages (representative, not guaranteed complete) are needed for retrieval/advanced workflows, for example: `numpy`, `PyMuPDF` (`fitz`), `faiss`/`faiss-cpu`, `sentence-transformers`, and `paperscraper`.
- External non-Python toolchains are required for Gaussian-integrated paths (for example Gaussian executables, and possibly scheduler/runtime tooling such as Slurm in cluster setups).

## Documentation

- Project structure: `docs/project_structure.md`
- Architecture overview: `docs/architecture_overview.md`
- Quickstart and caveats: `docs/quickstart.md`

## Known Caveats (Important)

- Mock mode is the least fragile public baseline.
- Advanced workflows depend on agent imports, additional Python dependencies, local chemistry toolchain availability, and runtime configuration; advanced runs may degrade or fail if these are unavailable.
- Toolpool/tool-path configuration is still partly legacy and may require local adjustment.
- Toolpool default-path expectations are legacy: controller defaults to `work_dir/toolpool/toolpool.json`, while checked-in tool definitions are at `src/chemistry_multiagent/tools/toolpool.json`; pass `--toolpool` explicitly.
- Some tool scripts and internal test harnesses assume local chemistry toolchains and machine-specific environments.
- Vendored third-party assets exist under `src/chemistry_multiagent/tools`; these components may carry their own licenses and redistribution terms, so review upstream licenses before redistribution or reuse.

## Contributing

See `CONTRIBUTING.md` for lightweight contribution and validation guidance.

## License

See `LICENSE` (final license selection is a maintainer decision pending).
