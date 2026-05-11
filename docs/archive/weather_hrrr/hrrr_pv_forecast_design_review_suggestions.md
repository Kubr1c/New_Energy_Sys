# HRRR 预报驱动光伏功率预测链路设计审核建议

**日期**: 2026-05-03  
**审核对象**:

1. `session_2026-05-03_irradiance_decomp_and_prediction_design.md`
2. `2026-05-03-hrrr-pv-forecast-pipeline-design.md`

---

## 一、总体结论

两份文件的总体技术方向是合理的：

- 已先完成 GHI → DNI/DHI 辐射分解模块；
- 再以 HRRR 真预报替换 NSRDB oracle 天气特征；
- 最后通过 `history_only`、`NSRDB oracle`、`HRRR forecast` 三基线比较，量化真实预报对 PV 功率预测的影响。

主要问题不在总体路线，而在以下细节：

1. 多时次聚合边界需要更严格，防止数据泄露；
2. HRRR 覆盖年份与训练/测试切分之间存在潜在混杂；
3. `weather_missing=True` 的“回退”语义需要明确；
4. Step 1 特征数量前后不一致；
5. `clearsky_index`、`kt`、`DHI/GHI ratio` 等派生特征需要异常值保护；
6. DISC/Erbs 的选择应通过 PV 预测消融实验验证；
7. 评估指标应以白天 nRMSE 为主，而不是只看全时段 nRMSE。

---

## 二、关键问题与修改建议

### 1. 多时次聚合窗口存在边界歧义

#### 当前写法

文档中写：

```text
issue_time ∈ [T-48h, T-24h]
```

同时又写：

```text
若某时次 lead_time < 24h，则排除
```

这两种表述本质上都在限制日前预报，但如果实现时同时使用，容易出现边界不一致，例如：

- 是否包含刚好 `T-24h` 的预报？
- 是否包含 `F24`？
- 是否包含 `F48`？
- `issue_time` 和 `lead_time` 是否都以小时为单位精确对齐？

#### 风险

如果误用了 `lead_time < 24h` 的短临预报，模型会看到更接近真实天气的预报，测试结果会虚高。

#### 建议修改

统一写成基于 `valid_time` 和 `lead_time_hour` 的规则：

```text
For each target_time T, collect HRRR forecasts satisfying:
valid_time == T
24h <= lead_time_hour <= 48h
```

对应实现条件：

```python
target_time = valid_time
lead_time_hour = (valid_time - issue_time).total_seconds() / 3600

mask = (
    (df["valid_time"] == target_time)
    & (df["lead_time_hour"] >= 24)
    & (df["lead_time_hour"] <= 48)
)
```

#### 优先级

**最高。**

这是最应该先修正的地方。只要这里出现泄露，后续所有 nRMSE 结果都会失真。

---

### 2. 数据泄露风险应从“中风险”提高到“高风险”

#### 当前描述

设计规格中将“多时次聚合引入数据泄露”列为中风险。

#### 问题

日前预测任务的核心约束是：

```text
预测目标时间 T 时，只能使用 T-24h 或更早时刻已经可获得的信息。
```

如果使用了：

```text
lead_time < 24h
```

则等价于使用了更接近目标时间的天气预报，评估结果不再代表日前预测能力。

#### 建议增加强制测试

```python
assert all(feature_df["lead_time_hour"] >= 24)

assert all(
    feature_df["issue_time"]
    <= feature_df["target_time"] - pd.Timedelta(hours=24)
)

assert not feature_df.duplicated(["target_time"]).any()
```

如果未来扩展到多站点，则改为：

```python
assert not feature_df.duplicated(["site_id", "target_time"]).any()
```

#### 建议保留审计列

训练时可以不使用这些列，但特征表中建议保留：

```text
target_time
issue_time_min
issue_time_max
lead_time_min
lead_time_max
n_forecasts_used
weather_missing
```

这些字段用于排查：

