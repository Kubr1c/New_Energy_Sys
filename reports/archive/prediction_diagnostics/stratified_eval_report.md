# PV Prediction Stratified Evaluation Report

*Generated: 2026-05-04 03:37 UTC*
*Site: PVDAQ System 10 (39.7404, -105.1774), Capacity: 1.12 kW*

## Model: LightGBM Tuned (Full Features)

### t+1h

| Scenario | N | RMSE (kW) | nRMSE | MAE (kW) | Bias (kW) |
|----------|---|-----------|---:|----------|-----------:|
| clear    | 1242 | 0.092 | 0.192 | 0.059 | -0.007 |
| mixed    |  301 | 0.129 | 0.743 | 0.089 | +0.042 |
| overcast |  137 | 0.107 | 1.364 | 0.073 | +0.053 |
| night    | 2122 | 0.012 | 3.364 | 0.005 | +0.002 |
| TOTAL    | 3802 | 0.068 | 0.386 | 0.032 | +0.004 |

### t+6h

| Scenario | N | RMSE (kW) | nRMSE | MAE (kW) | Bias (kW) |
|----------|---|-----------|---:|----------|-----------:|
| clear    | 1240 | 0.133 | 0.277 | 0.073 | +0.014 |
| mixed    |  300 | 0.130 | 0.748 | 0.083 | +0.009 |
| overcast |  137 | 0.100 | 1.270 | 0.062 | +0.000 |
| night    | 2119 | 0.013 | 3.479 | 0.007 | +0.004 |
| TOTAL    | 3796 | 0.087 | 0.496 | 0.036 | +0.008 |

### t+24h

| Scenario | N | RMSE (kW) | nRMSE | MAE (kW) | Bias (kW) |
|----------|---|-----------|---:|----------|-----------:|
| clear    | 1231 | 0.134 | 0.280 | 0.075 | +0.014 |
| mixed    |  298 | 0.132 | 0.757 | 0.086 | +0.008 |
| overcast |  137 | 0.103 | 1.302 | 0.064 | +0.003 |
| night    | 2112 | 0.014 | 3.744 | 0.008 | +0.005 |
| TOTAL    | 3778 | 0.088 | 0.503 | 0.038 | +0.008 |

### Evening Bias Analysis (20–23 UTC Valid Hour)

| Horizon | N | Bias (kW) | RMSE (kW) |
|---------|---|----------:|----------:|
| t+1h |  636 | +0.015 | 0.104 |
| t+6h |  635 | +0.026 | 0.128 |
| t+24h |  632 | +0.026 | 0.128 |

## Comparison: Persistence Baseline

*(Prediction = PV power at origin time)*

### t+1h

| Scenario | N | RMSE (kW) | nRMSE | MAE (kW) | Bias (kW) |
|----------|---|-----------|---:|----------|-----------:|
| clear    | 1242 | 0.185 | 0.387 | 0.150 | -0.007 |
| mixed    |  301 | 0.175 | 1.009 | 0.123 | +0.032 |
| overcast |  137 | 0.108 | 1.375 | 0.059 | +0.019 |
| night    | 2122 | 0.027 | 7.480 | 0.006 | -0.001 |
| TOTAL    | 3802 | 0.120 | 0.687 | 0.064 | +0.000 |

### t+6h

| Scenario | N | RMSE (kW) | nRMSE | MAE (kW) | Bias (kW) |
|----------|---|-----------|---:|----------|-----------:|
| clear    | 1240 | 0.560 | 1.168 | 0.494 | -0.280 |
| mixed    |  300 | 0.386 | 2.226 | 0.307 | +0.047 |
| overcast |  137 | 0.221 | 2.810 | 0.143 | +0.022 |
| night    | 2119 | 0.318 | 87.311 | 0.163 | +0.156 |
| TOTAL    | 3796 | 0.415 | 2.371 | 0.282 | -0.000 |

### t+24h

| Scenario | N | RMSE (kW) | nRMSE | MAE (kW) | Bias (kW) |
|----------|---|-----------|---:|----------|-----------:|
| clear    | 1231 | 0.229 | 0.476 | 0.128 | -0.056 |
| mixed    |  298 | 0.280 | 1.606 | 0.196 | +0.113 |
| overcast |  137 | 0.396 | 5.024 | 0.297 | +0.265 |
| night    | 2112 | 0.016 | 4.452 | 0.002 | +0.000 |
| TOTAL    | 3778 | 0.170 | 0.974 | 0.069 | +0.001 |

## Figures

![stratified_rmse_by_scenario](figures/stratified_rmse_by_scenario.png)

![stratified_bias_by_valid_hour](figures/stratified_bias_by_valid_hour.png)

*Figures saved to `C:\Project\New_Energy_Sys\reports\figures`*

## Conclusion

- **t+1h**: TOTAL RMSE=0.068 kW, nRMSE=0.386, Bias=+0.004 kW
  - clear: nRMSE=0.192, N=1242
  - mixed: nRMSE=0.743, N=301
  - overcast: nRMSE=1.364, N=137

- **t+6h**: TOTAL RMSE=0.087 kW, nRMSE=0.496, Bias=+0.008 kW
  - clear: nRMSE=0.277, N=1240
  - mixed: nRMSE=0.748, N=300
  - overcast: nRMSE=1.270, N=137

- **t+24h**: TOTAL RMSE=0.088 kW, nRMSE=0.503, Bias=+0.008 kW
  - clear: nRMSE=0.280, N=1231
  - mixed: nRMSE=0.757, N=298
  - overcast: nRMSE=1.302, N=137

### Quality Gates

- t+1h clear < mixed < overcast nRMSE ordering: `True`
- t+6h clear < mixed < overcast nRMSE ordering: `True`
- t+24h clear < mixed < overcast nRMSE ordering: `True`

### Pitfall

当前分层基于 NSRDB 观测 clearsky_index_ghi 而非预报值。在实际预测场景中，valid_time 的 clearsky_index_ghi 不可提前获取，本报告的分层仅用于理解模型在不同天气条件下的表现差异，不能直接作为预报时期望性能的准确估计。
