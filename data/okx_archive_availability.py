"""Probe an OKX learning curriculum without downloading bulk L2 archives."""
from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


VERSION = "OKX_ARCHIVE_AVAILABILITY_V1"


def probe_url(url, timeout=30, runner=subprocess.run):
    """Use curl because the OKX CDN chain is not trusted by every Python CA bundle."""
    try:
        completed = runner(
            ["curl", "-sSIL", "-o", "/dev/null", "--max-time", str(timeout),
             "-w", "%{http_code}\t%header{content-length}", url],
            check=False, capture_output=True, text=True, timeout=timeout + 5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": 0, "content_length_bytes": 0, "error": type(exc).__name__}
    fields = completed.stdout.strip().split("\t")
    if completed.returncode or not fields or not fields[0].isdigit():
        return {"status": 0, "content_length_bytes": 0,
                "error": f"CURL_EXIT_{completed.returncode}"}
    length = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 0
    return {"status": int(fields[0]), "content_length_bytes": length}


def audit_availability(curriculum, probe=probe_url, workers=12):
    candidates = curriculum.get("archive_candidates", [])
    results = [None] * len(candidates)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, row["url"]): index
                   for index, row in enumerate(candidates)}
        for future in as_completed(futures):
            index = futures[future]
            results[index] = {**candidates[index], **future.result()}
    available = [row for row in results if row["status"] == 200]
    missing = [row for row in results if row["status"] == 404]
    errors = [row for row in results if row["status"] not in (200, 404)]
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "curriculum_version": curriculum.get("version"),
        "candidate_count": len(results),
        "available_count": len(available),
        "missing_count": len(missing),
        "error_count": len(errors),
        "available_compressed_bytes": sum(row["content_length_bytes"] for row in available),
        "available": available,
        "missing": missing,
        "errors": errors,
        "bulk_archives_downloaded": False,
        "ready_for_bounded_download": bool(available) and not errors,
        "ready_for_unbiased_historical_model_test": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curriculum", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    curriculum = json.loads(Path(args.curriculum).read_text(encoding="utf-8"))
    result = audit_availability(curriculum, workers=args.workers)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
