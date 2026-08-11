"""Install the AG Crypto Analyst Forward Shadow cycle as a macOS LaunchAgent.

The LaunchAgent runs the live cycle every 5 minutes while the user is logged in.
It uses the current Python interpreter and current project directory, writes logs
under reports/, and does not modify the locked model parameters.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.agca.forward-shadow"
INTERVAL_SECONDS = 300


def main():
    project_dir = Path.cwd().resolve()
    if not (project_dir / "analysis" / "forward_shadow_cycle.py").exists():
        raise SystemExit("HATA: Bu komutu AG-Crypto-Analyst proje kök klasöründe çalıştır.")

    python_exe = Path(sys.executable).resolve()
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{LABEL}.plist"

    reports = project_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stdout_path = reports / "forward_shadow_launchd.log"
    stderr_path = reports / "forward_shadow_launchd_error.log"

    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(python_exe),
            "-m",
            "analysis.forward_shadow_cycle",
        ],
        "WorkingDirectory": str(project_dir),
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
        },
    }

    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)

    uid = os.getuid()
    domain = f"gui/{uid}"
    # Remove a previous loaded copy if present. Failure is harmless on first install.
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)], check=False, capture_output=True)
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip())
        raise SystemExit("HATA: LaunchAgent yüklenemedi.")

    subprocess.run(["launchctl", "enable", f"{domain}/{LABEL}"], check=False)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=False)

    print("AG Crypto Analyst - Forward Shadow LaunchAgent kuruldu")
    print(f"Label          : {LABEL}")
    print(f"Çalışma aralığı: {INTERVAL_SECONDS // 60} dakika")
    print(f"Python         : {python_exe}")
    print(f"Proje          : {project_dir}")
    print(f"Log            : {stdout_path}")
    print(f"Hata logu      : {stderr_path}")
    print("Durum          : AKTİF")


if __name__ == "__main__":
    main()
