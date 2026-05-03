"""HRRR GHI decomposition into DHI and DNI.

Two decomposition models are supported:

  Erbs  (Erbs, Klein & Duffie, 1982)
    Empirical piecewise polynomial relating clearness index kt to diffuse
    fraction kd.  Requires only GHI + solar geometry.  Fast and well-
    validated, but tends to underestimate diffuse under broken clouds in
    dry/high-altitude climates.

  DISC  (Maxwell, 1987)
    Quasi‑physical model that computes direct beam transmittance Kn from
    kt and absolute airmass.  Requires surface pressure (defaults to
    standard 101325 Pa if unavailable).  DISC typically yields 5–10 %
    lower DNI bias than Erbs at high‑altitude sites because the airmass
    correction accounts for reduced atmospheric scattering.

Output columns (common to both models):
  - solar_zenith_deg   Solar zenith angle (degrees)
  - kt                 Clearness index (dimensionless)
  - dhi_wm2            Diffuse horizontal irradiance (W/m²)
  - dni_wm2            Direct normal irradiance (W/m²)

References:
  Erbs, D.G., Klein, S.A., Duffie, J.A., 1982. Solar Energy 28, 293–302.
  Maxwell, E.L., 1987. SERI/TR-215-3087, Solar Energy Research Institute.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
import pvlib

ModelT = Literal["erbs", "disc"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def decompose_ghi_to_dhi_dni(
    ghi: pd.Series,
    timestamp: pd.DatetimeIndex,
    latitude: float,
    longitude: float,
    altitude: float = 1700.0,
    cloud_cover: pd.Series | None = None,
    model: ModelT = "disc",
    pressure: pd.Series | None = None,
) -> pd.DataFrame:
    """Decompose GHI into DHI and DNI.

    Parameters
    ----------
    ghi : pd.Series
        Global horizontal irradiance in W/m².
    timestamp : pd.DatetimeIndex
        UTC timestamps for each observation.
    latitude, longitude : float
        Site coordinates in decimal degrees.
    altitude : float
        Site elevation in metres.
    cloud_cover : pd.Series or None
        HRRR total cloud cover in percent (0–100).  Used only by the
        'erbs' model as a cap‑bounded boost to diffuse fraction under
        broken‑cloud conditions.
    model : {'erbs', 'disc'}
        Decomposition model (default 'erbs').
    pressure : pd.Series or None
        Surface pressure in Pa.  Used by the 'disc' model for absolute
        airmass correction.  If None, standard sea‑level pressure
        (101325 Pa) is used.

    Returns
    -------
    pd.DataFrame with columns:
        solar_zenith_deg, kt, dhi_wm2, dni_wm2
    """
    # ------------------------------------------------------------------
    # Common: solar geometry
    # ------------------------------------------------------------------
    location = pvlib.location.Location(
        latitude=latitude, longitude=longitude, altitude=altitude,
    )
    ts_dti = pd.DatetimeIndex(timestamp.values)
    solar_pos = location.get_solarposition(times=ts_dti)
    zenith_np = solar_pos["zenith"].values.astype(float)

    if len(zenith_np) != len(ghi):
        raise ValueError(
            f"Solar position returned {len(zenith_np)} rows "
            f"but input has {len(ghi)} rows."
        )

    daytime_np = zenith_np < 85.0

    # ------------------------------------------------------------------
    # Common: extraterrestrial radiation
    # ------------------------------------------------------------------
    solar_constant = 1367.0  # DISC uses 1367; Erbs originally 1361
    doy = ts_dti.dayofyear.astype(float)
    eccentricity = 1.0 + 0.033 * np.cos(2.0 * np.pi * doy / 365.0)
    dni_extra = solar_constant * eccentricity
    ghi_extra = dni_extra * np.cos(np.radians(zenith_np))
    ghi_extra = np.maximum(ghi_extra, 1e-6)

    # ------------------------------------------------------------------
    # Common: clearness index (cap differs by model)
    # ------------------------------------------------------------------
    ghi_np = ghi.values.astype(float)
    if model == "disc":
        # DISC caps kt at 1.0 (physical limit for clearness after
        # airmass correction).
        kt_np = np.clip(ghi_np / ghi_extra, 0.0, 1.0)
        result = _decompose_disc(
            ghi_np=ghi_np,
            zenith_np=zenith_np,
            daytime_np=daytime_np,
            kt_np=kt_np,
            dni_extra=dni_extra,
            doy=doy,
            pressure=pressure,
        )
    else:
        # Erbs allows kt up to 1.5 to accommodate cloud enhancement.
        kt_np = np.clip(ghi_np / ghi_extra, 0.0, 1.5)
        result = _decompose_erbs(
            ghi_np=ghi_np,
            zenith_np=zenith_np,
            daytime_np=daytime_np,
            kt_np=kt_np,
            latitude=latitude,
            doy=doy,
            cloud_cover=cloud_cover,
        )

    return pd.DataFrame(
        {
            "solar_zenith_deg": zenith_np,
            "kt": kt_np,
            "dhi_wm2": result["dhi_np"],
            "dni_wm2": result["dni_np"],
        },
        index=ghi.index,
    )


def decompose_hrrr_parquet(
    input_path: str,
    output_path: str,
    latitude: float = 39.74,
    longitude: float = -105.18,
    altitude: float = 1730.0,
    model: ModelT = "disc",
) -> pd.DataFrame:
    """Decompose GHI in a HRRR parquet file and write enriched output.

    Parameters
    ----------
    input_path : str
        Path to the input HRRR parquet (must contain ghi_wm2, timestamp).
    output_path : str
        Path where the enriched parquet will be written.
    latitude, longitude : float
        Site coordinates.
    altitude : float
        Site elevation in metres.
    model : {'erbs', 'disc'}
        Decomposition model.

    Returns
    -------
    pd.DataFrame
        Enriched DataFrame with dhi_wm2, dni_wm2, solar_zenith_deg, kt.
    """
    df = pd.read_parquet(input_path)
    ts = pd.to_datetime(df["timestamp"], utc=True)

    cloud_cover = df.get("cloud_cover_pct") if "cloud_cover_pct" in df.columns else None

    # Auto-detect pressure column (HRRR surface_pressure_hpa → Pa).
    pressure: pd.Series | None = None
    if model == "disc" and "surface_pressure_hpa" in df.columns:
        pressure = df["surface_pressure_hpa"] * 100.0  # hPa → Pa

    decomp = decompose_ghi_to_dhi_dni(
        ghi=df["ghi_wm2"],
        timestamp=ts,
        latitude=latitude,
        longitude=longitude,
        altitude=altitude,
        cloud_cover=cloud_cover,
        model=model,
        pressure=pressure,
    )

    enriched = df.copy()
    for col in ["solar_zenith_deg", "kt", "dhi_wm2", "dni_wm2"]:
        enriched[col] = decomp[col].values

    enriched.to_parquet(output_path, index=False)
    return enriched


# ---------------------------------------------------------------------------
# Erbs model (private)
# ---------------------------------------------------------------------------


def _decompose_erbs(
    *,
    ghi_np: np.ndarray,
    zenith_np: np.ndarray,
    daytime_np: np.ndarray,
    kt_np: np.ndarray,
    latitude: float,
    doy: np.ndarray,
    cloud_cover: pd.Series | None,
) -> dict[str, np.ndarray]:
    """Erbs clearness-index decomposition (pure numpy)."""
    # Sunset hour angle for the high-kt branch.
    lat_rad = np.radians(latitude)
    declination = np.radians(23.45 * np.sin(2.0 * np.pi * (284.0 + doy) / 365.0))
    cos_ws = -np.tan(lat_rad) * np.tan(declination)
    cos_ws = np.clip(cos_ws, -1.0, 1.0)
    ws = np.arccos(cos_ws)

    kd = np.full_like(kt_np, np.nan, dtype=float)

    # Branch 1: kt <= 0.22
    m_low = kt_np <= 0.22
    kd[m_low] = 1.0 - 0.09 * kt_np[m_low]

    # Branch 2: 0.22 < kt <= 0.80
    m_mid = (kt_np > 0.22) & (kt_np <= 0.80)
    ktm = kt_np[m_mid]
    kd[m_mid] = (
        0.9511 - 0.1604 * ktm + 4.388 * ktm**2
        - 16.638 * ktm**3 + 12.336 * ktm**4
    )

    # Branch 3: kt > 0.80
    m_high = kt_np > 0.80
    kth = kt_np[m_high]
    wsh = ws[m_high]
    kd[m_high] = np.where(
        np.isfinite(wsh) & (wsh > 0.0),
        0.165,
        np.where(kth < 0.87, 1.0 - 0.67 * kth, np.nan),
    )
    kd = np.where(np.isfinite(kd), kd, 0.2)

    cos_z = np.cos(np.radians(zenith_np))
    dhi_np = (ghi_np * kd).clip(min=0.0)
    dni_np = ((ghi_np - dhi_np) / np.maximum(cos_z, 1e-6)).clip(min=0.0)

    # ---- Cloud-cover adjustment (capped) ----
    if cloud_cover is not None:
        cc_np = cloud_cover.values.astype(float)
        broken = (
            (kt_np > 0.3) & (kt_np < 0.75)
            & (cc_np > 50.0)
            & daytime_np
        )
        if broken.any():
            raw_correction = ghi_np * 0.10 * (cc_np / 100.0)
            headroom = np.maximum(0.0, ghi_np - dhi_np)
            correction = np.minimum(raw_correction, headroom * 0.90)
            dhi_np[broken] = dhi_np[broken] + correction[broken]
            dni_np[broken] = (
                (ghi_np[broken] - dhi_np[broken])
                / np.maximum(cos_z[broken], 1e-6)
            )
            dni_np = dni_np.clip(min=0.0)

    # ---- Night-time ----
    night = ~daytime_np
    ghi_np[night] = 0.0
    dhi_np[night] = 0.0
    dni_np[night] = 0.0

    return {"dhi_np": dhi_np, "dni_np": dni_np}


# ---------------------------------------------------------------------------
# DISC model (private)
# ---------------------------------------------------------------------------


def _decompose_disc(
    *,
    ghi_np: np.ndarray,
    zenith_np: np.ndarray,
    daytime_np: np.ndarray,
    kt_np: np.ndarray,
    dni_extra: np.ndarray,
    doy: np.ndarray,
    pressure: pd.Series | None,
) -> dict[str, np.ndarray]:
    """DISC direct-beam transmittance decomposition (pure numpy).

    Algorithm (Maxwell 1987):
      1. Compute relative airmass (Kasten & Young 1989).
      2. Correct to absolute airmass if pressure is available.
      3. Compute clear‑sky transmittance Knc from airmass polynomial.
      4. Compute cloud correction δKn = a + b·exp(c·AM) where
         a, b, c are piecewise polynomials of kt.
      5. Kn = Knc − δKn  (clamped non‑negative).
      6. DNI = Kn × I₀_extraterrestrial.
      7. DHI = GHI − DNI·cos(θz)  (energy closed by construction).
    """
    z_rad = np.radians(zenith_np)
    cos_z = np.cos(z_rad)

    # ---- Airmass (Kasten & Young 1989) ----
    z_clipped = np.where(zenith_np < 87.0, zenith_np, 87.0)
    am_rel = 1.0 / (
        np.cos(np.radians(z_clipped))
        + 0.50572 * (96.07995 - z_clipped) ** (-1.6364)
    )
    am_rel = np.clip(am_rel, 1.0, None)

    if pressure is not None:
        p_np = pressure.values.astype(float)
        am_abs = am_rel * (p_np / 101325.0)
    else:
        am_abs = am_rel

    # Cap airmass at 12 (range over which Kn was fit).
    am = np.minimum(am_abs, 12.0)

    # ---- Clear-sky direct beam transmittance Knc ----
    Knc = (
        0.866
        + am * (-0.122 + am * (0.0121 + am * (-0.000653 + 1.4e-05 * am)))
    )

    # ---- Cloud correction δKn = a + b·exp(c·AM) ----
    is_cloudy = kt_np <= 0.6

    # Polynomial coefficients a, b, c — Horner form.
    a = np.where(
        is_cloudy,
        0.512 + kt_np * (-1.56 + kt_np * (2.286 - 2.222 * kt_np)),
        -5.743 + kt_np * (21.77 + kt_np * (-27.49 + 11.56 * kt_np)),
    )
    b = np.where(
        is_cloudy,
        0.37 + 0.962 * kt_np,
        41.4 + kt_np * (-118.5 + kt_np * (66.05 + 31.9 * kt_np)),
    )
    c = np.where(
        is_cloudy,
        -0.28 + kt_np * (0.932 - 2.048 * kt_np),
        -47.01 + kt_np * (184.2 + kt_np * (-222.0 + 73.81 * kt_np)),
    )

    delta_kn = a + b * np.exp(c * am)

    # ---- Direct beam transmittance ----
    Kn = Knc - delta_kn
    Kn = np.clip(Kn, 0.0, None)

    # ---- DNI and DHI ----
    dni_np = Kn * dni_extra

    # Remove bad values: high zenith, negative GHI, negative DNI.
    bad = (zenith_np > 87.0) | (ghi_np < 0) | (dni_np < 0)
    dni_np = np.where(bad, 0.0, dni_np)

    # Energy closure: DHI = GHI − DNI·cos(θz), clamped ≥ 0.
    dhi_np = np.maximum(0.0, ghi_np - dni_np * cos_z)

    # ---- Night-time ----
    night = ~daytime_np
    ghi_np[night] = 0.0
    dhi_np[night] = 0.0
    dni_np[night] = 0.0

    return {"dhi_np": dhi_np, "dni_np": dni_np}
