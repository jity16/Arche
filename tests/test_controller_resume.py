import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

def find_project_root() -> Path:
    env_root = os.environ.get("ARCHE_PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        if (parent / "src" / "chemistry_multiagent").exists():
            return parent

    raise RuntimeError("Cannot infer ARCHE project root. Set ARCHE_PROJECT_ROOT.")


PROJECT_ROOT = find_project_root()
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController


class ControllerResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work_dir = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _new_controller(self) -> ChemistryMultiAgentController:
        return ChemistryMultiAgentController(
            deepseek_api_key="",
            work_dir=self.work_dir,
            gaussian_execution_mode="local_shell",
        )

    def _planning_result(self) -> dict:
        return {
            "optimized_protocols": [
                {
                    "strategy_name": "S1",
                    "Steps": [
                        {
                            "Step_number": 1,
                            "Description": "Run gaussian",
                            "Tool": "run_gaussian",
                        }
                    ],
                }
            ]
        }

    def _pending_execution_result(self) -> dict:
        return {
            "results": [
                {
                    "workflow_name": "S1",
                    "status": "in_progress",
                    "workflow_outcome": "unknown",
                    "steps": [
                        {
                            "step_id": "1",
                            "step_number": 1,
                            "step_name": "Run gaussian",
                            "tool_name": "run_gaussian",
                            "raw_output": {
                                "execution_mode": "gaussian_job",
                                "status": "running",
                                "scheduler": "local_shell",
                                "job_id": "job-001",
                                "work_dir": "/tmp/job-001",
                                "job_state": {
                                    "status": "running",
                                    "scheduler": "local_shell",
                                    "job_id": "job-001",
                                    "work_dir": "/tmp/job-001",
                                    "state_path": "/tmp/job-001/.gaussian_job_state_1.json",
                                },
                            },
                        }
                    ],
                }
            ],
            "overall_success_rate": 0.0,
        }

    def _completed_execution_result(self) -> dict:
        return {
            "results": [
                {
                    "workflow_name": "S1",
                    "status": "success",
                    "workflow_outcome": "supported",
                    "steps": [
                        {
                            "step_id": "1",
                            "step_number": 1,
                            "step_name": "Run gaussian",
                            "tool_name": "run_gaussian",
                            "status": "success",
                            "raw_output": {
                                "execution_mode": "gaussian_job",
                                "status": "completed",
                                "job_state": {
                                    "status": "completed",
                                    "job_id": "job-001",
                                },
                            },
                        }
                    ],
                }
            ],
            "overall_success_rate": 1.0,
            "total_steps": 1,
            "successful_steps": 1,
            "failed_steps": 0,
        }

    def _pending_job_summary(self) -> list:
        return [
            {
                "workflow_name": "S1",
                "step_id": "1",
                "step_number": 1,
                "step_name": "Run gaussian",
                "tool_name": "run_gaussian",
                "status": "running",
                "scheduler": "local_shell",
                "job_id": "job-001",
                "work_dir": "/tmp/job-001",
                "state_path": "/tmp/job-001/.gaussian_job_state_1.json",
                "message": "job running",
            }
        ]

    def _base_workflow_state(self, planning_result: dict, execution_result: dict) -> dict:
        return {
            "status": "waiting_for_gaussian_jobs",
            "current_step": "execution_waiting",
            "start_time": time.time() - 10,
            "end_time": None,
            "current_question": "Q: test gaussian resume?",
            "chemistry_context": {},
            "selected_strategy_profile": {},
            "expert_review_history": [],
            "gaussian_analysis_history": [],
            "revision_history": [],
            "requested_expert_backend": "local_hf",
            "used_expert_backend": None,
            "fallback_triggered": False,
            "fallback_reason": None,
            "fallback_model": None,
            "expert_backend_audit_history": [],
            "expert_backend_audit_summary": {
                "requested_expert_backend": "local_hf",
                "used_expert_backend": None,
                "used_expert_backends": [],
                "fallback_triggered": False,
                "fallback_reason": None,
                "fallback_model": None,
                "expert_run_mode": "unknown",
            },
            "waiting_for_jobs": True,
            "can_resume": True,
            "gaussian_execution_mode": "local_shell",
            "pending_gaussian_jobs": self._pending_job_summary(),
            "last_planning_result": planning_result,
            "last_execution_result": execution_result,
        }

    def _build_waiting_snapshot(self) -> dict:
        planning = self._planning_result()
        pending_exec = self._pending_execution_result()
        workflow_state = self._base_workflow_state(planning, pending_exec)

        return {
            "scientific_question": "Q: test gaussian resume?",
            "status": "waiting_for_gaussian_jobs",
            "can_resume": True,
            "workflow_start_time": time.time() - 15,
            "workflow_state": workflow_state,
            "resume_state": {
                "scientific_question": "Q: test gaussian resume?",
                "current_round": 1,
                "planning_result": planning,
                "execution_result": pending_exec,
                "workflow_state": workflow_state,
                "pending_gaussian_jobs": self._pending_job_summary(),
                "gaussian_execution_mode": "local_shell",
                "can_resume": True,
            },
            "pending_gaussian_jobs": self._pending_job_summary(),
            "gaussian_execution_mode": "local_shell",
            "last_planning_result": planning,
            "last_execution_result": pending_exec,
            "retrieval_phase": {"literature_review": "mock"},
            "hypothesis_phase": {
                "ranked_strategies": [{"strategy_name": "S1"}],
                "top_n_strategies": [{"strategy_name": "S1"}],
            },
            "planning_rounds": [{"round": 1, "result": planning, "status": "success"}],
            "execution_rounds": [{"round": 1, "result": pending_exec, "status": "success"}],
            "reflection_rounds": [],
            "revision_events": [],
            "retrieval_followup_rounds": [],
            "stop_conditions": {
                "triggered_condition": "waiting_for_gaussian_jobs",
                "final_round": 1,
                "max_reflection_rounds": 3,
                "max_unrecoverable_failures": 3,
            },
        }

    def _write_snapshot(self, payload: dict, name: str = "waiting_gaussian_jobs_state.json") -> str:
        out_dir = Path(self.work_dir) / "outputs" / "multiagent"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def _accept_reflection(self) -> dict:
        return {
            "decision": "accept",
            "identified_problems": [],
            "workflow_revision_instructions": [],
            "hypothesis_revision_instructions": [],
            "recommended_actions": [],
            "evidence_summary": {},
        }

    def test_resume_from_waiting_state_reaches_finalization(self) -> None:
        controller = self._new_controller()
        state_path = self._write_snapshot(self._build_waiting_snapshot())

        planner_called = {"count": 0}

        def fake_planner(*args, **kwargs):
            planner_called["count"] += 1
            return {"error": "planner should not run in direct resume"}

        controller.run_planner_phase = fake_planner
        controller.run_execution_phase = lambda *args, **kwargs: self._completed_execution_result()
        controller.run_reflection_phase = lambda *args, **kwargs: self._accept_reflection()

        result = controller.resume_from_waiting_state(state_path, top_n_strategies=1)

        self.assertEqual(result.get("status"), "accepted")
        self.assertEqual(planner_called["count"], 0)
        self.assertEqual(len(result.get("reflection_rounds", [])), 1)
        self.assertTrue(result.get("execution_rounds", [])[-1].get("resumed_from_waiting"))
        self.assertTrue(os.path.exists(result.get("output_files", {}).get("complete_result", "")))

    def test_invalid_resume_state_file_returns_clear_error(self) -> None:
        controller = self._new_controller()
        invalid_payload = {
            "status": "waiting_for_gaussian_jobs",
            "can_resume": False,
            "workflow_state": {},
            "resume_state": {},
            "pending_gaussian_jobs": [],
            "gaussian_execution_mode": "local_shell",
        }
        state_path = self._write_snapshot(invalid_payload, name="invalid_waiting_state.json")

        result = controller.resume_from_waiting_state(state_path)

        self.assertEqual(result.get("status"), "error")
        self.assertIn("恢复状态校验失败", result.get("error", ""))

    def test_fresh_run_wrapper_path_still_available(self) -> None:
        controller = self._new_controller()
        self.assertTrue(hasattr(controller, "run_complete_workflow"))

        closed_loop_result = {
            "scientific_question": "Q: fresh path",
            "status": "accepted",
            "retrieval_phase": {
                "top_keywords": ["k1"],
                "literature_review": "lit",
                "relevant_papers": [],
            },
            "hypothesis_phase": {
                "ranked_strategies": [],
                "top_n_strategies": [],
                "optimized_hypotheses": [],
            },
            "planning_rounds": [{"result": {"optimized_protocols": [], "original_protocols": [], "optimization_ratio": 1.0}}],
            "execution_rounds": [{"result": {"overall_success_rate": 1.0, "total_steps": 1, "successful_steps": 1, "failed_steps": 0, "total_duration": 1.0, "final_output": "ok"}}],
            "final_conclusion": {"workflow_outcome": {"execution_success_rate": 1.0}},
            "total_duration_seconds": 1.2,
        }

        controller.run_bounded_closed_loop_workflow = lambda **kwargs: closed_loop_result

        result = controller.run_complete_workflow(scientific_question="Q: fresh path")

        self.assertEqual(result.get("status"), "success")
        self.assertIn("phases", result)
        self.assertIn("execution", result["phases"])

    def test_resume_pending_then_second_resume_finishes(self) -> None:
        controller = self._new_controller()
        state_path = self._write_snapshot(self._build_waiting_snapshot())

        execution_sequence = [self._pending_execution_result(), self._completed_execution_result()]
        reflection_calls = {"count": 0}

        def fake_execution(*args, **kwargs):
            return execution_sequence.pop(0)

        def fake_reflection(*args, **kwargs):
            reflection_calls["count"] += 1
            return self._accept_reflection()

        controller.run_execution_phase = fake_execution
        controller.run_reflection_phase = fake_reflection
        controller.run_planner_phase = lambda *args, **kwargs: {"error": "planner should not run in this test"}

        first = controller.resume_from_waiting_state(state_path)
        self.assertEqual(first.get("status"), "waiting_for_gaussian_jobs")
        self.assertTrue(first.get("can_resume"))
        self.assertEqual(reflection_calls["count"], 0)

        second_state_path = first.get("output_files", {}).get("waiting_state")
        self.assertTrue(second_state_path and os.path.exists(second_state_path))

        second = controller.resume_from_waiting_state(second_state_path)
        self.assertEqual(second.get("status"), "accepted")
        self.assertEqual(reflection_calls["count"], 1)
        self.assertEqual(second.get("stop_conditions", {}).get("triggered_condition"), "reflection_accept")


if __name__ == "__main__":
    unittest.main()
