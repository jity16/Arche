# Architecture Overview

This project uses a controller-driven multi-agent workflow for computational chemistry tasks.

## High-Level Flow

1. Retrieval phase
- gathers and summarizes literature context

2. Hypothesis phase
- generates and ranks candidate scientific strategies

3. Planner phase
- builds executable workflow protocols from selected strategies

4. Execution phase
- executes workflow steps via tool routing
- supports Gaussian-oriented execution modes (`replay`, `local_shell`, `slurm`)

5. Reflection phase
- reviews evidence and execution quality
- decides whether to accept, stop, or revise workflow/hypothesis

## Orchestration Model

The controller manages a bounded closed-loop process with limits on reflection rounds and unrecoverable failures.

Main production orchestrator:
- `src/chemistry_multiagent/controllers/chemistry_multiagent_controller.py`

## Gaussian Waiting and Resume (High Level)

When Gaussian-related steps are still pending (e.g., running/queued), the controller can pause with status:
- `waiting_for_gaussian_jobs`

A waiting-state snapshot is written to:
- `outputs/multiagent/waiting_gaussian_jobs_state.json`

Resume entry point:
- controller CLI `--resume-state <path>`

Resume behavior (current intent):
- load and validate waiting snapshot
- restore minimal workflow state
- re-enter execution boundary and poll/recover jobs through existing execution-agent logic
- if still pending, write updated waiting snapshot and exit
- once pending jobs resolve, continue reflection and finalization

## Execution Tool Routing Notes

Execution routing combines:
- direct handling of Gaussian-related paths
- top-level tool script resolution under `src/chemistry_multiagent/tools`
- simulated fallback for unsupported/unavailable tools in some paths

Because this is research software, tool behavior depends on local environment and available external software.

## Maturity Notes

This architecture is functional for experimentation and controlled studies, but not yet standardized as a production package with a strict compatibility matrix.
