from pathlib import Path
import os
import shlex
import shutil
import sys


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
TESTS_DIR = PROJECT_ROOT / "tests"
TEST_INPUTS_DIR = TESTS_DIR / "inputs"
TEST_TEMP_DIR = TESTS_DIR / "temp"
TEST_REPORTS_DIR = TESTS_DIR / "reports"
TEST_OUTPUTS_DIR = TESTS_DIR / "outputs"
TOOLPOOL_PATH = PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "toolpool.json"


def add_src_to_path() -> None:
    src = str(SRC_DIR)
    if src not in sys.path:
        sys.path.insert(0, src)


def require_gaussian_demo_enabled(label: str = "Gaussian demo") -> bool:
    if os.environ.get("ARCHE_RUN_GAUSSIAN_TESTS") != "1":
        print(
            f"[SKIP] {label} requires real Gaussian execution. "
            "Set ARCHE_RUN_GAUSSIAN_TESTS=1 and GAUSSIAN_COMMAND=g16 to run it."
        )
        return False

    command = os.environ.get("GAUSSIAN_COMMAND", "g16")
    try:
        executable = shlex.split(command)[0]
    except Exception:
        executable = command.strip()

    if not executable:
        print("[SKIP] GAUSSIAN_COMMAND is empty.")
        return False

    command_available = shutil.which(executable) is not None or Path(executable).expanduser().exists()
    if not command_available and not (
        os.environ.get("GAUSSIAN_MODULE_LOAD") or os.environ.get("GAUSSIAN_ENV_HOOK")
    ):
        print(
            f"[SKIP] GAUSSIAN_COMMAND={command!r} is not available in PATH. "
            "Set GAUSSIAN_COMMAND or provide GAUSSIAN_MODULE_LOAD/GAUSSIAN_ENV_HOOK."
        )
        return False

    if not command_available:
        print(
            f"[WARN] {command!r} is not currently in PATH, but Gaussian env hook/module "
            "configuration is set; continuing."
        )
    return True

