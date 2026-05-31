#!/usr/bin/env python3
"""QCM Solver — one-file, cross-platform setup.

Run this ONCE to get the desktop app ready, then use the launcher it creates.

    Windows :  double-click  setup.py   (or:  py setup.py )
    macOS   :  double-click  setup.py   (or:  python3 setup.py )
    Linux   :  python3 setup.py

What it does (the same on every OS):
  1. Checks your Python is new enough and that tkinter is available.
  2. Creates a private virtual environment (.venv) next to this file so the
     app's dependencies never touch your system Python.
  3. Installs the dependencies (mss, Pillow, requests) into that venv.
  4. Optionally stores your access token so you don't paste it every time.
  5. Writes a double-clickable launcher for your OS:
        Windows -> "Run QCM Solver.bat"
        macOS   -> "Run QCM Solver.command"
        Linux   -> "run-qcm-solver.sh"
  6. Offers to launch the app right away.

This file has no third-party dependencies of its own — it only uses the
Python standard library, so it runs anywhere Python does.
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Small console helpers (no external deps).
# --------------------------------------------------------------------------- #

HERE = Path(__file__).resolve().parent
APP = HERE / "qcm_app.py"
VENV = HERE / ".venv"
REQUIREMENTS = HERE / "requirements.txt"
ENV_FILE = HERE / ".env"
DEFAULT_ENDPOINT = "https://qcm-solver.vercel.app/api/solve"
MIN_PYTHON = (3, 8)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n: int, msg: str) -> None:
    say(f"\n[{n}/6] {msg}")


def fail(msg: str) -> None:
    say("\n  ERROR: " + msg)
    say("\nSetup did not finish. Fix the issue above and run it again.")
    _pause_if_double_clicked()
    sys.exit(1)


def _pause_if_double_clicked() -> None:
    """Keep the window open when launched by a double-click so the user can
    read the output instead of it vanishing instantly."""
    if os.environ.get("QCM_NO_PAUSE"):
        return
    try:
        input("\nPress Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

def check_python() -> None:
    step(1, "Checking Python and tkinter...")
    if sys.version_info < MIN_PYTHON:
        fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required, but this is "
            f"{sys.version.split()[0]}. Install a newer Python from "
            "https://www.python.org/downloads/ and run setup again."
        )
    try:
        import tkinter  # noqa: F401
    except Exception:
        system = platform.system()
        hint = {
            "Linux": "Install it with your package manager, e.g.\n"
            "    Debian/Ubuntu:  sudo apt install python3-tk\n"
            "    Fedora:         sudo dnf install python3-tkinter\n"
            "    Arch:           sudo pacman -S tk",
            "Darwin": "Reinstall Python from https://www.python.org/downloads/ "
            "(the official build bundles tkinter), or:  brew install python-tk",
            "Windows": "Reinstall Python from https://www.python.org/downloads/ "
            'and tick "tcl/tk and IDLE" during installation.',
        }.get(system, "Install the Tk bindings for your Python.")
        fail("tkinter is missing — the app's window toolkit.\n  " + hint)
    say(f"  OK — Python {sys.version.split()[0]} with tkinter.")


def venv_python() -> Path:
    if platform.system() == "Windows":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def create_venv() -> None:
    step(2, "Creating the private virtual environment (.venv)...")
    py = venv_python()
    if py.exists():
        say("  Already present — reusing it.")
        return
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(VENV)],
            check=True,
        )
    except subprocess.CalledProcessError:
        fail(
            "Could not create a virtual environment. On Debian/Ubuntu you may "
            "need:  sudo apt install python3-venv"
        )
    if not py.exists():
        fail("Virtual environment was created but its Python is missing.")
    say("  OK — virtual environment ready.")


def install_deps() -> None:
    step(3, "Installing dependencies (mss, Pillow, requests)...")
    py = str(venv_python())
    # Upgrade pip quietly first so wheels install cleanly, then the deps.
    subprocess.run(
        [py, "-m", "pip", "install", "--upgrade", "pip"],
        check=False,
    )
    cmd = [py, "-m", "pip", "install"]
    if REQUIREMENTS.exists():
        cmd += ["-r", str(REQUIREMENTS)]
    else:
        cmd += ["mss>=9.0.0", "Pillow>=10.0.0", "requests>=2.31.0"]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        fail("Dependency installation failed. Check your internet connection "
             "and run setup again.")
    say("  OK — dependencies installed.")


def configure_token() -> None:
    step(4, "Configuring your access token (optional)...")
    existing = ""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("QCM_ACCESS_TOKEN="):
                existing = line.split("=", 1)[1].strip()
    if existing:
        say("  A token is already saved. Leave blank to keep it.")

    say("  Paste your QCM access token so you won't have to enter it each time.")
    say("  (You can skip this and type it into the app's Token field instead.)")
    try:
        entered = input("  Access token [press Enter to skip]: ").strip()
    except (EOFError, KeyboardInterrupt):
        entered = ""

    token = entered or existing
    endpoint = DEFAULT_ENDPOINT
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("QCM_ENDPOINT="):
                endpoint = line.split("=", 1)[1].strip() or DEFAULT_ENDPOINT

    lines = [
        "# QCM Solver desktop config. Read automatically by the launcher.",
        f"QCM_ENDPOINT={endpoint}",
        f"QCM_ACCESS_TOKEN={token}",
        "",
    ]
    ENV_FILE.write_text("\n".join(lines))
    # Keep the secret readable only by the user where the OS supports it.
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass
    if token:
        say("  OK — token saved to .env (it is gitignored).")
    else:
        say("  No token saved — you can paste it into the app later.")


def write_launchers() -> Path:
    step(5, "Creating your double-click launcher...")
    system = platform.system()
    py = venv_python()

    if system == "Windows":
        launcher = HERE / "Run QCM Solver.bat"
        launcher.write_text(
            "@echo off\r\n"
            "rem QCM Solver launcher\r\n"
            'cd /d "%~dp0"\r\n'
            "if exist .env (\r\n"
            '  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do '
            'set "%%A=%%B"\r\n'
            ")\r\n"
            f'"{py}" "{APP}"\r\n'
            "if errorlevel 1 pause\r\n"
        )
        say(f"  OK — created: {launcher.name}")
        say('  Double-click "Run QCM Solver.bat" to start the app.')
        return launcher

    # macOS uses .command (double-clickable in Finder); Linux uses .sh.
    if system == "Darwin":
        launcher = HERE / "Run QCM Solver.command"
    else:
        launcher = HERE / "run-qcm-solver.sh"

    launcher.write_text(
        "#!/usr/bin/env bash\n"
        "# QCM Solver launcher\n"
        'cd "$(dirname "$0")" || exit 1\n'
        "if [ -f .env ]; then\n"
        "  set -a\n"
        "  # shellcheck disable=SC1091\n"
        "  . ./.env\n"
        "  set +a\n"
        "fi\n"
        f'exec "{py}" "{APP}"\n'
    )
    os.chmod(launcher, 0o755)
    say(f"  OK — created: {launcher.name}")
    if system == "Darwin":
        say('  Double-click "Run QCM Solver.command" in Finder to start.')
        say("  (First time: right-click -> Open to clear the Gatekeeper prompt.)")
    else:
        say(f"  Start it with:  ./{launcher.name}   (or double-click it)")
    return launcher


def maybe_launch(launcher: Path) -> None:
    step(6, "All set!")
    try:
        ans = input("  Launch QCM Solver now? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    if ans in ("", "y", "yes"):
        say("  Starting...")
        env = os.environ.copy()
        # Load .env values for this immediate launch too.
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text().splitlines():
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        subprocess.Popen([str(venv_python()), str(APP)], env=env)
        say("  Launched. You can close this window.")
    else:
        say(f"  When you're ready, run: {launcher.name}")


def main() -> None:
    say("=" * 60)
    say("  QCM Solver — desktop setup")
    say("=" * 60)
    if not APP.exists():
        fail(f"Could not find the app at {APP}. Keep setup.py next to qcm_app.py.")

    check_python()
    create_venv()
    install_deps()
    configure_token()
    launcher = write_launchers()
    maybe_launch(launcher)

    say("\nDone. Setup only needs to be run once per machine.")
    _pause_if_double_clicked()


if __name__ == "__main__":
    main()
