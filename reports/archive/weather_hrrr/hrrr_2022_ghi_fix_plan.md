# Fix 2022 HRRR GHI All-Zero & Distributed Re-Extraction Plan

## Context

**Problem**: The 2022 HRRR f24 parquet has all-zero GHI (all 8748 rows). Temperature/wind/humidity are fine.

**Root Cause**: The 2022 extraction used old code that read DSWRF from `hrrrzarr` Zarr — the Zarr metadata exists but actual forecast data chunks for DSWRF are absent. The code silently set missing values to 0.0.

**Fix Status**: The GRIB2 DSWRF fallback (`_read_grib_dswrf_value()`) already exists in `src/new_energy_sys/hrrr_point_forecast.py` lines 843-888. The 2021 extraction used it successfully (GHI max 1062, mean 213). The payload zip `reports/hrrr_ec2_payload.zip` already contains this fix.

**v2 Report Reference**: `reports/hrrr_validation_audit_v2.md` confirms GHI=0 + precipitation=accumulated (not hourly) for 2022.

---

## Plan

### Phase 1: Sample Verification via Probe (~20 min)

Send a strict-probe-only SSM command to instance `i-003506e7961b8d034`. This runs 4 seasonal 48h windows (192 rows) and validates the probe contract — including the `dswrf_source_trace` gate that checks:
- Every row's `source_url` contains `DSWRF` 
- `ghi_wm2.max() > 0`

```powershell
scripts/aws-hrrr-run-ssm.ps1 `
  -InstanceId i-003506e7961b8d034 `
  -ForecastYear 2022 `
  -SkipProbe:$false `
  -AllowFullRun:$false
```

**Verify**: Download probe results from S3. Confirm:
- `dswrf_source_trace.passed = true`
- GHI max > 500 W/m2 in summer window
- Source URLs contain `#idx=DSWRF@surface` (GRIB2 fallback evidence)

**If probe fails**: stop, investigate source_url. **If probe passes**: proceed.

---

### Phase 2: Code Changes for Month-Range Distribution

Modify **2 files only** (~45 lines total):

**File 1: `scripts/aws-hrrr-run-remote.sh`**

1. Add 3 new env vars after line 8:
```bash
HRRR_MONTH_START="${HRRR_MONTH_START:-}"
HRRR_MONTH_END="${HRRR_MONTH_END:-}"
HRRR_MERGE_AFTER="${HRRR_MERGE_AFTER:-1}"
```

2. After the manifest glob (line 159) and before the loop (line 164), insert month-range filter:
```bash
if [[ -n "$HRRR_MONTH_START" && -n "$HRRR_MONTH_END" ]]; then
    filtered=()
    for manifest in "${monthly_manifests[@]}"; do
        month_tag=$(basename "$manifest" | grep -oP "${HRRR_YEAR}_\K\d{2}")
        if [[ "$month_tag" -ge "$HRRR_MONTH_START" && "$month_tag" -le "$HRRR_MONTH_END" ]]; then
            filtered+=("$manifest")
        fi
    done
    monthly_manifests=("${filtered[@]}")
fi
```

3. Guard the merge block (lines 191-209) with `if [[ "$HRRR_MERGE_AFTER" == "1" ]]; then ... else upload monthly parquets individually; fi`

**File 2: `scripts/aws-hrrr-run-ssm.ps1`**

1. Add parameters `[int]$MonthStart = 0, [int]$MonthEnd = 0` after line 18
2. Add 3 env var exports to the `$commands` array (before line 218):
```powershell
"export HRRR_MONTH_START='$(if ($MonthStart -gt 0) { $MonthStart } else { "" })'"
"export HRRR_MONTH_END='$(if ($MonthEnd -gt 0) { $MonthEnd } else { "" })'"
"export HRRR_MERGE_AFTER='$(if ($MonthStart -gt 0 -and $MonthEnd -gt 0) { "0" } else { "1" })'"
```

---

### Phase 3: Distributed Full Extraction (~4h parallel)

Rebuild payload with updated scripts, then launch both instances in parallel:

**Instance A** (i-003506e7961b8d034) — Months 1-6:
```powershell
scripts/aws-hrrr-run-ssm.ps1 `
  -InstanceId i-003506e7961b8d034 `
  -ForecastYear 2022 `
  -MonthStart 1 -MonthEnd 6 `
  -SkipProbe -AllowFullRun
```

**Instance B** (i-02a4daad75b094878) — Months 7-12:
```powershell
scripts/aws-hrrr-run-ssm.ps1 `
  -InstanceId i-02a4daad75b094878 `
  -ForecastYear 2022 `
  -MonthStart 7 -MonthEnd 12 `
  -SkipProbe -AllowFullRun
```

Each instance has its own S3 bucket (random suffix) — no collision. Estimated ~4h per instance (vs 8h serial).

---

### Phase 4: Download, Merge, Validate

1. Download monthly parquets and audits from both S3 buckets
2. Merge all 12 months locally:
```powershell
python -m new_energy_sys.cli.merge_hrrr_point_forecast_batches `
  --input-dir <merged-monthly-dir> `
  --audit-dir <merged-audit-dir> `
  --output-parquet data/processed/pvdaq_nsrdb_2020_2022/stage7_hrrr_forecast_weather_2022_f24.parquet `
  --audit-json reports/hrrr_point_forecast_2022_f24_audit.json `
  --expected-start 2022-01-01 --expected-end 2022-12-31 `
  --lead-time 24 --min-lead-time 24 --max-lead-time 48
```
The merge function (line 1467) auto-rejects all-zero GHI — if the fix didn't work, this step fails.

3. Validate: Check GHI max > 500, non-zero rate reasonable, precipitation is hourly (not accumulated ~12000mm/yr), source_url contains DSWRF.

4. Replace old parquet. Keep backup of original.

---

### Phase 5: Cleanup

Stop both EC2 instances to avoid ongoing costs:
```powershell
aws ec2 stop-instances --instance-ids i-003506e7961b8d034 i-02a4daad75b094878 --profile new-energy-hrrr --region us-west-1
```

---

## Verification

| Step | Check | Expected |
|------|-------|----------|
| Probe | `dswrf_source_trace.passed` | true |
| Probe | `ghi_distribution.summer_hrrr_ghi_max_wm2` | > 500 |
| Probe | source_url contains `DSWRF@surface` | 100% of rows |
| Merge | `ghi_wm2.max() > 0` | true (merge auto-rejects all-zero) |
| Final | GHI max (full year) | 800-1100 W/m2 |
| Final | GHI non-zero rate (daytime) | > 85% |
| Final | Precipitation annual total | ~400-500mm (not 12000) |
| Final | `precipitation_semantics` in audit | present ("accumulated_to_hourly_diff") |

## Files Modified

- `scripts/aws-hrrr-run-remote.sh` — ~30 lines added
- `scripts/aws-hrrr-run-ssm.ps1` — ~15 lines added
- No Python source changes needed
