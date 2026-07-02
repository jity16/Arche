import os
import sys
import json
import time
import datetime
from typing import Any, Dict, Optional

from path_utils import PROJECT_ROOT, TEST_TEMP_DIR, TOOLPOOL_PATH, add_src_to_path, require_gaussian_demo_enabled

add_src_to_path()

from chemistry_multiagent.agents.execution_agent import ExecutionAgent, ExecutionStep

TOOLPOOL = str(TOOLPOOL_PATH)

RUN_ID = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
WORK_DIR = str(TEST_TEMP_DIR / f"hybrid_workflow_{RUN_ID}")
os.makedirs(WORK_DIR, exist_ok=True)


def dump_json(name: str, obj: Dict[str, Any]) -> None:
    path = os.path.join(WORK_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"saved: {path}")


def dump_step(name: str, step: ExecutionStep) -> None:
    payload = {
        "step_id": step.step_id,
        "step_number": step.step_number,
        "step_name": step.step_name,
        "tool_name": step.tool_name,
        "description": step.description,
        "status": step.status,
        "error": step.error,
        "error_info": step.error_info,
        "input_data": step.input_data,
        "output_files": step.output_files,
        "artifacts": step.artifacts,
        "raw_output": step.raw_output,
        "parsed_results": step.parsed_results,
        "validation": step.validation,
        "duration": step.duration,
        "working_directory": step.working_directory,
    }
    dump_json(name, payload)


def summarize_step(step: ExecutionStep) -> Dict[str, Any]:
    raw = step.raw_output if isinstance(step.raw_output, dict) else {}
    return {
        "step_number": step.step_number,
        "tool_name": step.tool_name,
        "status": step.status,
        "error": step.error,
        "execution_mode": raw.get("execution_mode"),
        "execution_backend": raw.get("execution_backend"),
        "message": raw.get("message"),
        "output_files": step.output_files,
    }


def summarize_gaussian_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": result.get("success"),
        "execution_mode": result.get("execution_mode"),
        "scheduler": result.get("scheduler"),
        "status": result.get("status"),
        "submitted": result.get("submitted"),
        "completed": result.get("completed"),
        "job_id": result.get("job_id"),
        "work_dir": result.get("work_dir"),
        "log_path": (result.get("output_artifacts") or {}).get("log"),
        "chk_path": (result.get("output_artifacts") or {}).get("chk"),
        "message": result.get("message"),
        "error": result.get("error"),
        "has_parsed_results": isinstance(result.get("parsed_results"), dict),
    }


def _extract_raw_result(step: ExecutionStep) -> Any:
    if isinstance(step.raw_output, dict):
        return step.raw_output.get("raw_result")
    return None


def extract_output_path(step: ExecutionStep, suffix: str) -> Optional[str]:
    raw_result = _extract_raw_result(step)

    # import backend often returns raw_result directly as a path string
    if isinstance(raw_result, str) and raw_result.lower().endswith(suffix.lower()) and os.path.exists(raw_result):
        return os.path.abspath(raw_result)

    # some tools may wrap output path in dict
    if isinstance(raw_result, dict):
        for key in ["output_path", "output_sdf_path", "output_gjf_path", "gjf_path", "sdf_path"]:
            value = raw_result.get(key)
            if isinstance(value, str) and value.lower().endswith(suffix.lower()) and os.path.exists(value):
                return os.path.abspath(value)

    # fallback to output_files
    for p in step.output_files or []:
        if isinstance(p, str) and p.lower().endswith(suffix.lower()) and os.path.exists(p):
            return os.path.abspath(p)

    return None


def extract_route_section(step: ExecutionStep) -> Optional[str]:
    raw_result = _extract_raw_result(step)

    # import backend: structured dict from generate_gaussian_code_result
    if isinstance(raw_result, dict):
        for key in ["gaussian_code", "route_section", "corrected_gaussian_code"]:
            value = raw_result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    # subprocess backend: stdout may contain JSON string
    if isinstance(step.raw_output, dict):
        rr = step.raw_output.get("raw_result")
        if isinstance(rr, dict):
            stdout = rr.get("stdout")
            if isinstance(stdout, str) and stdout.strip():
                try:
                    parsed = json.loads(stdout)
                    for key in ["gaussian_code", "route_section", "corrected_gaussian_code"]:
                        value = parsed.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
                except Exception:
                    pass

    return None


