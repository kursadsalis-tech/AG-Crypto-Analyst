"""Serialize cloud runtime jobs without hiding real module failures.

Both the five-minute live cycle and the hourly forecast retry cycle write the
same SQLite database.  A single non-blocking advisory lock prevents them from
running concurrently.  A busy lock is an expected skip, not a failed job.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import runpy
import sys
from pathlib import Path


DEFAULT_LOCK = "/var/lock/agca-runtime.lock"
ALLOWED_MODULES = {
    "analysis.forward_shadow_cycle",
    "decision_engine.hourly_forecast_job",
}


def run_module(module: str, lock_path: str = DEFAULT_LOCK) -> int:
    if module not in ALLOWED_MODULES:
        raise SystemExit(f"HATA: cloud job module izinli değil: {module}")

    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"Cloud job SKIPPED: runtime lock busy | module={module}")
            return 0

        print(f"Cloud job START: module={module} | pid={os.getpid()}")
        original_argv = sys.argv[:]
        try:
            sys.argv = [module]
            runpy.run_module(module, run_name="__main__")
        finally:
            sys.argv = original_argv
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        print(f"Cloud job END: module={module}")
        return 0


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        lock = str(Path(directory) / "runtime.lock")
        path = Path(lock)
        with path.open("a+", encoding="utf-8") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with path.open("a+", encoding="utf-8") as contender:
                try:
                    fcntl.flock(
                        contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError("second process must not acquire runtime lock")
    assert ALLOWED_MODULES == {
        "analysis.forward_shadow_cycle",
        "decision_engine.hourly_forecast_job",
    }
    print("Cloud job runner self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("module", nargs="?")
    parser.add_argument("--lock-path", default=DEFAULT_LOCK)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.module:
        parser.error("module is required")
    return run_module(args.module, args.lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
