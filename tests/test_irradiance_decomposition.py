"""Property-based and physical-invariant tests for GHI → DHI+DNI decomposition.

All assertions are derived from solar physics invariants, not regression
values, so the tests are robust against model parameter changes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def golden_noonday() -> tuple[pd.Series, pd.DatetimeIndex]:
    """19:00 UTC = noon MDT at Golden, CO (105.18°W)."""
    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([900.0], index=ts)
    return ghi, ts


@pytest.fixture
def site_params() -> dict:
    return {"latitude": 39.74, "longitude": -105.18, "altitude": 1730.0}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_module_imports():
    from new_energy_sys.irradiance_decomposition import (
        decompose_ghi_to_dhi_dni,
        decompose_hrrr_parquet,
    )

    assert callable(decompose_ghi_to_dhi_dni)
    assert callable(decompose_hrrr_parquet)


# ---------------------------------------------------------------------------
# Shape and columns
# ---------------------------------------------------------------------------


def test_output_columns(site_params):
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=3, freq="h", tz="UTC")
    ghi = pd.Series([900.0, 200.0, 0.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    assert list(result.columns) == ["solar_zenith_deg", "kt", "dhi_wm2", "dni_wm2"]
    assert len(result) == 3
    assert result.index.equals(ghi.index)


def test_output_dtypes(site_params):
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([900.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    for col in ["solar_zenith_deg", "kt", "dhi_wm2", "dni_wm2"]:
        assert np.issubdtype(result[col].dtype, np.floating), (
            f"{col} should be float, got {result[col].dtype}"
        )


# ---------------------------------------------------------------------------
# Physical invariants
# ---------------------------------------------------------------------------


def test_energy_closure_clear_sky(site_params):
    """GHI = DHI + DNI·cos(θz) must hold to machine precision."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([900.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    z_rad = np.radians(result["solar_zenith_deg"].values)
    reconstructed = result["dhi_wm2"].values + result["dni_wm2"].values * np.cos(z_rad)
    np.testing.assert_allclose(reconstructed, ghi.values, atol=1e-6)


