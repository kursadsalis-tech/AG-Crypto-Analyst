#!/usr/bin/env bash
set -euo pipefail

# Rebuild the bounded OKX L2 research pilot. Raw archives live only in a
# temporary directory and are deleted automatically on success or failure.

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"

temp_root=$(mktemp -d "${TMPDIR:-/tmp}/agca-okx-l2-pilot.XXXXXX")
trap 'rm -rf "$temp_root"' EXIT

download_and_extract() {
    local family=$1
    local date=$2
    local compact=${date//-/}
    local slug
    slug=$(printf '%s' "$family" | tr 'A-Z-' 'a-z_')
    local url="https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily/${compact}/${family}-SWAP-L2orderbook-400lv-${date}.tar.gz"
    local archive="$temp_root/${family}-${date}.tar.gz"
    local feature="$temp_root/${family}-${date}.csv"
    curl -sSfL --retry 3 --retry-delay 2 --max-time 300 "$url" -o "$archive"
    python3 -m data.okx_l2_archive_audit \
        --archive "$archive" --instrument "${family}-SWAP" \
        --output "reports/okx_l2_${slug}_archive_${date}.json" >/dev/null
    python3 -m data.okx_l2_feature_extractor \
        --archive "$archive" --instrument "${family}-SWAP" --source-url "$url" \
        --output "$feature" \
        --summary "reports/okx_l2_${slug}_features_${date}.json" >/dev/null
    rm "$archive"
    printf '%s\n' "$feature"
}

lqty_dates=(
    2025-05-01 2025-08-01 2025-11-01
    2026-02-01 2026-05-01 2026-08-01
)
lqty_inputs=()
for date in "${lqty_dates[@]}"; do
    lqty_inputs+=("$(download_and_extract LQTY-USDT "$date")")
done

lqty_args=()
for path in "${lqty_inputs[@]}"; do lqty_args+=(--input "$path"); done
python3 -m data.okx_l2_feature_dataset \
    "${lqty_args[@]}" \
    --output reports/okx_l2_lqty_causal_dataset.csv \
    --manifest reports/okx_l2_lqty_causal_dataset.json >/dev/null

cohort_families=(
    ARKM-USDT WLD-USDT TIA-USDT ETHFI-USDT BIO-USDT LQTY-USDT UMA-USDT
)
cohort_inputs=()
cohort_args=()
for family in "${cohort_families[@]}"; do
    if [[ $family == LQTY-USDT ]]; then
        for index in "${!lqty_dates[@]}"; do
            if [[ ${lqty_dates[$index]} == 2026-08-01 ]]; then
                cohort_inputs+=("${lqty_inputs[$index]}")
            fi
        done
    else
        cohort_inputs+=("$(download_and_extract "$family" 2026-08-01)")
    fi
    cohort_args+=(--instrument "${family}-SWAP")
done

cohort_input_args=()
for path in "${cohort_inputs[@]}"; do cohort_input_args+=(--input "$path"); done
python3 -m data.okx_l2_fixed_cohort \
    "${cohort_input_args[@]}" "${cohort_args[@]}" \
    --output reports/okx_l2_fixed_cohort_2026-08-01.csv \
    --manifest reports/okx_l2_fixed_cohort_2026-08-01.json >/dev/null

echo "OKX L2 pilot rebuilt successfully"
