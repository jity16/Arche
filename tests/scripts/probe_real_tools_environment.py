import os
import sys
import json
import shutil
import importlib
from pprint import pprint

from path_utils import PROJECT_ROOT, SRC_DIR, TOOLPOOL_PATH, add_src_to_path

add_src_to_path()

TOOLPOOL_PATH = str(TOOLPOOL_PATH)

def safe_import(module_name: str):
    try:
        importlib.import_module(module_name)
        return True, None
    except Exception as e:
        return False, str(e)

def check_python_file_exists(path_str: str):
    if not path_str:
        return False, "empty path"
    abs_path = path_str
    if not os.path.isabs(abs_path):
        abs_path = os.path.join(str(PROJECT_ROOT), path_str)
    return os.path.exists(abs_path), abs_path

def check_command_exists(cmd: str):
    found = shutil.which(cmd)
    return found is not None, found

def main():
    print("========== Real Tools Environment Probe ==========")
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"SRC_DIR:      {SRC_DIR}")
    print(f"TOOLPOOL:     {TOOLPOOL_PATH}")
    print()

    if not os.path.exists(TOOLPOOL_PATH):
        print("ERROR: toolpool.json not found.")
        sys.exit(1)

    with open(TOOLPOOL_PATH, "r", encoding="utf-8") as f:
        toolpool = json.load(f)

    # ---- basic python dependency probe ----
    print("---- Python dependency probe ----")
    modules_to_check = [
        "requests",
        "openai",
        "numpy",
        "rdkit",
        "openbabel",
        "ase",
        "cclib",
    ]
    dep_results = {}
    for mod in modules_to_check:
        ok, err = safe_import(mod)
        dep_results[mod] = {"ok": ok, "error": err}
        print(f"{mod:12s} -> {'OK' if ok else 'MISSING'}")
        if err and not ok:
            print(f"   reason: {err}")
    print()

    # ---- command probe ----
    print("---- Command probe ----")
    commands_to_check = [
        "python",
        "python3",
        "obabel",
        "babel",
        "g16",
        "formchk",
        "cubegen",
    ]
    cmd_results = {}
    for cmd in commands_to_check:
        ok, path = check_command_exists(cmd)
        cmd_results[cmd] = {"ok": ok, "path": path}
        print(f"{cmd:12s} -> {'FOUND' if ok else 'NOT FOUND'} {path if path else ''}")
    print()

    # ---- toolpool probe ----
    print("---- Toolpool probe ----")
    tools = toolpool.get("tools", toolpool) if isinstance(toolpool, dict) else toolpool
    if not isinstance(tools, list):
        print("WARNING: toolpool format is not a list under 'tools'; probing best-effort only.")
        tools = []

    summary = {
        "total_tools": len(tools),
        "python_paths_checked": [],
        "commands_checked": [],
        "suspicious_entries": [],
    }

    for i, tool in enumerate(tools, start=1):
        name = tool.get("tool_name") or tool.get("name") or f"tool_{i}"
        tool_path = tool.get("tool_path") or tool.get("path") or ""
        description = tool.get("description", "")

        print(f"[{i}] {name}")
        print(f"    tool_path: {tool_path}")
        if description:
            print(f"    desc: {description[:120]}")

        # Heuristic classification
        if tool_path.endswith(".py"):
            ok, info = check_python_file_exists(tool_path)
            print(f"    python file exists: {ok} ({info})")
            summary["python_paths_checked"].append(
                {"tool": name, "path": tool_path, "exists": ok, "resolved": info}
            )
        elif "." in tool_path and not tool_path.startswith("/"):
            # could be import path like module.submodule.func
            mod_name = tool_path.split(":")[0].split("(")[0]
            ok, err = safe_import(mod_name.split(".")[0])
            print(f"    importable module root: {ok} ({mod_name.split('.')[0]})")
            if not ok:
                print(f"    import error: {err}")
                summary["suspicious_entries"].append(
                    {"tool": name, "tool_path": tool_path, "problem": err}
                )
        elif tool_path:
            cmd = tool_path.split()[0]
            ok, found = check_command_exists(cmd)
            print(f"    command exists: {ok} ({found})")
            summary["commands_checked"].append(
                {"tool": name, "command": cmd, "found": found, "ok": ok}
            )
        else:
            print("    WARNING: empty tool_path")
            summary["suspicious_entries"].append(
                {"tool": name, "tool_path": tool_path, "problem": "empty tool_path"}
            )

        print()

    print("---- Summary ----")
    pprint(summary)

    print("\nInterpretation:")
    print("- If rdkit/openbabel/ase/cclib are missing, many chemistry utilities cannot run for real.")
    print("- If obabel/babel is missing, format-conversion tools may fail in real execution.")
    print("- If most tool_path entries are not real files, import paths, or shell commands, Execution cannot yet do true tool calls.")
    print("- If g16 is missing, Gaussian must remain replay-only on this machine.")
    print("=================================================")


if __name__ == "__main__":
    main()
