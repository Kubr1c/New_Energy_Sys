# Work Log

跨 agent 工作日志，记录每次操作变动，保持进度同步。

---

## Session Start - 2026-05-03

**Agent**: Claude Code (deepseek-v4-pro)
**状态**: 接手项目，执行 initial assessment

**发现**:
- PROGRESS.md 记录到 Stage19（Rawhide 前端接入）
- 最后一次 Handover Snapshot: 2026-04-30 Frontend Dev Server Fix
- 存在 21 个 modified 文件 + 大量 untracked 文件（未提交的 WIP）
- `docs/frontend_review_plan.md` 提供了前端改进方案，但有 4 个 Open Questions 未回答
- 当前无 memory 文件

**WIP 变更涉及模块**:
- `src/new_energy_sys/hrrr.py` — HRRR 天气提取增强（`DEFAULT_REQUIRED_PATTERNS` 常量化、`_record_range_end` 精确字节范围）
- `src/new_energy_sys/cleaning.py` — 新增 `_compute_time_axis_diagnostics` PV/GHI 时间轴校验
- `src/new_energy_sys/standardize.py` — 标准化增强
- `frontend/` 多个文件 — UI 改进，新增 InsightSummary.vue 组件
- `pyproject.toml`, `requirements.txt` — 依赖更新

---

## HRRR 三年并行抽取计划 Review - 2026-05-03

**Review 结论**: 方案架构合理，已有代码基本能支撑，但需要补充 4 个关键点。

### 现有代码支撑情况

| 需求 | 现有实现 | 状态 |
|------|---------|------|
| 年份隔离 bucket 名 | `aws-hrrr-run-ssm.ps1:117` bucket 含 `y$ForecastYear` + 随机后缀 | 已支持 |
| 年份隔离下载目录 | `aws-hrrr-run-ssm.ps1:127` download_target 已按年份分目录 | 已支持 |
| 远程年度合并 | `aws-hrrr-run-remote.sh:192-209` 按 `$HRRR_YEAR` merge | 已支持 |
| Candidate lead-time (f24-f48) | `hrrr_point_forecast.py:1154` `lead_times_as_candidates=True` | 已支持 |
| 预算硬限制 | `DownloadBudget` class + projected stop gate | 已支持 |
| 两阶段安全门（probe → full） | `aws-hrrr-run-remote.sh:67-153` probe 先过审再全量 | 已支持 |
| DSWRF 混合路径（Zarr + GRIB） | `_collect_zarr_sample:938-948` DSWRF 走 GRIB byte-range | 已支持 |
| Stage7 契约校验 | `hrrr_stage7_contract.py` 13 个硬门禁 | 已支持 |

### Review 发现的问题

**1. 缺少 Monthly Manifest 预生成步骤** ⚠️

远程脚本需要 `reports/hrrr_monthly/hrrr_point_forecast_${HRRR_YEAR}_*_f24_manifest.json` 已存在于 payload 中。当前计划未说明 2020/2021 的 12 个月 manifest 何时生成。

建议：在启动 EC2 前，本地运行三年（2020/2021/2022）的 `build_hrrr_monthly_point_forecast_manifests`，生成 36 个 manifest 文件打包进 payload。

**2. NOAA GRIB Archive 对 2020-2021 的可用性未验证** ⚠️

DSWRF 绕过了 Zarr 的缺失数据问题，走 NOAA GRIB byte-range 下载。但 2020-2021 的 HRRR GRIB 可能已被迁移到 archive 路径或完全下线。当前 `hrrr.py` 中的 `build_hrrr_cycle_urls` 使用的 URL pattern 需要确认对 2020-2021 是否仍然有效。

建议：先在本地用 2020-01-15 和 2021-01-15 的一个采样时间点做 DSWRF GRIB 下载连通性测试。

**3. WIP `hrrr.py` 的 `_record_range_end` 函数是关键改进** ✅

当前 WIP 中的 `_record_range_end` 函数修复了一个重要 bug：之前可能把选中变量之间的不相关 GRIB 消息也下载了。这对 DSWRF-only 下载路径尤其重要——如果没有这个修复，每次都下载远超 DSWRF 的数据量。该改动**必须在打包 payload 前提交**。

**4. 三年合并后缺少跨年一致性校验** ⚠️

