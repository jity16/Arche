from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "src" / "chemistry_multiagent" / "controllers" / "chemistry_multiagent_controller.py"


def test_final_conclusion_summary_uses_chinese_report_copy_not_english_templates():
    source = CONTROLLER.read_text(encoding="utf-8")

    assert "_format_final_conclusion_summary" in source
    assert "Preliminary results for" not in source
    assert "require further validation" not in source
    assert "真实 Gaussian 计算结果:" not in source
    assert "本次计算得到" in source
    assert "仍需复核" in source
