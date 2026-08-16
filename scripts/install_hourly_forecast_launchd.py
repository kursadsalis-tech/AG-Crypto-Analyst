"""Install the hourly AGCA forecast job as a macOS LaunchAgent.

The job runs at minutes 02, 07 and 12 of every hour while the user is logged
in. Later attempts are idempotent retries when exchange data is delayed.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path


LABEL = "com.agca.hourly-forecast"
RUN_MINUTES = (2, 7, 12)


def main():
    project_dir = Path.cwd().resolve()
    required = (
        project_dir / "decision_engine" / "hourly_forecast_job.py",
        project_dir / "decision_engine" / "forecast_engine.py",
    )
    if not all(path.exists() for path in required):
        raise SystemExit("HATA: Bu komutu güncel AG-Crypto-Analyst proje kökünde çalıştır.")

    python_exe = Path(sys.executable).resolve()
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{LABEL}.plist"
    reports = project_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(python_exe),
            "-m",
            "decision_engine.hourly_forecast_job",
        ],
        "WorkingDirectory": str(project_dir),
        "RunAtLoad": False,
        "StartCalendarInterval": [
            {"Minute": minute} for minute in RUN_MINUTES
        ],
        "StandardOutPath": str(reports / "hourly_forecast_launchd.log"),
        "StandardErrorPath": str(reports / "hourly_forecast_launchd_error.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "ProcessType": "Background",
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)

    uid = os.getuid()
    domain = f"gui/{uid}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        check=False,
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip())
        raise SystemExit("HATA: Hourly Forecast LaunchAgent yüklenemedi.")
    subprocess.run(
        ["launchctl", "enable", f"{domain}/{LABEL}"],
        check=False,
        capture_output=True,
    )

    print("AG Crypto Analyst - Hourly Forecast LaunchAgent kuruldu")
    print(f"Label    : {LABEL}")
    print("Dakikalar: " + ", ".join(f":{minute:02d}" for minute in RUN_MINUTES))
    print(f"Python   : {python_exe}")
    print(f"Proje    : {project_dir}")
    print(f"Log      : {reports / 'hourly_forecast_launchd.log'}")
    print(f"Hata logu: {reports / 'hourly_forecast_launchd_error.log'}")
    print("Durum    : AKTİF")


if __name__ == "__main__":
    main()
