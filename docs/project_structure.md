# Project Structure

This document describes the current repository layout for external readers.

## Top-Level Layout

- `src/`
  - production code for controller, agents, and tool wrappers
- `tests/`
  - test code and internal validation harness scripts
- `docs/`
  - public documentation for architecture and usage
- `examples/`
  - reserved for future public examples
- `data/`
  - optional local workspace for user datasets and non-versioned runtime artifacts

## Production Runtime Code

Primary production path:
- `src/chemistry_multiagent/controllers/chemistry_multiagent_controller.py`

Core agent modules:
- `src/chemistry_multiagent/agents/retrieval_agent.py`
- `src/chemistry_multiagent/agents/hypothesis_agent.py`
- `src/chemistry_multiagent/agents/planner_agent.py`
- `src/chemistry_multiagent/agents/execution_agent.py`
- `src/chemistry_multiagent/agents/reflection_agent.py`

Utility modules:
- `src/chemistry_multiagent/utils/`

Tool definitions and wrappers:
- `src/chemistry_multiagent/tools/toolpool.json`
- `src/chemistry_multiagent/tools/*.py`

## Tests and Internal Validation

- `tests/test_controller_resume.py`
  - focused unit-style checks for controller resume behavior
- `tests/scripts/`
  - internal/developer validation scripts; many are environment-specific and should not be treated as stable public entry points

## Vendored / Third-Party Assets

Large vendored content currently exists under:
- `src/chemistry_multiagent/tools/ash-NEW/`
- `src/chemistry_multiagent/tools/openbabel-openbabel-3-1-1/`
- `src/chemistry_multiagent/tools/cmake-3.30.4-linux-x86_64/`
- `src/chemistry_multiagent/tools/Multiwfn_3.8_dev_bin_Linux_noGUI/`
- `src/chemistry_multiagent/tools/paper-scraper/`

These are retained for now to preserve local behavior, but they increase repository size and require ongoing license/compliance review before wide redistribution.

Important licensing note for public reuse:
- vendored components may carry their own upstream licenses and redistribution conditions
- review the license files within each vendored subtree before redistributing this repository or derivative bundles

## Runtime-Generated Artifacts (Not Versioned)

The following paths are generated during runs/tests and should remain untracked:
- `outputs/`
- `tests/temp/`
- `tests/outputs/`
- `tests/reports/`
- `src/chemistry_multiagent/agents/outputs/`
- cache artifacts (`__pycache__/`, `*.pyc`, `.DS_Store`)

## Internal / Legacy Files Retained

Some internal or legacy files are retained for compatibility and developer reference, for example:
- `src/chemistry_multiagent/controllers/chemistry_multiagent_controller_test.py`
- `src/chemistry_multiagent/agents/execution_agent_test.py`

These should be treated as internal/developer-oriented content, not as public API surfaces.