- 是否用了非法 lead time；
- 某个目标时间用了多少条预报；
- 是否存在 HRRR 缺失；
- 聚合窗口是否正确。

#### 优先级

**最高。**

---

### 3. HRRR 数据范围与训练集划分存在潜在混杂

#### 当前情况

设计中提到 HRRR Stage7 数据范围为：

```text
2021–2022, F24–F44
```

同时风险表中写：

```text
HRRR 覆盖不全，2020 年用 history_only 特征
```

#### 问题

如果训练/验证/测试采用时间序列 70%/15%/15% 切分，而 HRRR 只覆盖 2021–2022，则不同年份的特征分布可能明显不同：

- 2020：天气特征缺失或回退；
- 2021–2022：HRRR 天气特征可用。

这样会导致模型同时学习两件事：

1. HRRR 天气特征对 PV 预测是否有用；
2. 某些年份是否有天气特征。

后者会污染对前者的判断。

#### 建议拆成两个评估口径

| 评估口径 | 用途 |
|---|---|
| HRRR-overlap only | 只在 HRRR 覆盖期内训练和测试，用于评估 HRRR 天气特征本身的增益 |
| Full-period with fallback | 全量时间评估，缺 HRRR 时回退 history_only，用于评估生产链路鲁棒性 |

#### 建议主实验

主实验应使用：

```text
HRRR-overlap only
```

也就是只在 HRRR 有覆盖的 2021–2022 时间段内比较：

```text
history_only vs HRRR forecast vs NSRDB oracle
```

这样才能相对纯粹地回答：

```text
HRRR 真预报到底比纯历史模型提升多少？
```

#### 优先级

**高。**

---

### 4. `weather_missing=True` 的“回退”语义需要明确

#### 当前写法

文档中写：

```text
若某目标时间无任何 HRRR 覆盖，该行标记为 weather_missing=True，模型回退到 history_only 特征
```

#### 问题

“回退到 history_only”有两种实现方式，含义不同。

---

#### 方案 A：单模型 + 缺失标记

做法：

- 使用一个 LightGBM 模型；
- HRRR 缺失时，天气特征设为 NaN；
- 加入 `weather_missing=True`；
- 让 LightGBM 自动学习缺失分支。

优点：

- 实现简单；
- 训练流程统一。

缺点：

- 严格来说，这不是完全的 history_only 回退；
- 模型结构仍然是 HRRR 模型，只是天气特征缺失。

---

#### 方案 B：双模型回退

做法：

- 有 HRRR 特征时，使用 `hrrr_forecast_model`；
- 无 HRRR 特征时，使用 `history_only_model`。

优点：

- 语义清楚；
- 真正实现 history_only fallback。

缺点：

- 训练、评估、部署逻辑更复杂；
- 需要分别维护两个模型。

---

#### 建议写法

建议在设计规格中明确：

```text
实验阶段采用方案 A：单 LightGBM 模型 + weather_missing flag。
生产回退阶段可扩展为方案 B：缺 HRRR 时调用 history_only 模型。
```

#### 优先级

**高。**

---

### 5. Step 1 特征数量前后不一致

#### 当前问题

设计文件中出现了不同数量：

- Step 1 标题写：`~75 特征`
- 特征表合计写：`~69 特征`
- 三基线表里 HRRR forecast 写：`~75 特征`

#### 风险

这不会影响模型本身，但会降低设计规格的准确性，后续实现者也不容易判断特征是否漏掉。

#### 建议统一

如果按表格中的数量计算：

```text
12 time
+ 38 historical power
+ 5 HRRR weather
+ 10 HRRR weather rolling
+ 3 HRRR derived
+ 1 weather_valid_flag
= 69 features
```

建议写成：

```text
Step 1 expected feature count: 69
```

如果后续还计划加入额外特征，例如：

- solar elevation；
- zenith；
- weather lag；
- interaction features；

则建议写成：

```text
Step 1 expected feature count: about 69–75, exact count determined by the implemented feature list.
```

但不要在同一文件中同时无说明地出现 `69` 和 `75`。

