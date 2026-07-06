import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "src" / "chemistry_multiagent" / "controllers" / "chemistry_multiagent_controller.py"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_final_conclusion_summary_uses_chinese_report_copy_not_english_templates():
    source = CONTROLLER.read_text(encoding="utf-8")

    assert "_format_final_conclusion_summary" in source
    assert "Preliminary results for" not in source
    assert "require further validation" not in source
    assert "真实 Gaussian 计算结果:" not in source
    assert "本次计算得到" in source
    assert "解释边界" in source


def test_mechanism_conclusion_rejects_water_only_gaussian_values(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)
    question = (
        "I plan to verify the mechanism of an asymmetric catalytic reaction using the "
        "reaction of p-nitrobenzaldehyde O=Cc1ccc(cc1)[N+](=O)[O-] with CC(=O)C "
        "under catalysis, producing CC(=O)C(O)c1ccc([N+](=O)[O-])cc1. "
        "Could you provide a specific approach to validate the reaction mechanism?"
    )
    execution_result = {
        "overall_success_rate": 0.5428571428571428,
        "results": [
            {
                "workflow_outcome": "partially_supported",
                "overall_status": "partial_success",
                "steps": [
                    {
                        "step_id": "1",
                        "step_number": 1,
                        "step_name": "Generate initial transition state guess for enamine formation",
                        "tool_name": "main",
                        "status": "failed",
                        "error": "TS pipeline missing",
                    },
                    {
                        "step_id": "4",
                        "step_number": 4,
                        "step_name": "Run Gaussian to perform transition state optimization and frequency analysis",
                        "tool_name": "generate_gaussian_code",
                        "status": "success",
                        "raw_output": {
                            "execution_mode": "gaussian_job",
                            "status": "completed",
                            "deterministic_provenance": {
                                "mol_label": "sdf:water_initial.sdf",
                                "geometry_source": "common_name_fallback",
                                "smiles": "O",
                                "atom_count": 3,
                            },
                            "parsed_results": {
                                "job_type": "freq",
                                "normal_termination": True,
                                "scf_energy": -76.4076698492,
                                "E_HOMO": -0.29119,
                                "E_LUMO": 0.05784,
                                "HOMO_LUMO_gap": 0.34903,
                                "frequencies": [-0.0019, 1713.8934, 1713.8934],
                            },
                            "spectroscopy": {
                                "ir_peaks": [
                                    {"freq_cm1": 1713.9, "intensity_km_mol": 75.7},
                                    {"freq_cm1": 3846.7, "intensity_km_mol": 19.3},
                                    {"freq_cm1": 3725.1, "intensity_km_mol": 1.7},
                                ]
                            },
                        },
                    },
                    {
                        "step_id": "6",
                        "step_number": 6,
                        "step_name": "Run IRC to verify the transition state connects reactants and products",
                        "tool_name": "get_gjf_from_log",
                        "status": "failed",
                        "error": "unsupported_subprocess_cli",
                    },
                ],
            }
        ],
    }

    conclusion = controller._synthesize_final_conclusion(
        scientific_question=question,
        status="completed",
        structured_record={"execution_rounds": [{"status": "success"}], "revision_events": [{"status": "attempted"}]},
        retrieval_result={"literature_review": "retrieved"},
        hypothesis_result={"ranked_strategies": [{"strategy_name": "TS/IRC mechanism validation"}]},
        planning_result={},
        execution_result=execution_result,
        final_round=2,
        final_decision="revise_workflow",
    )

    assert conclusion["conclusion_type"] != "supported"
    assert "HOMO-LUMO" not in conclusion["conclusion_summary"]
    assert "SCF 能量" not in conclusion["conclusion_summary"]
    assert "红外振动峰" not in conclusion["conclusion_summary"]
    assert not any(item.get("type") == "computed_results" for item in conclusion["key_findings"])


def test_poisoned_geometry_context_does_not_render_mechanism_template(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {
        "expert_backend_audit_summary": {},
        "chemistry_context": {
            "needs_ts": False,
            "needs_irc": True,
            "suspected_job_types": ["opt", "irc"],
        },
    }
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)

    conclusion = controller._synthesize_final_conclusion(
        scientific_question=r"预测 $\ce{H2O}$ 在 $\text{B3LYP/6-31G}^*$ 下的优化几何构型",
        status="completed",
        structured_record={},
        retrieval_result={"literature_review": "retrieved"},
        hypothesis_result={"ranked_strategies": [{"strategy_name": "Water geometry benchmark"}]},
        planning_result={},
        execution_result={"results": []},
        final_round=2,
        final_decision="revise_workflow",
    )

    analysis = conclusion["integrated_analysis"]
    rendered = "\n".join(
        [conclusion["conclusion_summary"]]
        + [section.get("body", "") for section in analysis["sections"]]
        + ["\n".join(section.get("items", [])) for section in analysis["sections"]]
    )

    assert "triflic acid" not in rendered
    assert "enamine" not in rendered
    assert "beta-hydroxy ketone" not in rendered
    assert [section["title"] for section in analysis["sections"][:2]] == ["综合判断", "证据来源"]


