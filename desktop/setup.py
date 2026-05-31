#!/usr/bin/env python3
"""QCM Solver — one-file, cross-platform setup.

Run this ONCE to get the desktop app ready, then use the launcher it creates.

    Windows :  double-click  setup.py   (or:  py setup.py )
    macOS   :  double-click  setup.py   (or:  python3 setup.py )
    Linux   :  python3 setup.py

What it does (the same on every OS):
  1. Finds the newest Python on the machine that has a modern Tk (8.6+),
     ignoring old/deprecated ones like the macOS system Python 3.9 (Tk 8.5).
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

from __future__ import annotations

import json
import os
import platform
import shutil
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

# A venv inherits the Python that builds it, so we must pick a GOOD base
# interpreter — recent enough and with a modern Tk. The macOS system Python 3.9
# ships the deprecated Tk 8.5, which is buggy/unsupported, so we require Tk 8.6+.
MIN_PYTHON = (3, 10)
MIN_TK = 8.6

# Interpreter names to look for, newest first. The newest acceptable one wins.
CANDIDATE_NAMES = [
    "python3.14",
    "python3.13",
    "python3.12",
    "python3.11",
    "python3.10",
    "python3",
    "python",
]

# Tiny program we run inside each candidate to read its version + Tk version
# without needing a display (TkVersion is a module constant set at import).
_PROBE = (
    "import sys, json\n"
    "info = {'ver': list(sys.version_info[:3])}\n"
    "try:\n"
    "    import tkinter\n"
    "    info['tk'] = float(tkinter.TkVersion)\n"
    "except Exception as e:\n"
    "    info['tk'] = None\n"
    "    info['tk_err'] = str(e)\n"
    "print(json.dumps(info))\n"
)


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

def probe(interpreter: str) -> dict | None:
    """Return {'ver': [maj, min, mic], 'tk': float|None} for an interpreter,
    or None if it can't be run."""
    try:
        out = subprocess.run(
            [interpreter, "-c", _PROBE],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def is_acceptable(info: dict | None) -> bool:
    """A Python is acceptable if it's recent enough AND has a modern Tk."""
    if not info:
        return False
    if tuple(info.get("ver", ())) < MIN_PYTHON:
        return False
    tk = info.get("tk")
    return tk is not None and tk >= MIN_TK


def _no_python_hint() -> str:
    return {
        "Darwin": "Install a modern Python that bundles Tk 8.6+:\n"
        "    brew install python-tk        (Homebrew)\n"
        "    or the installer from https://www.python.org/downloads/\n"
        "  The macOS system Python 3.9 ships the deprecated Tk 8.5 and is not "
        "supported.",
        "Linux": "Install Python 3.10+ and its Tk bindings, e.g.\n"
        "    Debian/Ubuntu:  sudo apt install python3 python3-venv python3-tk\n"
        "    Fedora:         sudo dnf install python3 python3-tkinter\n"
        "    Arch:           sudo pacman -S python tk",
        "Windows": "Install Python 3.10+ from https://www.python.org/downloads/ "
        'and tick "tcl/tk and IDLE" during installation.',
    }.get(platform.system(), "Install Python 3.10+ with Tk 8.6+ bindings.")


def select_base_python() -> str:
    """Find the newest interpreter on this machine that is recent enough and
    has a modern Tk. This becomes the base the venv is built from — so we never
    inherit an old/deprecated Tk just because setup.py was launched with it."""
    step(1, "Finding a suitable Python (recent version + modern Tk)...")

    # De-duplicate by real path, but keep the friendly name order.
    seen: set[str] = set()
    candidates: list[str] = []
    for name in CANDIDATE_NAMES:
        loc = shutil.which(name)
        if loc and os.path.realpath(loc) not in seen:
            seen.add(os.path.realpath(loc))
            candidates.append(loc)
    if os.path.realpath(sys.executable) not in seen:
        candidates.append(sys.executable)

    best: str | None = None
    best_ver: tuple = ()
    rejected: list[tuple[str, dict]] = []
    for loc in candidates:
        info = probe(loc)
        if is_acceptable(info):
            ver = tuple(info["ver"])  # type: ignore[index]
            if ver > best_ver:
                best, best_ver = loc, ver
        elif info:
            rejected.append((loc, info))

    if best:
        say(f"  OK — using {best}  (Python {'.'.join(map(str, best_ver))}, "
            f"Tk {probe(best)['tk']}).")  # type: ignore[index]
        return best

    if rejected:
        say("  Found Python, but none is recent enough with a usable Tk "
            f"{MIN_TK}+:")
        for loc, info in rejected:
            ver = ".".join(map(str, info.get("ver", ())))
            tk = info.get("tk")
            reason = f"Tk {tk}" if tk else "no tkinter"
            say(f"    - {loc}  (Python {ver}, {reason})")
    fail(f"No suitable Python found (need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ "
         f"with Tk {MIN_TK}+).\n  " + _no_python_hint())


def venv_python() -> Path:
    if platform.system() == "Windows":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def create_venv(base_python: str) -> None:
    step(2, "Creating the private virtual environment (.venv)...")
    py = venv_python()
    if py.exists():
        info = probe(str(py))
        if is_acceptable(info):
            say(f"  Reusing existing .venv (Python "
                f"{'.'.join(map(str, info['ver']))}).")  # type: ignore[index]
            return
        say("  Existing .venv uses an old/unsuitable Python — rebuilding it.")
        shutil.rmtree(VENV, ignore_errors=True)
    try:
        subprocess.run([base_python, "-m", "venv", str(VENV)], check=True)
    except subprocess.CalledProcessError:
        fail(
            "Could not create a virtual environment. On Debian/Ubuntu you may "
            "need:  sudo apt install python3-venv"
        )
    if not py.exists():
        fail("Virtual environment was created but its Python is missing.")
    info = probe(str(py))
    if not is_acceptable(info):
        tk = (info or {}).get("tk")
        fail(f"The new .venv still lacks a modern Tk (got Tk {tk}). Install the "
             "Tk bindings for the chosen Python and run setup again.")
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

    base_python = select_base_python()
    create_venv(base_python)
    install_deps()
    configure_token()
    launcher = write_launchers()
    maybe_launch(launcher)

    say("\nDone. Setup only needs to be run once per machine.")
    _pause_if_double_clicked()


if __name__ == "__main__":
    main()
