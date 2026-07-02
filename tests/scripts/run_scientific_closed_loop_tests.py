import os
import json

from path_utils import TESTS_DIR, TEST_REPORTS_DIR, TOOLPOOL_PATH, add_src_to_path

add_src_to_path()

from chemistry_multiagent.agents.retrieval_agent import RetrievalAgent
from chemistry_multiagent.agents.hypothesis_agent import HypothesisAgent
from chemistry_multiagent.agents.planner_agent import PlannerAgent
from chemistry_multiagent.agents.execution_agent import ExecutionAgent
from chemistry_multiagent.agents.reflection_agent import ReflectionAgent

WORK_DIR = str(TESTS_DIR)
TOOLPOOL = str(TOOLPOOL_PATH)
os.makedirs(TEST_REPORTS_DIR, exist_ok=True)

with open(os.path.join(WORK_DIR, "inputs", "questions", "q3_barrier_contradiction.txt"), "r", encoding="utf-8") as f:
    question = f.read().strip()

retrieval = RetrievalAgent(deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"), embedder_name="bge")
hypothesis = HypothesisAgent(deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"))
planner = PlannerAgent(
    deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
    toolpool_path=TOOLPOOL,
    expert_model_name=os.environ.get("ARCHE_CHEM_MODEL_NAME", "qwen2.5-7b-instruct"),
    expert_model_path=os.environ.get("ARCHE_CHEM_MODEL_PATH"),
    expert_backend=os.environ.get("ARCHE_CHEM_BACKEND", "local_hf"),
    enable_expert_review=True
)
execution = ExecutionAgent(
    deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
    toolpool_path=TOOLPOOL,
    expert_model_name=os.environ.get("ARCHE_CHEM_MODEL_NAME", "qwen2.5-7b-instruct"),
    expert_model_path=os.environ.get("ARCHE_CHEM_MODEL_PATH"),
    expert_backend=os.environ.get("ARCHE_CHEM_BACKEND", "local_hf"),
    enable_expert_analysis=True
)
reflection = ReflectionAgent(deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"))

history = []
followup_history = []
max_rounds = 2

retrieval_result = retrieval.process_question(
    question=question,
    pdf_dir=os.path.join(WORK_DIR, "inputs", "papers"),
    index_dir=os.path.join(WORK_DIR, "temp", "index"),
    search_papers=False
)

hypothesis_result = hypothesis.generate_and_rank_hypotheses(
    research_question=question,
    literature_review=retrieval_result.get("literature_review", ""),
    num_queries=2,
    num_hypotheses_per_query=2,
    top_n=2
)

for current_round in range(1, max_rounds + 1):
    ranked = hypothesis_result.get("ranked_strategies") or hypothesis_result.get("top_n_strategies") or []
    if not ranked:
        raise RuntimeError("No ranked strategies available")

    selected_strategy = ranked[0]
    chemistry_context = retrieval_result.get("chemistry_context", {})

    planning_result = planner.generate_workflows_for_top_strategies(
        ranked_strategies=[selected_strategy],
        question=question,
        top_n=1,
        chemistry_context=chemistry_context
    )

    protocols = planning_result.get("optimized_protocols", [])
    execution_result = execution.execute_multiple_workflows(protocols)
    workflow_execution_result = execution_result["results"][0] if execution_result.get("results") else execution_result

    scientific_evidence = {
        "retrieval_result": retrieval_result,
        "hypothesis_result": hypothesis_result,
        "execution_result": execution_result,
        "followup_retrieval_history": followup_history,
        "chemistry_context": chemistry_context,
        "selected_strategy_profile": selected_strategy.get("computation_profile", {})
    }

    reflection_result = reflection.reflect(
        selected_strategy=selected_strategy,
        workflow=protocols[0],
        execution_result=workflow_execution_result,
        scientific_evidence=scientific_evidence,
        prior_reflections=history,
        retry_count=current_round - 1,
        reflection_round=current_round
    )

    history.append(reflection_result)

    decision = reflection_result.get("decision")
    if decision == "accept" or decision == "stop":
        break

    if decision == "revise_hypothesis":
        updated_queries = reflection_result.get("updated_queries", [])
        followup = retrieval.retrieve_followup_evidence(
            original_question=question,
            evidence_needs=updated_queries,
            pdf_dir=os.path.join(WORK_DIR, "inputs", "papers"),
            index_dir=os.path.join(WORK_DIR, "temp", "index")
        )
        followup_history.append(followup)

        evidence_results = [followup] if isinstance(followup, dict) else []
        hypothesis_result = hypothesis.revise_hypotheses_from_reflection(
            hypotheses=hypothesis_result.get("optimized_hypotheses") or hypothesis_result.get("ranked_strategies") or [],
            reflection_result=reflection_result,
            evidence_results=evidence_results
        )

    elif decision == "revise_workflow":
        # 下一轮保持 hypothesis_result，不重建 hypothesis，只重新走 Planner
        pass

report = {
    "question": question,
    "retrieval_result": retrieval_result,
    "final_hypothesis_result": hypothesis_result,
    "reflection_history": history,
    "followup_retrieval_history": followup_history
}

with open(os.path.join(WORK_DIR, "reports", "scientific_closed_loop_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print("Saved scientific_closed_loop_report.json")
