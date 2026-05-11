"""Quick script: scale predictions to Rawhide scale and run Stage22."""
from __future__ import annotations

import pandas as pd
from pathlib import Path

from new_energy_sys.config import load_config
from new_energy_sys.stage18_rawhide_simulation import scale_predictions_to_rawhide

RAW_DATA = Path("data/processed/pvdaq_nsrdb_2020_2022")
OUT_DIR = Path("data/processed/rawhide_scaled")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Load config and predictions
config = load_config("configs/data_sources.rawhide_prairie_scaled_2020_2022.json")
predictions = pd.read_csv(RAW_DATA / "stage9_main_model_predictions.csv")

# Scale
scaled = scale_predictions_to_rawhide(predictions, config.raw)
scaled_path = OUT_DIR / "stage9_rawnhide_scaled_predictions.csv"
scaled.to_csv(scaled_path, index=False)
print(f"Scaled predictions written to {scaled_path}")
print(f"  prediction_kw max: {scaled['prediction_kw'].max():.0f}")
print(f"  actual_kw max: {scaled['actual_kw'].max():.0f}")
print(f"  rows: {len(scaled)}")