#### 优先级

**中。**

---

### 6. `clearsky_index` 与 `kt` 定义需要区分

#### 当前写法

设计中包含：

```text
clearsky_index = GHI / clearsky_ghi(zenith)
DHI/GHI ratio
kt
```

#### 问题

`clearsky_index` 和 `kt` 容易混用，但它们不是同一个指标。

建议定义：

```text
clearsky_index = GHI / GHI_clearsky
kt = GHI / (I0 * cos(zenith))
```

其中：

- `GHI_clearsky` 是晴空模型估计的地表晴空水平总辐照度；
- `I0 * cos(zenith)` 是大气层外水平面辐照度。

#### 建议加入异常保护

```python
if ghi <= 0 or cos_zenith <= 0:
    clearsky_index = 0
    kt = 0
```

或者使用向量化形式：

```python
clearsky_index = np.where(
    clearsky_ghi > 20,
    ghi / clearsky_ghi,
    np.nan
)

kt = np.where(
    extraterrestrial_horizontal > 20,
    ghi / extraterrestrial_horizontal,
    np.nan
)
```

建议再加 clip：

```python
clearsky_index = np.clip(clearsky_index, 0, 2)
kt = np.clip(kt, 0, 1.2)
```

#### 优先级

**高。**

---

### 7. `DHI/GHI ratio` 需要低 GHI 保护

#### 当前问题

`DHI/GHI ratio` 在 GHI 很小时容易爆炸，例如：

```text
GHI = 0.5
DHI = 10
DHI/GHI = 20
```

这类值对树模型会产生异常分裂点。

#### 建议实现

方案一：

```python
dhi_ghi_ratio = np.where(
    ghi > 20,
    dhi / ghi,
    np.nan
)
```

方案二：

```python
dhi_ghi_ratio = dhi / np.maximum(ghi, 1.0)
dhi_ghi_ratio = np.clip(dhi_ghi_ratio, 0, 1.5)
```

更推荐方案一，让 LightGBM 自己处理夜间或低辐照条件下的缺失值。

#### 优先级

**高。**

---

### 8. 24h rolling 特征需要明确是否因果

#### 当前写法

设计中写：

```text
+ 24h rolling mean/std
```

但没有说明 rolling 方向。

#### 问题

如果 rolling 使用 centered window，例如：

```text
T-12h : T+12h
```

则会使用目标时间之后的天气信息。

这在某些业务设定下不一定绝对错误，因为日前预测时可能已经拿到了未来 24–48h 的完整天气预报轨迹。但如果你的预测任务定义是“逐小时独立预测 T”，则这会产生未来信息争议。

#### 建议明确

推荐写成：

```text
All rolling weather features are computed causally over valid_time, using values <= T only.
```

对应实现：

```python
rolling_24h_mean(T) = mean(weather_forecast[T-23h : T])
rolling_24h_std(T) = std(weather_forecast[T-23h : T])
```

不要默认使用：

```python
weather_forecast[T-12h : T+12h]
```

除非你明确声明业务场景是：

```text
At forecast issue time, the full future weather trajectory is available and may be used.
```

#### 优先级

**中高。**

---

### 9. DISC 默认合理，但应保留 Erbs / Hybrid 消融

#### 当前结论

会话记录中给出的验证结果是：

- DISC 的 DNI RMSE 更低；
- Erbs 的 DHI RMSE 更低；
- 因为 PV 预测更依赖 DNI，所以默认使用 DISC。

这个判断是合理的，但仍需要通过 PV 预测实验验证。

#### 问题

PV 模型的最终目标不是单独预测 DNI 或 DHI，而是预测功率。DNI/DHI 分解误差对功率预测的影响不一定完全等同于 DNI/DHI 自身 RMSE。

此外，既然已有结论：

```text
DISC DNI 更好
Erbs DHI 更好
```

那么值得测试一个混合方案：

```text
DNI from DISC
DHI from Erbs
```

#### 建议增加消融实验