def test_final_conclusion_integrates_outputs_from_all_agents(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)
    question = (
        "Validate the mechanism of an asymmetric aldol reaction between "
        "p-nitrobenzaldehyde and acetone with acid/chiral amine co-catalysis."
    )
    retrieval_result = {
        "literature_review": (
            "Prior reports indicate enamine formation, acid-assisted aldehyde activation, "
            "and stereocontrolling C-C bond formation are mechanistically decisive."
        ),
        "mechanistic_clues": [
            "enamine pathway is likely for acetone activation",
            "acid co-catalyst may stabilize the C-C bond-forming transition state",
        ],
        "limitations": ["No direct TS/IRC evidence was retrieved."],
    }
    hypothesis_result = {
        "ranked_strategies": [
            {
                "strategy_name": "Explicit co-catalyst C-C bond-forming TS validation",
                "reasoning": (
                    "Compare acid-assisted and non-assisted transition states to test whether "
                    "the co-catalyst changes the stereodetermining barrier."
                ),
                "confidence": 0.74,
            }
        ],
    }
    planning_result = {
        "optimized_protocols": [
            {
                "workflow_name": "TS/IRC/free-energy mechanism validation",
                "Steps": [
                    {"step_id": "1", "description": "Enumerate reactant and catalyst conformers"},
                    {"step_id": "2", "description": "Optimize C-C bond-forming transition states"},
                    {"step_id": "3", "description": "Run IRC and compute Gibbs free-energy barriers"},
                ],
            }
        ]
    }
    execution_result = {
        "overall_success_rate": 0.55,
        "results": [
            {
                "workflow_outcome": "partially_supported",
                "overall_status": "partial_success",
                "steps": [
                    {
                        "step_id": "1",
                        "step_number": 1,
                        "step_name": "Enumerate reactant and catalyst conformers",
                        "status": "success",
                    },
                    {
                        "step_id": "2",
                        "step_number": 2,
                        "step_name": "Optimize C-C bond-forming transition states",
                        "status": "failed",
                        "error": "TS initial guess failed",
                    },
                    {
                        "step_id": "3",
                        "step_number": 3,
                        "step_name": "Run IRC and compute Gibbs free-energy barriers",
                        "status": "failed",
                        "error": "unsupported_subprocess_cli",
                    },
                ],
                "issues": ["IRC not completed"],
            }
        ],
    }
    structured_record = {
        "execution_rounds": [{"round": 1, "status": "success", "result": execution_result}],
        "revision_events": [{"status": "attempted"}],
        "reflection_rounds": [
            {
                "round": 1,
                "result": {
                    "decision": "revise_workflow",
                    "reasoning": "TS/IRC chain is incomplete; revise workflow before accepting mechanism.",
                    "identified_problems": ["TS optimization failed", "IRC missing"],
                    "recommended_actions": ["regenerate TS guesses", "rerun IRC after TS frequency check"],
                },
            }
        ],
    }

    conclusion = controller._synthesize_final_conclusion(
        scientific_question=question,
        status="completed",
        structured_record=structured_record,
        retrieval_result=retrieval_result,
        hypothesis_result=hypothesis_result,
        planning_result=planning_result,
        execution_result=execution_result,
        final_round=2,
        final_decision="revise_workflow",
    )

    analysis = conclusion.get("integrated_analysis")
    assert isinstance(analysis, dict)
    assert "结论摘要" in conclusion["conclusion_summary"]
    assert "优先验证" in conclusion["conclusion_summary"]
    assert "没有形成可靠" not in conclusion["conclusion_summary"]
    assert "Explicit co-catalyst" in analysis["overall_judgment"]
    assert "enamine" in analysis["evidence_from_retrieval"]
    assert "C-C bond-forming" in analysis["working_hypothesis"]
    assert "IRC" in analysis["planned_validation"]
    assert "revise_workflow" in analysis["reflection_verdict"]
    assert "工作假设" in analysis["scientific_conclusion"]
    assert "优先验证" in analysis["scientific_conclusion"]
    assert any("TS/IRC" in gap for gap in analysis["validation_gaps"])