计划提到"三年合并后再跑一次整体 contract"，但现有 `merge_hrrr_point_forecast_batches` 只支持单年合并。三年跨年合并需要一个额外的步骤。

建议：在 `merge_hrrr_point_forecast_batches` 之后，单独写一个简单的三年 concat + 全量 contract 调用。

### 关于 "数据有问题" 的判断

根据 `cleaning.py` WIP 中新增的 `_compute_time_axis_diagnostics` 函数和 `frontend_review_plan.md`，数据问题可能涉及：

- PVDAQ 本地时间被误当 UTC 导致 PV-GHI 时间轴错位（`_compute_time_axis_diagnostics` 检查的就是这个问题）
- HRRR Zarr DSWRF 缺失导致 GHI 列全零（`hrrr_stage7_contract.py:467` 会拦截此问题）
- 之前 Stage7 Open-Meteo forecast 验证的 nRMSE 偏高（0.1422 vs LightGBM 0.1225）

HRRR 三年重新抓取是正确的方向，因为：
1. 使用真正的 forecast-cycle 天气（HRRR issue_time + lead_time）替代 Open-Meteo assumed issue time
2. Stage7 链路已具备完整的 contract 校验体系
3. 三年覆盖能匹配主实验期 2020-2022

### 建议增加的实施步骤

```
0. (前置) 确认 NOAA GRIB DSWRF 2020-2021 可访问
1. (前置) 本地生成 2020/2021/2022 三年共 36 个月度 manifest
2. (前置) 提交 WIP hrrr.py 改动（_record_range_end 关键）
3. (前置) 重新打包 payload
4. 按计划执行三台 EC2 并行抓取
5. 每台完成后本地验证年份 contract
6. 三年 concat + 全量 contract
7. Stage7 用 HRRR 数据重新训练/评估
```

**总体评价**: 方案成熟，现有代码基础扎实。主要风险在数据源可用性（NOAA GRIB archive for 2020-2021）而非代码实现。建议先做前置步骤 0-3，再启动 EC2。

---

## HRRR 三年并行抽取执行 - 2026-05-03

**Agent**: Claude Code (deepseek-v4-pro)
**状态**: 全自动执行中，用户已就寝

### EC2 实例清单

| Instance ID | IP | Year | 用途 |
|---|---|---|---|
| i-07ec8acb16929f4c6 | 3.101.82.14 | 2022 | 原始实例（Probe 已过审） |
| i-02a4daad75b094878 | 18.145.155.0 | 2020 | 新实例 |
| i-003506e7961b8d034 | 18.145.64.245 | 2021 | 新实例 |

### 2022 Probe 审查结论（已完成）

- 10/10 门禁全部通过
- GHI 白天非零率: 97.7% (阈值 85%)
- 夏季 GHI 峰值: 1001 W/m² (阈值 500)
- DSWRF 溯源: 416 路径, 0 缺失
- Issue time 泄漏: 0 行
- 特征合理率: 100%
- 结论: **批准 Full Run**

### 2020/2021 Probe 执行

- 2020 Bucket: `new-energy-hrrr-916651624078-uswest1-y2020-20260503015726-14657`
- 2021 Bucket: `new-energy-hrrr-916651624078-uswest1-y2021-20260503015745-32726`
- 2020 CommandId: `46aa4a3d-cefc-4264-9c6b-417a87354102`
- 2021 CommandId: `7a543242-dbd9-42fb-9779-7ad50197c8e4`
- 起始时间: 2026-05-03 01:57 UTC

### 2021 Probe 结果 (02:15 UTC 完成)

- 10/10 门禁全部通过
- GHI 白天非零率: 97.6%, 夏季峰值: 971 W/m²
- DSWRF 溯源: 416 路径, 0 缺失
- Issue time 泄漏: 0 行, 特征合理率: 100%
- 结论: **批准 Full Run**

### 2020 Probe 状态

- 仍在执行中 (02:37 UTC, 已运行 ~40 分钟)
- 进程活跃: CPU 9.8%, 3 活跃网络连接, 已读 2.66GB, 写 343MB
- 2020 NOAA GRIB archive 响应较慢是主因，非代码问题

### 下一步（等 2020 probe 完成后）

1. 下载审查 2020 probe 结果
2. 三台统一发送 Full Run（-AllowFullRun）
3. 监控 Full Run 完成
4. 按年下载结果 + contract 校验
5. 三年 merge + 全量 contract