| 实验 | GHI | DNI | DHI |
|---|---|---|---|
| HRRR-GHI-only | HRRR | 不使用 | 不使用 |
| HRRR-Erbs | HRRR | Erbs DNI | Erbs DHI |
| HRRR-DISC | HRRR | DISC DNI | DISC DHI |
| HRRR-Hybrid | HRRR | DISC DNI | Erbs DHI |

#### 优先级

**高。**

---

### 10. 成功标准需要更细化

#### 当前成功标准

```text
HRRR forecast nRMSE < history_only nRMSE (0.1225)
```

#### 问题

这个标准太粗，只能说明 HRRR 是否有提升，但不能说明提升幅度是否有工程意义。

#### 建议增加 gap closure

定义：

```text
gap_closure = (nRMSE_history - nRMSE_HRRR) / (nRMSE_history - nRMSE_oracle)
```

示例：

```text
history_only = 0.1225
oracle = 0.0784
HRRR = 0.1050

gap_closure = (0.1225 - 0.1050) / (0.1225 - 0.0784)
            ≈ 39.7%
```

含义：

```text
HRRR forecast 缩小了 history_only 与 oracle 之间约 39.7% 的差距。
```

#### 建议分级

| 等级 | 标准 |
|---|---|
| 最低成功 | 链路跑通，HRRR-overlap 测试集无泄露 |
| 有效成功 | HRRR forecast 全时段 nRMSE < history_only |
| 强成功 | HRRR forecast 白天 nRMSE < history_only 白天 nRMSE |
| 接近上限 | gap_closure >= 30% |

#### 优先级

**中高。**

---

## 三、建议补充的实验设计

### 实验 A：GHI-only vs GHI+DNI+DHI

#### 目的

判断 GHI 分解是否真的提升 PV 预测，而不是增加噪声。

#### 建议实验

```text
A1. history_only
A2. HRRR-GHI-only
A3. HRRR-GHI+DNI+DHI
A4. HRRR-GHI+DNI+DHI+temperature+cloud_cover
```

#### 判断逻辑

如果：

```text
HRRR-GHI-only ≈ HRRR-GHI+DNI+DHI
```

说明 DNI/DHI 分解的边际贡献有限。

如果：

```text
HRRR-GHI+DNI+DHI 明显优于 HRRR-GHI-only
```

说明分解模块对 PV 预测确实有效。

---

### 实验 B：lead_time 聚合方式消融

#### 当前方案

```text
权重 ∝ 1 / lead_time
```

#### 问题

这个假设不一定最优。HRRR 不同 lead time 的误差不一定简单服从 `1 / lead_time` 关系。

#### 建议比较

| 聚合方式 | 含义 |
|---|---|
| nearest lead only | 只取最接近 24h 的日前预报 |
| simple mean | F24–F48 等权平均 |
| inverse lead | 权重 ∝ 1 / lead_time |
| inverse squared lead | 权重 ∝ 1 / lead_time² |
| latest available | 只取满足日前条件的最新 issue_time |

建议至少实现前三个：

```text
nearest F24/F25
mean F24-F48
weighted mean F24-F48
```

---

### 实验 C：白天指标作为主指标

#### 问题

PV 功率预测中，全时段 nRMSE 容易被夜间零功率稀释。

#### 建议

将主指标改为：

```text
Primary metric: daytime nRMSE
Secondary metric: all-hour nRMSE
```

#### 白天定义建议

不要只用：

```text
actual_power > 0
```

因为设备停机、限电、清晨/傍晚低功率可能混在一起。

建议使用：

```text
daytime = clear_sky_ghi > 20 W/m²
```

或者：

```text
daytime = solar_elevation > 5°
```

如果两者都有，推荐：

```text
daytime = clear_sky_ghi > 20 W/m² and solar_elevation > 5°
```

---

### 实验 D：按天气场景分组评估

#### 目的

判断 HRRR 预报在哪类天气下真正提升 PV 预测。

#### 建议分组方式

按云量：

