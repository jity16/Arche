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
    assert [section["title"] for section in analysis["sections"][:3]] == ["综合判断", "结论刻度", "证据来源"]


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


def test_non_mechanism_final_analysis_uses_scaled_judgment_and_structured_evidence_sources(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)
    question = "Predict the IR spectrum and frontier orbital gap of formaldehyde at B3LYP/6-31G* and summarize the conclusion."
    retrieval_result = {
        "literature_review": (
            "Benchmark studies place the carbonyl stretching band near 1700 cm^-1 and note that "
            "frontier orbital gaps are sensitive to the chosen functional and basis set."
        ),
        "mechanistic_clues": [
            "reported IR assignments focus on the carbonyl stretching region",
            "frontier orbital gaps should be compared as method-dependent descriptors rather than standalone proof",
        ],
        "limitations": ["Published peak positions depend on basis set choice and anharmonic correction."],
        "retrieved_papers": [
            "Benchmark study on formaldehyde vibrational assignments",
            "DFT frontier-orbital analysis of small carbonyl compounds",
        ],
    }
    hypothesis_result = {
        "ranked_strategies": [
            {
                "strategy_name": "B3LYP/6-31G* geometry-frequency benchmark",
                "reasoning": "Optimize the structure, compute IR peaks and orbital gap, then compare against benchmark literature.",
                "confidence": 0.82,
            }
        ]
    }
    planning_result = {
        "optimized_protocols": [
            {
                "workflow_name": "Geometry/frequency/property benchmark",
                "Steps": [
                    {"description": "Optimize the formaldehyde geometry"},
                    {"description": "Run frequency analysis and assign the strongest IR peaks"},
                    {"description": "Extract frontier orbital gap and compare with literature values"},
                ],
            }
        ]
    }
    execution_result = {
        "overall_success_rate": 0.88,
        "results": [
            {
                "workflow_outcome": "supported",
                "overall_status": "success",
                "steps": [
                    {"step_name": "Optimize the formaldehyde geometry", "status": "success"},
                    {
                        "step_name": "Run frequency analysis and orbital inspection",
                        "status": "success",
                        "raw_output": {
                            "execution_mode": "gaussian_job",
                            "status": "completed",
                            "deterministic_provenance": {
                                "mol_label": "formaldehyde",
                                "geometry_source": "question_context",
                                "smiles": "C=O",
                                "atom_count": 4,
                            },
                            "parsed_results": {
                                "job_type": "freq",
                                "normal_termination": True,
                                "scf_energy": -113.86138,
                                "E_HOMO": -0.41512,
                                "E_LUMO": 0.02141,
                                "HOMO_LUMO_gap": 0.43653,
                            },
                            "spectroscopy": {
                                "ir_peaks": [
                                    {"freq_cm1": 1748.2, "intensity_km_mol": 101.3},
                                    {"freq_cm1": 1502.4, "intensity_km_mol": 22.5},
                                ]
                            },
                        },
                    },
                ],
            }
        ],
    }
    structured_record = {
        "execution_rounds": [{"round": 1, "status": "success"}],
        "reflection_rounds": [
            {
                "round": 1,
                "result": {
                    "decision": "accept",
                    "reasoning": "The property calculation matches the question and yielded reportable scalar observables.",
                    "recommended_actions": ["compare peak positions with benchmark tables"],
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
        final_decision="accept",
    )

    analysis = conclusion["integrated_analysis"]
    assert analysis["judgment_scale"]["level"] == "L3"
    assert analysis["judgment_scale"]["label"] == "可报告的初步计算结论"

    section_titles = [section["title"] for section in analysis["sections"]]
    assert section_titles[:3] == ["综合判断", "结论刻度", "证据来源"]
    assert "L3（可报告的初步计算结论）" in analysis["overall_judgment"]
    assert "补齐跨方法/文献对照" in analysis["overall_judgment"]

    scale_section = analysis["sections"][1]
    assert any("当前级别：L3" in item for item in scale_section["items"])
    assert any("可引用性：" in item for item in scale_section["items"])
    assert any("升级到更高结论等级" in item for item in scale_section["items"])

    evidence_section = analysis["sections"][2]
    assert any(item.startswith("文献检索：") for item in evidence_section["items"])
    assert any(item.startswith("计算执行：") for item in evidence_section["items"])
    assert any("Benchmark study on formaldehyde vibrational assignments" in item for item in evidence_section["items"])
    assert any("accept" in item for item in evidence_section["items"])


def test_evidence_source_strips_literature_review_prompt_echo(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)

    dirty_review = (
        "好的，作为一名经验丰富的计算化学家，我将根据您提供的文献节选，"
        "撰写一份专注于计算化学方法的结构化文献综述。 --- "
        "### B3LYP/6-31G* 几何优化基准\n"
        "B3LYP/6-31G* 常用于中小分子的几何优化与频率分析，"
        "可为键长、键角和主要振动峰提供可比较的基准结果。"
    )

    conclusion = controller._synthesize_final_conclusion(
        scientific_question="预测甲醛在 B3LYP/6-31G* 下的几何优化和红外峰位。",
        status="completed",
        structured_record={"execution_rounds": [{"round": 1, "status": "success"}]},
        retrieval_result={"literature_review": dirty_review},
        hypothesis_result={"ranked_strategies": [{"strategy_name": "B3LYP/6-31G* 几何与频率计算"}]},
        planning_result={},
        execution_result={"results": []},
        final_round=2,
        final_decision="accept",
    )

    evidence = conclusion["integrated_analysis"]["evidence_from_retrieval"]
    rendered = "\n".join(
        [conclusion["conclusion_summary"]]
        + [section.get("body", "") for section in conclusion["integrated_analysis"]["sections"]]
        + ["\n".join(section.get("items", [])) for section in conclusion["integrated_analysis"]["sections"]]
    )

    assert "作为一名经验丰富的计算化学家" not in evidence
    assert "根据您提供的文献节选" not in evidence
    assert "结构化文献综述" not in evidence
    assert "作为一名经验丰富的计算化学家" not in rendered
    assert "B3LYP/6-31G*" in evidence
    assert "几何优化" in evidence


def test_supported_final_conclusion_does_not_treat_historical_revisions_as_open_issue(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)

    conclusion = controller._synthesize_final_conclusion(
        scientific_question="Calculate the HOMO-LUMO gap of benzene at B3LYP/6-31G*.",
        status="accepted",
        structured_record={
            "execution_rounds": [{"round": 1, "status": "success"}],
            "revision_events": [{"round": 1, "status": "success", "type": "workflow_revision"}],
            "reflection_rounds": [{"round": 2, "result": {"decision": "accept"}}],
        },
        retrieval_result={
            "literature_review": "Benchmark discussions note the orbital gap depends on method choice.",
            "retrieved_papers": ["Benzene frontier-orbital benchmark"],
        },
        hypothesis_result={
            "ranked_strategies": [
                {
                    "strategy_name": "Benzene orbital-gap benchmark",
                    "reasoning": "Optimize benzene and extract the frontier orbital energies.",
                    "confidence": 0.84,
                }
            ]
        },
        planning_result={
            "optimized_protocols": [
                {
                    "workflow_name": "Benzene orbital-gap benchmark",
                    "Steps": [
                        {"description": "Optimize the benzene geometry"},
                        {"description": "Extract HOMO and LUMO energies from the completed job"},
                    ],
                }
            ]
        },
        execution_result={
            "overall_success_rate": 1.0,
            "results": [
                {
                    "workflow_outcome": "supported",
                    "overall_status": "success",
                    "steps": [
                        {"step_name": "Optimize the benzene geometry", "status": "success"},
                        {
                            "step_name": "Extract HOMO and LUMO energies from the completed job",
                            "status": "success",
                            "raw_output": {
                                "execution_mode": "gaussian_job",
                                "status": "completed",
                                "parsed_results": {
                                    "job_type": "sp",
                                    "normal_termination": True,
                                    "scf_energy": -232.24000229230253,
                                    "E_HOMO": -0.2455808035306677,
                                    "E_LUMO": 0.004190831193789268,
                                    "HOMO_LUMO_gap": 0.24977163472445696,
                                },
                            },
                        },
                    ],
                }
            ],
        },
        final_round=3,
        final_decision="accept",
    )

    assert conclusion["conclusion_type"] == "supported"
    assert conclusion["unresolved_issues"] == []
    assert conclusion["integrated_analysis"]["validation_gaps"] == []
    assert "工作流执行期间发生过修订" not in conclusion["conclusion_summary"]
    assert "工作流执行期间发生过修订" not in conclusion["integrated_analysis"]["validation_gaps"]


def test_reaction_enthalpy_final_conclusion_surfaces_real_delta_h(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)

    conclusion = controller._synthesize_final_conclusion(
        scientific_question=r"求反应 $\ce{N2 + 3H2 <=> 2NH3}$ 的反应焓 $\Delta H_{rxn}$",
        status="accepted",
        structured_record={
            "execution_rounds": [{"round": 1, "status": "success"}],
            "reflection_rounds": [{"round": 1, "result": {"decision": "accept"}}],
        },
        retrieval_result={"literature_review": "Ammonia synthesis thermochemistry benchmark."},
        hypothesis_result={"ranked_strategies": [{"strategy_name": "Haber thermochemistry benchmark"}]},
        planning_result={},
        execution_result={
            "overall_success_rate": 1.0,
            "results": [
                {
                    "workflow_outcome": "supported",
                    "overall_status": "success",
                    "steps": [
                        {
                            "step_name": "Compute ΔH_rxn from parsed thermochemistry",
                            "tool_name": "compute_reaction_thermochemistry",
                            "status": "success",
                            "raw_output": {
                                "execution_mode": "real_tool",
                                "tool_name": "compute_reaction_thermochemistry",
                                "raw_result": {
                                    "success": True,
                                    "delta_h_rxn_hartree": -0.01875,
                                    "delta_h_rxn_kj_mol": -49.22,
                                },
                            },
                        }
                    ],
                }
            ],
        },
        final_round=2,
        final_decision="accept",
    )

    summary = conclusion["conclusion_summary"]
    findings = conclusion["key_findings"]
    assert "-49.22" in summary or "-49.2200" in summary
    assert any(item.get("reaction_enthalpy_kj_mol") == -49.22 for item in findings if item.get("type") == "computed_results")


def test_ir_question_prefers_frequencies_over_homo_lumo_when_only_freqs_available(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)

    conclusion = controller._synthesize_final_conclusion(
        scientific_question=r"分析 $\ce{CO2}$ 的振动光谱（IR）吸收峰归属",
        status="accepted",
        structured_record={
            "execution_rounds": [{"round": 1, "status": "success"}],
            "reflection_rounds": [{"round": 1, "result": {"decision": "accept"}}],
        },
        retrieval_result={"literature_review": "CO2 infrared benchmark."},
        hypothesis_result={"ranked_strategies": [{"strategy_name": "CO2 IR benchmark"}]},
        planning_result={},
        execution_result={
            "overall_success_rate": 1.0,
            "results": [
                {
                    "workflow_outcome": "supported",
                    "overall_status": "success",
                    "steps": [
                        {
                            "step_name": "Run the Gaussian geometry-optimization and frequency job for CO2.",
                            "tool_name": "generate_gaussian_code",
                            "status": "success",
                            "raw_output": {
                                "execution_mode": "gaussian_job",
                                "status": "completed",
                                "parsed_results": {
                                    "job_type": "freq",
                                    "normal_termination": True,
                                    "HOMO_LUMO_gap": 0.3662503201791132,
                                    "E_HOMO": -0.38599015937896364,
                                    "E_LUMO": -0.01973983919985045,
                                    "frequencies": [667.19, 668.14, 1373.71, 2417.11],
                                    "n_imag_freq": 0,
                                },
                            },
                        }
                    ],
                }
            ],
        },
        final_round=2,
        final_decision="accept",
    )

    summary = conclusion["conclusion_summary"]
    findings = conclusion["key_findings"]
    assert "IR" in summary or "cm⁻¹" in summary
    assert "HOMO-LUMO" not in summary
    assert any(item.get("ir_peaks_cm1") for item in findings if item.get("type") == "computed_results")
    assert not any("homo_lumo_gap_ev" in item for item in findings if item.get("type") == "computed_results")


def test_geometry_question_prefers_bond_metrics_over_homo_lumo(tmp_path):
    from chemistry_multiagent.controllers.chemistry_multiagent_controller import ChemistryMultiAgentController

    controller = ChemistryMultiAgentController.__new__(ChemistryMultiAgentController)
    controller.workflow_state = {"expert_backend_audit_summary": {}}
    controller.expert_backend = "openai_compatible"
    controller.work_dir = str(tmp_path)

    conclusion = controller._synthesize_final_conclusion(
        scientific_question=r"预测 $\ce{H2O}$ 在 $\text{B3LYP/6-31G}^*$ 下的优化几何构型",
        status="accepted",
        structured_record={
            "execution_rounds": [{"round": 1, "status": "success"}],
            "reflection_rounds": [{"round": 1, "result": {"decision": "accept"}}],
        },
        retrieval_result={"literature_review": "Water geometry benchmark."},
        hypothesis_result={"ranked_strategies": [{"strategy_name": "Water geometry benchmark"}]},
        planning_result={},
        execution_result={
            "overall_success_rate": 1.0,
            "results": [
                {
                    "workflow_outcome": "supported",
                    "overall_status": "success",
                    "steps": [
                        {
                            "step_name": "Run the Gaussian geometry optimization job.",
                            "tool_name": "generate_gaussian_code",
                            "status": "success",
                            "raw_output": {
                                "execution_mode": "gaussian_job",
                                "status": "completed",
                                "parsed_results": {
                                    "job_type": "opt",
                                    "normal_termination": True,
                                    "converged": True,
                                    "HOMO_LUMO_gap": 0.35545356949533424,
                                    "E_HOMO": -0.29019463101215903,
                                    "E_LUMO": 0.06525893848317521,
                                    "elements": ["O", "H", "H"],
                                    "coordinates": [[
                                        [-0.0017697761603869566, 0.39796458842498533, 0.0],
                                        [-0.7616744972158372, -0.20277830686379394, 0.0],
                                        [0.7635456162900025, -0.19584783087625654, 0.0],
                                    ]],
                                },
                            },
                        }
                    ],
                }
            ],
        },
        final_round=2,
        final_decision="accept",
    )

    summary = conclusion["conclusion_summary"]
    findings = conclusion["key_findings"]
    assert "键长" in summary or "键角" in summary
    assert "HOMO-LUMO" not in summary
    computed_items = [item for item in findings if item.get("type") == "computed_results"]
    assert any(item.get("bond_lengths_angstrom") for item in computed_items)
    assert any("bond_angle_deg" in item for item in computed_items)
    assert not any("homo_lumo_gap_ev" in item for item in computed_items)


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
