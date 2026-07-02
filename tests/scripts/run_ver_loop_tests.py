import os
import json

from path_utils import TESTS_DIR, TEST_OUTPUTS_DIR, TOOLPOOL_PATH, add_src_to_path

add_src_to_path()

from chemistry_multiagent.agents.planner_agent import PlannerAgent
from chemistry_multiagent.agents.execution_agent import ExecutionAgent
from chemistry_multiagent.agents.reflection_agent import ReflectionAgent

WORK_DIR = str(TESTS_DIR)
TOOLPOOL = str(TOOLPOOL_PATH)
os.makedirs(TEST_OUTPUTS_DIR, exist_ok=True)

with open(os.path.join(WORK_DIR, "inputs", "questions", "q1_ts_support.txt"), "r", encoding="utf-8") as f:
    question = f.read().strip()

with open(os.path.join(WORK_DIR, "outputs", "retrieval_result.json"), "r", encoding="utf-8") as f:
    retrieval_result = json.load(f)

with open(os.path.join(WORK_DIR, "outputs", "hypothesis_result.json"), "r", encoding="utf-8") as f:
    hypothesis_result = json.load(f)

ranked = hypothesis_result.get("ranked_strategies") or hypothesis_result.get("top_n_strategies") or []
if not ranked:
    raise RuntimeError("No ranked strategies found in hypothesis_result.json")

selected_strategy = ranked[0]
chemistry_context = retrieval_result.get("chemistry_context", {})
selected_strategy_profile = selected_strategy.get("computation_profile", {})

planner = PlannerAgent(
    deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
    toolpool_path=TOOLPOOL,
    expert_model_name=os.environ.get("ARCHE_CHEM_MODEL_NAME", "qwen2.5-7b-instruct"),
    expert_model_path=os.environ.get("ARCHE_CHEM_MODEL_PATH"),
    expert_backend=os.environ.get("ARCHE_CHEM_BACKEND", "local_hf"),
    enable_expert_review=True
)

planning_result = planner.generate_workflows_for_top_strategies(
    ranked_strategies=[selected_strategy],
    question=question,
    top_n=1,
    chemistry_context=chemistry_context
)

with open(os.path.join(WORK_DIR, "outputs", "planning_result_round1.json"), "w", encoding="utf-8") as f:
    json.dump(planning_result, f, indent=2, ensure_ascii=False)

protocols = planning_result.get("optimized_protocols", [])
if not protocols:
    raise RuntimeError("Planner did not generate optimized_protocols")

execution = ExecutionAgent(
    deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
    toolpool_path=TOOLPOOL,
    expert_model_name=os.environ.get("ARCHE_CHEM_MODEL_NAME", "qwen2.5-7b-instruct"),
    expert_model_path=os.environ.get("ARCHE_CHEM_MODEL_PATH"),
    expert_backend=os.environ.get("ARCHE_CHEM_BACKEND", "local_hf"),
    enable_expert_analysis=True
)

execution_result = execution.execute_multiple_workflows(protocols)
with open(os.path.join(WORK_DIR, "outputs", "execution_result_round1.json"), "w", encoding="utf-8") as f:
    json.dump(execution_result, f, indent=2, ensure_ascii=False)

reflection = ReflectionAgent(
    deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY")
)

workflow = protocols[0]
workflow_execution_result = execution_result["results"][0] if execution_result.get("results") else execution_result

scientific_evidence = {
    "retrieval_result": retrieval_result,
    "hypothesis_result": hypothesis_result,
    "execution_result": execution_result,
    "chemistry_context": chemistry_context,
    "selected_strategy_profile": selected_strategy_profile,
}

reflection_result = reflection.reflect(
    selected_strategy=selected_strategy,
    workflow=workflow,
    execution_result=workflow_execution_result,
    scientific_evidence=scientific_evidence,
    prior_reflections=[],
    retry_count=0,
    reflection_round=1
)

with open(os.path.join(WORK_DIR, "outputs", "reflection_result_round1.json"), "w", encoding="utf-8") as f:
    json.dump(reflection_result, f, indent=2, ensure_ascii=False)

print("Reflection decision:", reflection_result.get("decision"))