```text
clear: cloud_cover < 20%
partly_cloudy: 20% <= cloud_cover < 80%
cloudy: cloud_cover >= 80%
```

或按 clear-sky index：

```text
clear
variable
overcast
```

#### 建议输出

每个天气组分别输出：

```text
nRMSE
MAE
Bias
样本数
```

这样可以判断：

- 晴天是否主要由太阳几何与历史功率决定；
- 多云天气是否 HRRR 带来明显增益；
- 阴天是否 DNI/DHI 分解反而增加噪声。

---

## 四、建议修改原设计文档的具体文本

### 1. 数据流部分

建议将：

```text
issue_time ∈ [T-48h, T-24h]
```

改为：

```text
For each target_time T, collect HRRR forecasts satisfying:
valid_time == T
24h <= lead_time_hour <= 48h
```

并明确：

```text
lead_time_hour = valid_time - issue_time
```

---

### 2. 多时次聚合规则部分

建议补充：

```text
The feature generation process must preserve audit columns:
target_time, issue_time_min, issue_time_max, lead_time_min, lead_time_max,
n_forecasts_used, weather_missing.
These columns are excluded from model training unless explicitly stated.
```

---

### 3. 特征工程部分

建议补充：

```text
All rolling weather features are computed causally over valid_time,
using values with valid_time <= T only.
```

并补充异常值保护：

```text
dhi_ghi_ratio is set to NaN when GHI <= 20 W/m².
clearsky_index is clipped to [0, 2].
kt is clipped to [0, 1.2].
Nighttime records are assigned zero or NaN for irradiance ratio features,
depending on LightGBM missing-value handling.
```

---

### 4. 评估部分

建议补充：

```text
Primary evaluation is conducted on the HRRR-overlap period only.
Full-period evaluation with history_only fallback is reported separately.
```

---

### 5. 基线部分

建议将三基线扩展为：

```text
A. history_only
B. NSRDB oracle
C1. HRRR-GHI-only
C2. HRRR-Erbs
C3. HRRR-DISC
C4. HRRR-Hybrid: DISC DNI + Erbs DHI
```

---

### 6. 成功标准部分

建议改为：

```text
Primary metric: daytime nRMSE.
Secondary metric: all-hour nRMSE.

Minimum success:
- Full HRRR → feature → prediction → evaluation pipeline runs without errors.
- No lead_time < 24h is used in any HRRR feature.

Effective success:
- HRRR forecast daytime nRMSE < history_only daytime nRMSE.

Strong success:
- HRRR forecast closes at least 30% of the gap between history_only and NSRDB oracle.

gap_closure = (nRMSE_history - nRMSE_HRRR) / (nRMSE_history - nRMSE_oracle)
```

---

## 五、建议新增测试清单

### 1. 聚合窗口测试

```python
def test_no_forecast_with_lead_time_less_than_24h_is_used():
    assert all(feature_df["lead_time_hour"] >= 24)
```

```python
def test_no_forecast_after_target_minus_24h_is_used():
    assert all(
        feature_df["issue_time"]
        <= feature_df["target_time"] - pd.Timedelta(hours=24)
    )
```

---

### 2. 重复目标时间测试

```python
def test_one_feature_row_per_target_time():
    assert not feature_df.duplicated(["target_time"]).any()
```

多站点版本：

```python
def test_one_feature_row_per_site_and_target_time():
    assert not feature_df.duplicated(["site_id", "target_time"]).any()
```

---

### 3. 审计字段测试

```python
def test_audit_columns_are_present():
    required_cols = {
        "target_time",
        "lead_time_min",
        "lead_time_max",
        "n_forecasts_used",
        "weather_missing",
    }
    assert required_cols.issubset(feature_df.columns)
```

---

### 4. 比率特征异常值测试

```python
def test_dhi_ghi_ratio_is_safe():
    assert not np.isinf(feature_df["dhi_ghi_ratio"]).any()
    assert feature_df["dhi_ghi_ratio"].dropna().between(0, 1.5).all()
```

---