def test_generic_mechanism_question_does_not_fall_back_to_aldol_template(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)
    question = "Validate whether the substitution of CH3Cl by hydroxide follows an SN2 pathway."
    retrieval_result = {
        "literature_review": "Backside attack and inversion are the decisive mechanistic signatures.",
        "mechanistic_clues": [
            "the transition state should show simultaneous C-Cl bond breaking and C-O bond formation",
            "IRC should connect the ion-pair reactants and substitution products",
        ],
    }
    hypothesis_result = {
        "ranked_strategies": [
            {
                "strategy_name": "SN2 backside-attack transition-state validation",
                "reasoning": "Compare the SN2 saddle point and verify it by one imaginary frequency and IRC connectivity.",
                "confidence": 0.81,
            }
        ]
    }
    planning_result = {
        "optimized_protocols": [
            {
                "workflow_name": "SN2 TS/IRC validation",
                "Steps": [
                    {"description": "Optimize the SN2 transition state guess"},
                    {"description": "Confirm the TS by frequency and IRC"},
                    {"description": "Compare the barrier with alternative pathways"},
                ],
            }
        ]
    }
    execution_result = {"overall_success_rate": 0.2, "results": [{"overall_status": "partial_success", "steps": []}]}

    conclusion = controller._synthesize_final_conclusion(
        scientific_question=question,
        status="completed",
        structured_record={"reflection_rounds": [{"result": {"decision": "revise_workflow"}}]},
        retrieval_result=retrieval_result,
        hypothesis_result=hypothesis_result,
        planning_result=planning_result,
        execution_result=execution_result,
        final_round=2,
        final_decision="revise_workflow",
    )

    analysis = conclusion["integrated_analysis"]
    rendered = "\n".join(
        [conclusion["conclusion_summary"]]
        + [section.get("body", "") for section in analysis["sections"]]
        + ["\n".join(section.get("items", [])) for section in analysis["sections"]]
    )

    assert "SN2" in rendered
    assert "backside" in rendered
    assert "triflic acid" not in rendered
    assert "enamine" not in rendered
    assert "beta-hydroxy ketone" not in rendered


def test_mechanism_final_analysis_reads_like_report_not_failure_log(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)
    question = (
        "I plan to verify the mechanism of an asymmetric catalytic reaction using the "
        "reaction of p-nitrobenzaldehyde O=Cc1ccc(cc1)[N+](=O)[O-] with acetone "
        "under triflic acid and chiral amine catalysis at 30 °C in acetone solvent."
    )
    retrieval_result = {
        "literature_review": (
            "Related asymmetric aldol reports point to enamine formation from acetone, "
            "acid activation of the aldehyde, and a stereodetermining C-C bond-forming "
            "transition state."
        ),
        "mechanistic_clues": [
            "acetone is activated through an enamine-like intermediate",
            "the acid co-catalyst can bind or protonate the aldehyde oxygen",
            "stereoselectivity should be determined in the C-C bond-forming transition state",
        ],
    }
    hypothesis_result = {
        "ranked_strategies": [
            {
                "strategy_name": "Explicit Co-catalyst Participation in C-C Bond Formation TS",
                "reasoning": (
                    "Compare transition states where triflic acid explicitly coordinates "
                    "to the aldehyde oxygen against non-assisted transition states."
                ),
                "confidence": 0.74,
            }
        ]
    }
    planning_result = {
        "optimized_protocols": [
            {
                "workflow_name": "TS/IRC/free-energy mechanism validation",
                "Steps": [
                    {"description": "Build enamine, aldehyde-acid complex, and stereochemical TS candidates"},
                    {"description": "Optimize acid-assisted and non-assisted C-C bond-forming transition states"},
                    {"description": "Confirm each TS by frequency analysis, IRC, and relative Gibbs barriers"},
                ],
            }
        ]
    }
    execution_result = {
        "overall_success_rate": 0.54,
        "results": [
            {
                "overall_status": "partial_success",
                "steps": [
                    {"step_name": "Build TS guesses", "status": "failed", "error": "TS pipeline missing"},
                    {"step_name": "Run Gaussian TS optimizations", "status": "failed", "error": "Gaussian failed"},
                ],
            }
        ],
    }

    conclusion = controller._synthesize_final_conclusion(
        scientific_question=question,
        status="completed",
        structured_record={"reflection_rounds": [{"result": {"decision": "revise_workflow"}}]},
        retrieval_result=retrieval_result,
        hypothesis_result=hypothesis_result,
        planning_result=planning_result,
        execution_result=execution_result,
        final_round=2,
        final_decision="revise_workflow",
    )

    analysis = conclusion["integrated_analysis"]
    section_titles = [section["title"] for section in analysis["sections"]]
    rendered_report = "\n".join(
        [conclusion["conclusion_summary"]]
        + [section.get("body", "") for section in analysis["sections"]]
        + ["\n".join(section.get("items", [])) for section in analysis["sections"]]
    )

    assert section_titles == ["结论摘要", "机理图景", "证据解释", "验证方案", "判定标准"]
    assert "enamine" in rendered_report
    assert "triflic acid" in rendered_report
    assert "C-C bond" in rendered_report
    assert "IRC" in rendered_report
    assert "Gibbs" in rendered_report
    assert "工作流成功率" not in rendered_report
    assert "partial_success" not in rendered_report
    assert "failed" not in rendered_report.lower()
    assert "失败" not in rendered_report
    assert "缺口" not in rendered_report
    assert "TS pipeline missing" not in rendered_report