def main():
    if not require_gaussian_demo_enabled("Hybrid real-tool + Gaussian demo"):
        return

    print("========== Hybrid Real Tool + Gaussian Demo ==========")
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"TOOLPOOL:     {TOOLPOOL}")
    print(f"WORK_DIR:     {WORK_DIR}")

    agent = ExecutionAgent(
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
        toolpool_path=TOOLPOOL,
        expert_model_name=os.environ.get("ARCHE_CHEM_MODEL_NAME", "qwen2.5-7b-instruct"),
        expert_model_path=os.environ.get("ARCHE_CHEM_MODEL_PATH"),
        expert_backend=os.environ.get("ARCHE_CHEM_BACKEND", "local_hf"),
        enable_expert_analysis=True,
        gaussian_execution_mode="local_shell",
        gaussian_command=os.environ.get("GAUSSIAN_COMMAND", "g16"),
        gaussian_job_root=WORK_DIR,
    )

    # ---------------- Step 1: smiles_to_sdf ----------------
    sdf_path = os.path.join(WORK_DIR, "ethanol.sdf")
    step1 = ExecutionStep(
        step_number=1,
        description="Convert ethanol SMILES to SDF",
        tool_name="smiles2sdf",
        expected_input="SMILES list",
        expected_output=sdf_path,
        scientific_context={"smiles": "CCO"},
        working_directory=WORK_DIR,
    )
    input1 = {
        "smiles_list": ["CCO"],
        "output_sdf_path": sdf_path,
    }
    r1 = agent.execute_tool_step(step1, input1)
    dump_step("step1_smiles_to_sdf.json", r1)

    selected_sdf = extract_output_path(r1, ".sdf")
    if not selected_sdf:
        raise RuntimeError("Step 1 failed to produce a usable SDF file.")

    # ---------------- Step 2: generate_gaussian_code ----------------
    step2 = ExecutionStep(
        step_number=2,
        description="Generate Gaussian route section for ethanol optimization",
        tool_name="generate_gaussian_code",
        expected_input="Natural-language task description",
        expected_output="Gaussian route section",
        scientific_context={
            "scientific_question": (
                "Generate one concise Gaussian route section for a very small, "
                "fast geometry optimization of ethanol in the gas phase. "
                "Output only a valid Gaussian route line."
            )
        },
        working_directory=WORK_DIR,
    )
    input2 = {
        "user_input": (
            "Generate one concise Gaussian route section for a very small, "
            "fast geometry optimization of ethanol in the gas phase. "
            "Output only a valid Gaussian route line."
        ),
        "verbose": True,
        "fast_mode": True,
        "allow_api_fallback": True,
        "model_config": {
            "temperature": 0.2,
            "max_tokens": 128,
            "backend": os.environ.get("ARCHE_CHEM_BACKEND", "local_hf"),
            "model": os.environ.get("ARCHE_CHEM_MODEL_PATH"),
            "model_name": os.environ.get("ARCHE_CHEM_MODEL_NAME", "qwen2.5-7b-instruct"),
        },
    }
    r2 = agent.execute_tool_step(step2, input2)
    dump_step("step2_generate_gaussian_code.json", r2)

    route_section = extract_route_section(r2) or "#p HF/3-21G Opt"
    print(f"[INFO] selected route section: {route_section}")

    # ---------------- Step 3: sdf_to_gjf ----------------
    gjf_path = os.path.join(WORK_DIR, "ethanol_opt.gjf")
    step3 = ExecutionStep(
        step_number=3,
        description="Convert ethanol SDF to Gaussian input file",
        tool_name="sdf_to_gjf",
        expected_input=selected_sdf,
        expected_output=gjf_path,
        route_section=route_section,
        scientific_context={
            "charge": 0,
            "multiplicity": 1,
            "scientific_question": "Prepare Gaussian input for ethanol optimization",
        },
        working_directory=WORK_DIR,
    )
    input3 = {
        "sdf_path": selected_sdf,
        "gjf_path": gjf_path,
        "route_parameters": route_section,
        "title": "Ethanol optimization input",
        "nprocshared": 4,
        "mem": "2GB",
    }
    r3 = agent.execute_tool_step(step3, input3)
    dump_step("step3_sdf_to_gjf.json", r3)

    selected_gjf = extract_output_path(r3, ".gjf")
    if not selected_gjf:
        raise RuntimeError("Step 3 failed to produce a usable GJF file.")

    # ---------------- Step 4: Gaussian ----------------
    step4 = ExecutionStep(
        step_number=4,
        description="Run Gaussian optimization on ethanol",
        tool_name="Gaussian",
        expected_input=selected_gjf,
        expected_output=os.path.join(WORK_DIR, "ethanol_opt.log"),
        job_type="opt",
        route_section=route_section,
        scientific_context={
            "scientific_question": "Run Gaussian optimization for ethanol",
            "chemistry_context": {
                "candidate_elements": ["C", "H", "O"],
                "needs_ts": False,
                "needs_irc": False,
            },
        },
        working_directory=WORK_DIR,
        artifacts=[
            {
                "type": "input_file",
                "path": selected_gjf,
                "format": "gjf",
            }
        ],
    )

    input4 = {
        "gjf_path": selected_gjf,
        "input_path": selected_gjf,
        "work_dir": WORK_DIR,
        "job_name": "ethanol_opt_test",
        "job_type": "opt",
        "route_section": route_section,
    }

    result4 = agent.execute_gaussian_related_tool(
        tool_name="Gaussian",
        input_data=input4,
        step=step4,
    )
    dump_json("step4_gaussian_result_first.json", result4)
    dump_json("step4_gaussian_summary_first.json", summarize_gaussian_result(result4))

    result4_poll = None
    if result4.get("status") in {"submitted", "running", "prepared"}:
        print("Gaussian job not finished yet, sleeping 5s then polling once...")
        time.sleep(5)
        result4_poll = agent.execute_gaussian_related_tool(
            tool_name="Gaussian",
            input_data=input4,
            step=step4,
        )
        dump_json("step4_gaussian_result_poll.json", result4_poll)
        dump_json("step4_gaussian_summary_poll.json", summarize_gaussian_result(result4_poll))

    summary = {
        "step1": summarize_step(r1),
        "step2": summarize_step(r2),
        "step3": summarize_step(r3),
        "step4_first": summarize_gaussian_result(result4),
        "step4_poll": summarize_gaussian_result(result4_poll) if isinstance(result4_poll, dict) else None,
        "selected_sdf": selected_sdf,
        "selected_gjf": selected_gjf,
        "selected_route_section": route_section,
    }
    dump_json("hybrid_summary.json", summary)

    print("\n=== Hybrid Summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