### 5. clear-sky 特征异常值测试

```python
def test_clearsky_index_range():
    assert not np.isinf(feature_df["clearsky_index"]).any()
    assert feature_df["clearsky_index"].dropna().between(0, 2).all()
```

---

### 6. kt 特征异常值测试

```python
def test_kt_range():
    assert not np.isinf(feature_df["kt"]).any()
    assert feature_df["kt"].dropna().between(0, 1.2).all()
```

---

## 六、建议优先级排序

| 优先级 | 建议项 | 原因 |
|---|---|---|
| P0 | 严格定义 `valid_time` / `issue_time` / `lead_time_hour` 过滤规则 | 防止数据泄露 |
| P0 | 增加 `lead_time >= 24h` 单元测试 | 防止评估结果虚高 |
| P1 | 拆分 HRRR-overlap only 与 full-period fallback 实验 | 避免年份覆盖问题污染结论 |
| P1 | 明确 `weather_missing=True` 的回退实现 | 避免实现语义不一致 |
| P1 | 增加 GHI-only / Erbs / DISC / Hybrid 消融 | 判断分解模块对 PV 预测的真实贡献 |
| P1 | 将白天 nRMSE 设为主指标 | 避免夜间零功率稀释误差 |
| P2 | 统一 Step 1 特征数量 | 提高规格文档准确性 |
| P2 | 增加 ratio / clearsky / kt 异常值保护 | 提高特征稳定性 |
| P2 | 增加 lead_time 聚合方式消融 | 验证 `1 / lead_time` 加权是否合理 |
| P3 | 增加天气场景分组评估 | 提高误差解释能力 |

---

## 七、推荐的下一步执行顺序

### Step 1：先修正设计规格

优先修改：

1. 多时次聚合规则；
2. 数据泄露测试；
3. HRRR-overlap only 主实验；
4. 白天 nRMSE 主指标；
5. GHI-only / Erbs / DISC / Hybrid 消融实验。

---

### Step 2：实现 `hrrr_feature_aligner.py`

实现时建议先输出完整审计表：

```text
target_time
issue_time_min
issue_time_max
lead_time_min
lead_time_max
n_forecasts_used
weather_missing
ghi
dni
dhi
temperature
cloud_cover
```

先不要急着训练模型，先人工检查：

- 每个目标时间是否只有一行；
- lead_time 是否都在 24–48h；
- 是否存在异常 GHI/DNI/DHI；
- HRRR 缺失比例是多少。

---

### Step 3：先跑最小实验

推荐最小实验矩阵：

```text
A. history_only
B. HRRR-GHI-only
C. HRRR-DISC
D. NSRDB oracle
```

如果 C 不优于 B，则说明 DNI/DHI 分解对 PV 预测没有明显收益，或者分解误差过大。

如果 C 优于 B，再继续跑：

```text
E. HRRR-Erbs
F. HRRR-Hybrid
```

---

### Step 4：再扩展 lead_time 聚合消融

初始可比较：

```text
nearest F24/F25
simple mean F24-F48
weighted mean F24-F48
```

如果差异很小，保留最简单方案即可。

---

## 八、最终判断

当前两份文件已经具备较完整的工程设计框架，但建议在正式实现前先补强以下部分：

1. **把多时次聚合规则写成严格的 `24h <= lead_time_hour <= 48h`。**
2. **把数据泄露风险作为最高优先级处理。**
3. **将 HRRR 覆盖期实验和全量回退实验分开。**
4. **明确 `weather_missing=True` 是单模型缺失处理还是双模型回退。**
5. **增加 GHI-only、Erbs、DISC、Hybrid 消融实验。**
6. **把白天 nRMSE 作为主指标。**
7. **给所有 ratio 和 clear-sky 派生特征加异常值保护。**

最关键的一句话是：

```text
先验证 HRRR 特征是否严格无泄露，再谈模型精度。
```

只要聚合窗口或 rolling 特征存在泄露，后续所有模型指标都会被系统性高估。
