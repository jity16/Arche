import os
import sys
import json
import argparse
from datetime import datetime

# ---- project path setup ----
from path_utils import PROJECT_ROOT, SRC_DIR, TESTS_DIR, TEST_INPUTS_DIR, TEST_TEMP_DIR, TOOLPOOL_PATH, add_src_to_path

add_src_to_path()

from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def save_json(path: str, data) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def summarize_result(result: dict) -> dict:
    summary = {
        "status": result.get("status"),
        "question": result.get("question") or result.get("scientific_question"),
        "has_final_conclusion": "final_conclusion" in result,
        "final_status": None,
        "run_mode": None,
        "expert_backend_audit_summary": None,
        "num_reflections": 0,
        "num_revision_events": 0,
    }

    final_conclusion = result.get("final_conclusion", {})
    if isinstance(final_conclusion, dict):
        summary["final_status"] = final_conclusion.get("final_status") or final_conclusion.get("status")
        summary["run_mode"] = final_conclusion.get("run_mode")
        summary["expert_backend_audit_summary"] = final_conclusion.get("expert_backend_audit_summary")

    reflection_history = result.get("reflection_history", [])
    if isinstance(reflection_history, list):
        summary["num_reflections"] = len(reflection_history)

    revision_history = result.get("workflow_state", {}).get("revision_history", [])
    if isinstance(revision_history, list):
        summary["num_revision_events"] = len(revision_history)

    return summary


def print_summary(summary: dict) -> None:
    print("\n========== Controller Test Summary ==========")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print("============================================\n")


def validate_minimum_expectations(result: dict, mode: str) -> list:
    errors = []

    if not isinstance(result, dict):
        return ["Result is not a dict"]

    if mode == "bounded":
        if "final_conclusion" not in result:
            errors.append("Missing final_conclusion in bounded result")
        if "workflow_state" not in result:
            errors.append("Missing workflow_state in bounded result")
        if "reflection_history" not in result:
            errors.append("Missing reflection_history in bounded result")

        workflow_state = result.get("workflow_state", {})
        if isinstance(workflow_state, dict):
            for key in [
                "expert_review_history",
                "gaussian_analysis_history",
                "revision_history",
                "expert_backend_audit_history",
                "expert_backend_audit_summary",
            ]:
                if key not in workflow_state:
                    errors.append(f"workflow_state missing key: {key}")

    elif mode == "complete":
        # run_complete_workflow is legacy-compatible wrapper
        for key in ["retrieval_result", "hypothesis_result", "planner_result", "execution_result", "summary"]:
            if key not in result:
                errors.append(f"Missing key in complete result: {key}")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Controller integration test for ChemistryMultiAgentController")
    parser.add_argument(
        "--question",
        default="Identify a plausible transition-state search workflow for a simple nucleophilic addition reaction and specify how to validate the TS.",
        help="Scientific question for controller test"
    )
    parser.add_argument(
        "--mode",
        choices=["bounded", "complete"],
        default="bounded",
        help="Controller test mode"
    )
    parser.add_argument(
        "--work-dir",
        default=str(TESTS_DIR),
        help="Controller work directory"
    )
    parser.add_argument(
        "--toolpool",
        default=str(TOOLPOOL_PATH),
        help="Toolpool path"
    )
    parser.add_argument(
        "--pdf-dir",
        default=str(TEST_INPUTS_DIR / "papers"),
        help="PDF directory"
    )
    parser.add_argument(
        "--index-dir",
        default=str(TEST_TEMP_DIR / "index"),
        help="Index directory"
    )
    parser.add_argument("--num-queries", type=int, default=2)
    parser.add_argument("--num-hypotheses", type=int, default=2)
    parser.add_argument("--top-n", type=int, default=2)
    parser.add_argument("--max-reflection-rounds", type=int, default=2)
    parser.add_argument("--max-unrecoverable-failures", type=int, default=2)
    parser.add_argument("--search-papers", action="store_true", help="Enable external paper search")
    parser.add_argument("--disable-expert-review", action="store_true")
    args = parser.parse_args()

    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"SRC_DIR:      {SRC_DIR}")
    print(f"TOOLPOOL:     {args.toolpool}")

    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    expert_model_name = os.environ.get("ARCHE_CHEM_MODEL_NAME", "qwen2.5-7b-instruct")
    expert_model_path = os.environ.get("ARCHE_CHEM_MODEL_PATH")
    expert_backend = os.environ.get("ARCHE_CHEM_BACKEND", "local_hf")

    ensure_dir(args.work_dir)
    ensure_dir(args.pdf_dir)
    ensure_dir(args.index_dir)
    ensure_dir(os.path.join(args.work_dir, "reports"))

    controller = ChemistryMultiAgentController(
        deepseek_api_key=deepseek_api_key,
        work_dir=args.work_dir,
        toolpool_path=args.toolpool,
        expert_model_name=expert_model_name,
        expert_model_path=expert_model_path,
        expert_backend=expert_backend,
        enable_expert_review=not args.disable_expert_review,
    )

    started_at = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.mode == "bounded":
        result = controller.run_bounded_closed_loop_workflow(
            scientific_question=args.question,
            num_queries=args.num_queries,
            num_hypotheses_per_query=args.num_hypotheses,
            top_n_strategies=args.top_n,
            pdf_dir=args.pdf_dir,
            index_dir=args.index_dir,
            search_papers=args.search_papers,
            max_reflection_rounds=args.max_reflection_rounds,
            max_unrecoverable_failures=args.max_unrecoverable_failures,
        )
        output_path = os.path.join(args.work_dir, "reports", f"controller_bounded_result_{started_at}.json")
    else:
        result = controller.run_complete_workflow(
            scientific_question=args.question,
            num_queries=args.num_queries,
            num_hypotheses_per_query=args.num_hypotheses,
            top_n_strategies=args.top_n,
            pdf_dir=args.pdf_dir,
            index_dir=args.index_dir,
            search_papers=args.search_papers,
        )
        output_path = os.path.join(args.work_dir, "reports", f"controller_complete_result_{started_at}.json")

    save_json(output_path, result)
    print(f"Saved result to: {output_path}")

    summary = summarize_result(result)
    print_summary(summary)

    errors = validate_minimum_expectations(result, args.mode)
    if errors:
        print("Controller integration test finished with validation issues:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("Controller integration test passed minimum structural checks.")


if __name__ == "__main__":
    main()
