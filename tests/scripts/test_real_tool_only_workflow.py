import os
import sys
import json
from typing import Any, Dict, List, Optional

from path_utils import PROJECT_ROOT, TEST_TEMP_DIR, TOOLPOOL_PATH, add_src_to_path

add_src_to_path()

from chemistry_multiagent.agents.execution_agent import ExecutionAgent, ExecutionStep

TOOLPOOL = str(TOOLPOOL_PATH)
WORK_DIR = str(TEST_TEMP_DIR / "real_tool_only")
os.makedirs(WORK_DIR, exist_ok=True)


def dump_step(name: str, step: ExecutionStep) -> None:
    """
    Serialize an ExecutionStep into JSON safely.
    """
    payload = {
        "step_id": step.step_id,
        "step_number": step.step_number,
        "step_name": step.step_name,
        "tool_name": step.tool_name,
        "description": step.description,
        "status": step.status,
        "error": step.error,
        "error_info": step.error_info,
        "output_files": step.output_files,
        "artifacts": step.artifacts,
        "raw_output": step.raw_output,
        "parsed_results": step.parsed_results,
        "validation": step.validation,
        "duration": step.duration,
    }
    path = os.path.join(WORK_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"saved: {path}")


def extract_real_tool_result(step: ExecutionStep) -> Dict[str, Any]:
    """
    Return the real-tool execution payload stored in step.raw_output.
    """
    if isinstance(step.raw_output, dict):
        return step.raw_output
    return {}


def pick_first_existing_sdf_from_step(step: ExecutionStep) -> Optional[str]:
    """
    Prefer explicit saved_sdf_paths from raw_result;
    fallback to output_files ending with .sdf.
    """
    raw = extract_real_tool_result(step)
    raw_result = raw.get("raw_result")

    if isinstance(raw_result, dict):
        saved_sdf_paths = raw_result.get("saved_sdf_paths")
        if isinstance(saved_sdf_paths, list):
            for p in saved_sdf_paths:
                if isinstance(p, str) and os.path.exists(p) and p.lower().endswith(".sdf"):
                    return os.path.abspath(p)

        output_path = raw_result.get("output_path")
        if isinstance(output_path, str) and os.path.exists(output_path) and output_path.lower().endswith(".sdf"):
            return os.path.abspath(output_path)

    for p in step.output_files or []:
        if isinstance(p, str) and os.path.exists(p) and p.lower().endswith(".sdf"):
            return os.path.abspath(p)

    return None


def summarize_step(step: ExecutionStep) -> Dict[str, Any]:
    raw = extract_real_tool_result(step)
    return {
        "step_number": step.step_number,
        "tool_name": step.tool_name,
        "status": step.status,
        "execution_mode": raw.get("execution_mode"),
        "execution_backend": raw.get("execution_backend"),
        "success": raw.get("success"),
        "message": raw.get("message"),
        "error": step.error or raw.get("error"),
        "output_files": step.output_files,
    }


def main():
    print("========== Real Tool Only Demo ==========")
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"TOOLPOOL:     {TOOLPOOL}")
    print(f"WORK_DIR:     {WORK_DIR}")
    print("[INFO] This demo uses real top-level tool wrappers. RDKit/ASE/OpenBabel availability depends on the environment.")

    agent = ExecutionAgent(
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
        toolpool_path=TOOLPOOL,
        expert_model_name=os.environ.get("ARCHE_CHEM_MODEL_NAME", "qwen2.5-7b-instruct"),
        expert_model_path=os.environ.get("ARCHE_CHEM_MODEL_PATH"),
        expert_backend=os.environ.get("ARCHE_CHEM_BACKEND", "local_hf"),
        enable_expert_analysis=True,
        gaussian_execution_mode="replay",
        gaussian_job_root=WORK_DIR,
    )

    # Step 1: SMILES -> SDF
    step1 = ExecutionStep(
        step_number=1,
        description="Convert ethanol SMILES to SDF",
        tool_name="smiles2sdf",
        expected_input="SMILES",
        expected_output="SDF",
        scientific_context={"smiles": "CCO"},
        working_directory=WORK_DIR,
    )

    input1 = {
        "smiles": "CCO",
        "output_sdf_path": os.path.join(WORK_DIR, "ethanol.sdf"),
    }
    r1 = agent.execute_tool_step(step1, input1)
    dump_step("step1_smiles_to_sdf.json", r1)

    # Step 2: Generate conformations
    step2 = ExecutionStep(
        step_number=2,
        description="Generate conformations for ethanol",
        tool_name="gen_conformation",
        expected_input="SMILES or SDF",
        expected_output="Conformer SDFs",
        scientific_context={"smiles": "CCO"},
        working_directory=WORK_DIR,
    )

    input2 = {
        "smiles": "CCO",
        "output_dir": os.path.join(WORK_DIR, "conformers"),
        "top_n": 3,
        "num_conformers": 10,
    }
    r2 = agent.execute_tool_step(step2, input2)
    dump_step("step2_generate_conformations.json", r2)

    # Step 3: best conformer SDF -> GJF
    best_sdf = pick_first_existing_sdf_from_step(r2)
    if best_sdf is None:
        # fallback to step1 SDF if conformer generation didn't produce per-conformer SDFs
        best_sdf = pick_first_existing_sdf_from_step(r1)

    step3 = ExecutionStep(
        step_number=3,
        description="Convert best conformer SDF to Gaussian input",
        tool_name="sdf_to_gjf",
        expected_input="SDF",
        expected_output="GJF",
        route_section="#p HF/3-21G Opt",
        scientific_context={
            "charge": 0,
            "multiplicity": 1,
            "scientific_question": "Prepare Gaussian input from generated conformer"
        },
        working_directory=WORK_DIR,
    )

    input3 = {
        "sdf_path": best_sdf,
        "output_gjf_path": os.path.join(WORK_DIR, "ethanol_from_best_conf.gjf"),
        "route_section": "#p HF/3-21G Opt",
        "charge": 0,
        "multiplicity": 1,
        "title": "Ethanol optimization input",
    }
    r3 = agent.execute_tool_step(step3, input3)
    dump_step("step3_sdf_to_gjf.json", r3)

    summary = {
        "step1": summarize_step(r1),
        "step2": summarize_step(r2),
        "step3": summarize_step(r3),
        "selected_sdf_for_step3": best_sdf,
    }

    summary_path = os.path.join(WORK_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nsaved: {summary_path}")


if __name__ == "__main__":
    main()
