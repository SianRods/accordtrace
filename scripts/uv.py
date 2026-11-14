"""Project-local uv launcher: python scripts/uv.py sync (or any uv arguments)."""

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main():
    executable = shutil.which("uv")
    if executable is None:
        tool_directory = PROJECT_ROOT / ".tools" / "uv"
        binaries = tool_directory / ("Scripts" if os.name == "nt" else "bin")
        tool_python = binaries / ("python.exe" if os.name == "nt" else "python")
        executable = str(binaries / ("uv.exe" if os.name == "nt" else "uv"))
        if not Path(executable).is_file():
            print("Installing uv into .tools/uv (requires network access)...", flush=True)
            if not tool_python.is_file():
                venv.create(tool_directory, with_pip=True)
            subprocess.run(
                [str(tool_python), "-m", "pip", "install",
                 "--disable-pip-version-check", "--retries", "0", "--timeout", "15", "uv"],
                check=True,
                cwd=PROJECT_ROOT,
            )

    subprocess.run(
        [executable, *(sys.argv[1:] or ["sync"])],
        check=True,
        cwd=PROJECT_ROOT,
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(
            "Setup/uv command failed. If installation or downloading failed, "
            "check network/package-index access and rerun the same command.",
            file=sys.stderr,
        )
        sys.exit(error.returncode)
