"""Install AGCA's provider-neutral Ubuntu systemd runtime.

Run only on the target Ubuntu VM from the project virtual environment:

    sudo /opt/agca/venv/bin/python -m scripts.install_ubuntu_cloud_runtime \
        --project-dir /opt/agca/app --service-user ubuntu

The installer never copies credentials and never enables retrospective
catch-up.  Secrets belong in /etc/agca/agca.env with mode 0600.
"""
from __future__ import annotations

import argparse
import grp
import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path


UNIT_DIR = Path("/etc/systemd/system")
ENV_DIR = Path("/etc/agca")
ENV_FILE = ENV_DIR / "agca.env"
LOCK_FILE = Path("/var/lock/agca-runtime.lock")


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def service_text(
    description: str,
    module: str,
    project_dir: Path,
    python_exe: Path,
    service_user: str,
    service_group: str,
) -> str:
    return f"""[Unit]
Description={description}
Wants=network-online.target
After=network-online.target
ConditionPathExists={project_dir / "crypto_data.db"}

[Service]
Type=oneshot
User={service_user}
Group={service_group}
WorkingDirectory={_quoted(str(project_dir))}
EnvironmentFile=-{ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=AGCA_CLOUD_RUNTIME=1
ExecStart={_quoted(str(python_exe))} -m scripts.cloud_job_runner {module}
TimeoutStartSec=2700
UMask=0077
Nice=5
"""


def timer_text(description: str, service: str, calendar: str) -> str:
    return f"""[Unit]
Description={description}

[Timer]
OnCalendar={calendar}
AccuracySec=15s
RandomizedDelaySec=0
Persistent=false
Unit={service}

[Install]
WantedBy=timers.target
"""


def payloads(
    project_dir: Path,
    python_exe: Path,
    service_user: str,
    service_group: str | None = None,
) -> dict[str, str]:
    service_group = service_group or service_user
    return {
        "agca-live-cycle.service": service_text(
            "AGCA five-minute live product cycle",
            "analysis.forward_shadow_cycle",
            project_dir,
            python_exe,
            service_user,
            service_group,
        ),
        "agca-live-cycle.timer": timer_text(
            "Run AGCA live product cycle every five minutes",
            "agca-live-cycle.service",
            "*-*-* *:00/5:00",
        ),
        "agca-hourly-forecast.service": service_text(
            "AGCA hourly forecast, settlement, learning and paper agent",
            "decision_engine.hourly_forecast_job",
            project_dir,
            python_exe,
            service_user,
            service_group,
        ),
        "agca-hourly-forecast.timer": timer_text(
            "Run AGCA hourly forecast with delayed-data retries",
            "agca-hourly-forecast.service",
            "*-*-* *:02,07,12:00",
        ),
    }


def _run(*args: str) -> None:
    subprocess.run(args, check=True)


def install(project_dir: Path, service_user: str) -> None:
    if os.geteuid() != 0:
        raise SystemExit("HATA: Ubuntu systemd kurulumu sudo/root gerektirir.")

    project_dir = project_dir.resolve()
    python_exe = Path(sys.executable).resolve()
    required = (
        project_dir / "analysis" / "forward_shadow_cycle.py",
        project_dir / "decision_engine" / "hourly_forecast_job.py",
        project_dir / "scripts" / "cloud_job_runner.py",
        project_dir / "crypto_data.db",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("HATA: eksik cloud runtime dosyası: " + ", ".join(missing))

    account = pwd.getpwnam(service_user)
    group_name = grp.getgrgid(account.pw_gid).gr_name

    ENV_DIR.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        ENV_FILE.write_text(
            "# Secrets are entered on the VM; never commit this file.\n"
            "AGCA_REMOTE_DATABASE_SCHEMA=agca_runtime\n"
            "AGCA_REMOTE_CONNECT_TIMEOUT_SECONDS=5\n"
            "AGCA_REMOTE_RETRY_SECONDS=60\n",
            encoding="utf-8",
        )
    os.chmod(ENV_FILE, 0o600)
    os.chown(ENV_FILE, 0, 0)

    LOCK_FILE.touch(exist_ok=True)
    os.chown(LOCK_FILE, account.pw_uid, account.pw_gid)
    os.chmod(LOCK_FILE, 0o600)

    reports = project_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    os.chown(reports, account.pw_uid, account.pw_gid)

    for name, text in payloads(
        project_dir, python_exe, service_user, group_name
    ).items():
        path = UNIT_DIR / name
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o644)

    _run("systemctl", "daemon-reload")
    _run(
        "systemctl",
        "enable",
        "--now",
        "agca-live-cycle.timer",
        "agca-hourly-forecast.timer",
    )

    print("AG Crypto Analyst - Ubuntu Cloud Runtime kuruldu")
    print(f"User     : {service_user}:{group_name}")
    print(f"Project  : {project_dir}")
    print(f"Python   : {python_exe}")
    print(f"Env      : {ENV_FILE}")
    print("Timers   : agca-live-cycle, agca-hourly-forecast")
    print("Catch-up : KAPALI (retrospektif forecast yok)")


def self_test() -> None:
    project = Path("/opt/agca/app")
    python = Path("/opt/agca/venv/bin/python")
    units = payloads(project, python, "ubuntu")
    assert len(units) == 4
    assert "Persistent=false" in units["agca-live-cycle.timer"]
    assert "*-*-* *:00/5:00" in units["agca-live-cycle.timer"]
    assert "*-*-* *:02,07,12:00" in units["agca-hourly-forecast.timer"]
    assert "analysis.forward_shadow_cycle" in units["agca-live-cycle.service"]
    assert (
        "decision_engine.hourly_forecast_job"
        in units["agca-hourly-forecast.service"]
    )
    assert "AGCA_REMOTE_DATABASE_URL" not in "\n".join(units.values())
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "unit"
        path.write_text(units["agca-live-cycle.service"], encoding="utf-8")
        assert path.stat().st_size > 100
    print("Ubuntu Cloud Runtime installer self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=str(Path.cwd()))
    parser.add_argument("--service-user", default=os.environ.get("SUDO_USER", "ubuntu"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    install(Path(args.project_dir), args.service_user)


if __name__ == "__main__":
    main()
