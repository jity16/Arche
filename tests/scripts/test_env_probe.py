import os
import sys
import shutil
import subprocess
import json

from path_utils import PROJECT_ROOT, SRC_DIR, add_src_to_path

add_src_to_path()

def run_cmd(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
        }
    except Exception as e:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(e),
        }

def main():
    report = {
        "python": sys.executable,
        "project_root": str(PROJECT_ROOT),
        "src_dir": str(SRC_DIR),
        "PYTHONPATH": os.environ.get("PYTHONPATH"),
        "ARCHE_PROJECT_ROOT": os.environ.get("ARCHE_PROJECT_ROOT"),
        "DEEPSEEK_API_KEY_set": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "ARCHE_CHEM_MODEL_PATH_set": bool(os.environ.get("ARCHE_CHEM_MODEL_PATH")),
        "GAUSSIAN_EXECUTION_MODE": os.environ.get("GAUSSIAN_EXECUTION_MODE"),
        "GAUSSIAN_COMMAND": os.environ.get("GAUSSIAN_COMMAND"),
        "commands": {}
    }

    commands = ["g16", "formchk", "cubegen", "sbatch", "squeue", "sacct"]
    for cmd in commands:
        path = shutil.which(cmd)
        report["commands"][cmd] = {
            "found": bool(path),
            "path": path
        }

    report["g16_probe"] = run_cmd(["bash", "-lc", "which g16 && echo 'g16 available'"])
    report["pwd"] = os.getcwd()

    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