def test_energy_closure_overcast(site_params):
    """Closure must hold for low-light (overcast) conditions."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([200.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    z_rad = np.radians(result["solar_zenith_deg"].values)
    reconstructed = result["dhi_wm2"].values + result["dni_wm2"].values * np.cos(z_rad)
    np.testing.assert_allclose(reconstructed, ghi.values, atol=1e-6)


def test_energy_closure_with_cloud_adjustment(site_params):
    """Closure must hold when cloud-cover adjustment is active."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([400.0], index=ts)
    cc = pd.Series([80.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, cloud_cover=cc, model="erbs", **site_params)

    z_rad = np.radians(result["solar_zenith_deg"].values)
    reconstructed = result["dhi_wm2"].values + result["dni_wm2"].values * np.cos(z_rad)
    np.testing.assert_allclose(reconstructed, ghi.values, atol=1e-6)


def test_energy_closure_multi_timestamp(site_params):
    """Closure must hold across a full diurnal cycle for daytime rows."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01", periods=24, freq="h", tz="UTC")
    # Simulated clear-sky bell curve
    hours = np.arange(24)
    ghi_vals = 900.0 * np.sin(np.pi * np.clip((hours - 6) / 12.0, 0, 1))
    ghi = pd.Series(ghi_vals, index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    z_rad = np.radians(result["solar_zenith_deg"].values)
    reconstructed = result["dhi_wm2"].values + result["dni_wm2"].values * np.cos(z_rad)
    # Only verify daytime rows — nighttime GHI is zeroed by the function
    daytime = result["solar_zenith_deg"].values < 85.0
    np.testing.assert_allclose(
        reconstructed[daytime], ghi.values[daytime], atol=1e-6
    )


# ---------------------------------------------------------------------------
# DHI ≤ GHI invariant
# ---------------------------------------------------------------------------


def test_dhi_never_exceeds_ghi(site_params):
    """DHI must never exceed GHI (would imply negative DNI)."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=10, freq="h", tz="UTC")
    ghi = pd.Series(np.linspace(10, 1000, 10), index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    daytime = ghi.values > 10
    assert (result["dhi_wm2"].values[daytime] <= ghi.values[daytime] + 1e-9).all()


def test_dhi_never_exceeds_ghi_with_cloud(site_params):
    """DHI ≤ GHI with cloud-cover adjustment active (Erbs model)."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=10, freq="h", tz="UTC")
    ghi_vals = np.linspace(10, 1000, 10)
    ghi = pd.Series(ghi_vals, index=ts)
    cc = pd.Series(np.full(10, 100.0), index=ts)  # worst case: full cloud
    result = decompose_ghi_to_dhi_dni(ghi, ts, cloud_cover=cc, model="erbs", **site_params)

    daytime = ghi_vals > 10
    assert (result["dhi_wm2"].values[daytime] <= ghi_vals[daytime] + 1e-9).all()


# ---------------------------------------------------------------------------
# Night-time handling
# ---------------------------------------------------------------------------


def test_nighttime_all_components_zero(site_params):
    """All irradiance components must be zero when sun is below horizon."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 06:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([23.0], index=ts)  # HRRR residual
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    assert result["dhi_wm2"].iloc[0] == 0.0
    assert result["dni_wm2"].iloc[0] == 0.0
    # Night rows also zero GHI implicitly through the return values


def test_nighttime_high_zenith(site_params):
    """Zenith > 85° is classified as night for all rows."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-01-01 06:00", periods=6, freq="h", tz="UTC")
    ghi = pd.Series([0, 0, 0, 0, 0, 0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    night_mask = result["solar_zenith_deg"] > 85.0
    assert (result.loc[night_mask, "dhi_wm2"] == 0.0).all()
    assert (result.loc[night_mask, "dni_wm2"] == 0.0).all()


# ---------------------------------------------------------------------------
# Non-negativity
# ---------------------------------------------------------------------------


def test_all_outputs_nonnegative(site_params):
    """DHI and DNI must never be negative."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01", periods=24 * 30, freq="h", tz="UTC")
    # Random GHI profile
    rng = np.random.default_rng(42)
    ghi_vals = rng.uniform(0, 1100, size=len(ts))
    ghi = pd.Series(ghi_vals, index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    assert (result["dhi_wm2"] >= -1e-9).all()
    assert (result["dni_wm2"] >= -1e-9).all()


# ---------------------------------------------------------------------------
# Clearness index bounds
# ---------------------------------------------------------------------------


def test_kt_range(site_params):
    """Clearness index should be in [0, 1.0] (DISC default cap)."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01", periods=24, freq="h", tz="UTC")
    rng = np.random.default_rng(99)
    ghi = pd.Series(rng.uniform(0, 1100, size=24), index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    assert result["kt"].min() >= 0.0
    assert result["kt"].max() <= 1.0


# ---------------------------------------------------------------------------
# Physical plausibility
# ---------------------------------------------------------------------------


def test_overcast_diffuse_dominant(site_params):
    """Under overcast (low kt), DHI should dominate (>80% of GHI)."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([200.0], index=ts)  # low GHI → low kt → overcast
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    ratio = result["dhi_wm2"].iloc[0] / ghi.iloc[0]
    assert ratio > 0.8, f"Overcast DHI/GHI ratio {ratio:.3f} should be > 0.8"


def test_clear_sky_dni_dominant(site_params):
    """Under clear sky (high kt), DNI should dominate (>50% of GHI)."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([900.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    dni_equiv = result["dni_wm2"].iloc[0] * np.cos(
        np.radians(result["solar_zenith_deg"].iloc[0])
    )
    ratio = dni_equiv / ghi.iloc[0]
    assert ratio > 0.5, f"Clear-sky DNI·cos(z)/GHI ratio {ratio:.3f} should be > 0.5"


# ---------------------------------------------------------------------------
# Cloud-cover effect
# ---------------------------------------------------------------------------


def test_cloud_increases_diffuse_fraction(site_params):
    """Adding cloud cover should increase DHI (Erbs model cloud boost)."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([500.0], index=ts)
    result_no_cloud = decompose_ghi_to_dhi_dni(ghi, ts, model="erbs", **site_params)
    cc = pd.Series([90.0], index=ts)
    result_cloud = decompose_ghi_to_dhi_dni(ghi, ts, cloud_cover=cc, model="erbs", **site_params)

    # Cloud should increase DHI (or leave it unchanged if correction capped)
    assert result_cloud["dhi_wm2"].iloc[0] >= result_no_cloud["dhi_wm2"].iloc[0] - 1e-9


def test_cloud_adjustment_only_in_broken_zone(site_params):
    """Erbs cloud correction only activates for kt in (0.3,0.75) and cc>50."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    # Overcast (low kt): shouldn't trigger correction
    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([100.0], index=ts)  # kt ~0.08
    cc = pd.Series([100.0], index=ts)
    result_no = decompose_ghi_to_dhi_dni(ghi, ts, model="erbs", **site_params)
    result_cc = decompose_ghi_to_dhi_dni(ghi, ts, cloud_cover=cc, model="erbs", **site_params)
    # DHI should be identical (cloud correction not triggered)
    np.testing.assert_allclose(
        result_no["dhi_wm2"].values, result_cc["dhi_wm2"].values, atol=1e-9
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_zero_ghi(site_params):
    """Zero GHI should produce zero DHI and DNI."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([0.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    assert result["dhi_wm2"].iloc[0] == 0.0
    assert result["dni_wm2"].iloc[0] == 0.0
    assert result["kt"].iloc[0] == 0.0


def test_very_large_ghi(site_params):
    """Solar constant ~1361 W/m²; GHI up to 1200 shouldn't crash."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([1200.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)
    assert result["dhi_wm2"].iloc[0] >= 0
    assert result["dni_wm2"].iloc[0] >= 0


def test_empty_input(site_params):
    """Empty Series should produce empty DataFrame."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.DatetimeIndex([], tz="UTC")
    ghi = pd.Series([], dtype=float, index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    assert len(result) == 0
    assert list(result.columns) == ["solar_zenith_deg", "kt", "dhi_wm2", "dni_wm2"]


def test_zenith_boundary_85_degrees(site_params):
    """Rows with zenith close to 85° should be handled correctly (day/night boundary)."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    # Use a time near sunrise when zenith ~85°
    ts = pd.date_range("2021-07-01 11:30", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([50.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    z = result["solar_zenith_deg"].iloc[0]
    if z < 85.0:
        # Daytime: DHI/DNI should be positive if GHI > 0
        assert result["dhi_wm2"].iloc[0] >= 0
        assert result["dni_wm2"].iloc[0] >= 0
    else:
        # Nighttime: all zero
        assert result["dhi_wm2"].iloc[0] == 0.0
        assert result["dni_wm2"].iloc[0] == 0.0


def test_cloud_cover_none_handled(site_params):
    """cloud_cover=None should work the same as not providing it."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([500.0], index=ts)
    result_none = decompose_ghi_to_dhi_dni(ghi, ts, cloud_cover=None, **site_params)
    result_noarg = decompose_ghi_to_dhi_dni(ghi, ts, **site_params)

    np.testing.assert_allclose(result_none.values, result_noarg.values, atol=1e-9)


# ---------------------------------------------------------------------------
# DISC model tests
# ---------------------------------------------------------------------------


def test_disc_model_runs(site_params):
    """DISC model produces valid output without crashing."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=3, freq="h", tz="UTC")
    ghi = pd.Series([900.0, 400.0, 100.0], index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, model="disc", **site_params)

    assert len(result) == 3
    assert (result["dhi_wm2"] >= 0).all()
    assert (result["dni_wm2"] >= 0).all()
    assert result["kt"].max() <= 1.0  # DISC caps kt at 1.0


def test_disc_energy_closure(site_params):
    """DISC model must maintain GHI = DHI + DNI·cos(θz)."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=10, freq="h", tz="UTC")
    rng = np.random.default_rng(123)
    ghi = pd.Series(rng.uniform(10, 1000, size=10), index=ts)
    result = decompose_ghi_to_dhi_dni(ghi, ts, model="disc", **site_params)

    z_rad = np.radians(result["solar_zenith_deg"].values)
    reconstructed = result["dhi_wm2"].values + result["dni_wm2"].values * np.cos(z_rad)
    daytime = result["solar_zenith_deg"].values < 85.0
    np.testing.assert_allclose(
        reconstructed[daytime], ghi.values[daytime], atol=1e-6
    )


def test_disc_with_pressure(site_params):
    """DISC with explicit pressure should not crash and maintain closure."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=3, freq="h", tz="UTC")
    ghi = pd.Series([900.0, 400.0, 100.0], index=ts)
    pressure = pd.Series([83000.0, 83000.0, 83000.0], index=ts)  # ~1700m
    result = decompose_ghi_to_dhi_dni(ghi, ts, model="disc", pressure=pressure, **site_params)

    z_rad = np.radians(result["solar_zenith_deg"].values)
    reconstructed = result["dhi_wm2"].values + result["dni_wm2"].values * np.cos(z_rad)
    daytime = result["solar_zenith_deg"].values < 85.0
    np.testing.assert_allclose(
        reconstructed[daytime], ghi.values[daytime], atol=1e-6
    )


def test_disc_vs_erbs_agree_in_overcast(site_params):
    """Both models should agree that overcast = mostly diffuse."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([150.0], index=ts)  # low GHI → low kt → overcast
    erbs = decompose_ghi_to_dhi_dni(ghi, ts, model="erbs", **site_params)
    disc = decompose_ghi_to_dhi_dni(ghi, ts, model="disc", **site_params)

    # Both should predict DHI >= 90% of GHI for overcast
    assert erbs["dhi_wm2"].iloc[0] / ghi.iloc[0] > 0.9
    assert disc["dhi_wm2"].iloc[0] / ghi.iloc[0] > 0.9


def test_disc_dni_positive_in_clear_sky(site_params):
    """DISC should produce meaningful DNI under clear-sky conditions."""
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([950.0], index=ts)  # high GHI → high kt → clear
    result = decompose_ghi_to_dhi_dni(ghi, ts, model="disc", **site_params)

    # Clear sky: DNI should be substantial (> 40% of GHI equivalent)
    z_rad = np.radians(result["solar_zenith_deg"].iloc[0])
    dni_equiv = result["dni_wm2"].iloc[0] * np.cos(z_rad)
    assert dni_equiv / ghi.iloc[0] > 0.4


def test_disc_pressure_changes_output(site_params):
    """DISC with explicit pressure should produce different results vs default.

    The direction of the airmass effect depends on kt:
    - At very high kt (super-clear), lower airmass → higher DNI (less scattering).
    - At moderate kt, the delta_kn correction (calibrated at sea level) can
      over-correct and invert the relationship.  This is a known DISC
      limitation — the Dirindex model was developed partly to address it.
    """
    from new_energy_sys.irradiance_decomposition import decompose_ghi_to_dhi_dni

    ts = pd.date_range("2021-07-01 19:00", periods=1, freq="h", tz="UTC")
    ghi = pd.Series([950.0], index=ts)

    # Very low pressure (~4000m, Bolivia altiplano)
    pres_high = pd.Series([62000.0], index=ts)
    res_high = decompose_ghi_to_dhi_dni(ghi, ts, model="disc", pressure=pres_high, **site_params)

    # Default (sea-level) pressure
    res_default = decompose_ghi_to_dhi_dni(ghi, ts, model="disc", **site_params)

    # Pressure change must affect outputs — not zero effect
    assert abs(res_high["dni_wm2"].iloc[0] - res_default["dni_wm2"].iloc[0]) > 1.0, (
        "Pressure should change DISC output (airmass correction active)"
    )
