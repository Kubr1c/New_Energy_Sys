// Central presentation labels for the defense/demo UI.
//
// The backend must keep experiment IDs, feature-set IDs, scenario IDs, and task
// command IDs stable for reproducibility. The frontend, however, should show
// domain language. Keep the five concepts separated so a feature set is never
// presented as a model and a scenario is never presented as an experiment.

const MODEL_LABELS = {
  lightgbm: 'LightGBM',
  lightgbm_tuned: 'LightGBM 优化版',
  xgboost: 'XGBoost',
  catboost: 'CatBoost',
  extra_trees: '极端随机树',
  random_forest: '随机森林',
  ridge: '岭回归',
  elastic_net: '弹性网络',
  persistence: '持续性基准模型',
  persistence_baseline: '持续性基准模型',
  tcn: 'TCN',
  dlinear: 'DLinear',
  cnn_lstm: 'CNN-LSTM',
  attention_lstm: 'Attention-LSTM',
}

const FEATURE_SET_LABELS = {
  history_only: '历史特征集',
  full_features: '完整特征集',
  full_features_without_target_plus: '完整特征集',
  weather_history_target_aligned: '天气与历史特征集',
  persistence_baseline: '持续性基准特征集',
  csi_enhanced: '晴空指数增强特征集',
  ramp_enhanced: '光伏爬坡增强特征集',
  quantile_features: '分位数预测特征集',
}

const EXPERIMENT_LABELS = {
  stage5: '完整特征预测方案',
  e1: '完整特征预测方案',
  full_features_163: '完整特征预测方案',
  solar_ramp_171: '光伏爬坡增强预测方案',
  C0_171feat: '晴空指数增强预测方案',
  q1_p10: '分位数预测方案 P10',
  q1_p50: '分位数预测方案 P50',
  q1_p90: '分位数预测方案 P90',
}

const SCENARIO_LABELS = {
  no_storage: '无储能基准',
  stage10_fixed_threshold: '固定阈值调度方案',
  stage11_best_threshold_q40_q95: '离线阈值对照方案',
  rolling_optimization: '滚动优化调度方案',
  rolling_with_rainflow_degradation: '考虑电池退化的滚动优化方案',
  synthetic_scenario: '合成电价场景',
  solar_duck_curve: '光伏鸭形曲线场景',
  'Solar duck-curve proxy': '光伏鸭形曲线场景',
  flat_price: '平稳电价场景',
  evening_peak: '晚高峰电价场景',
  clear: '晴天场景',
  mixed: '多云场景',
  overcast: '阴天场景',
  smooth: '平滑运行调度',
  economic: '经济优先调度',
  none: '无储能运行',
}

const FEATURE_FIELD_LABELS = {
  hour_sin: '小时周期特征 sin',
  hour_cos: '小时周期特征 cos',
  dayofyear_sin: '年内日期周期特征 sin',
  dayofyear_cos: '年内日期周期特征 cos',
  month_sin: '月份周期特征 sin',
  month_cos: '月份周期特征 cos',
  solar_elevation_deg: '太阳高度角',
  solar_azimuth_deg: '太阳方位角',
  ghi_wm2: '总水平辐照度',
  dni_wm2: '直接法向辐照度',
  dhi_wm2: '散射水平辐照度',
  temperature_c: '环境温度',
  relative_humidity_pct: '相对湿度',
  wind_speed_ms: '风速',
  cloud_cover_pct: '云量',
  pv_power_kw: '光伏实际功率',
  target_pv_power_t_plus_24h: '24 小时后光伏功率',
  lag_1h_pv_power_kw: '前 1 小时光伏功率',
  lag_24h_pv_power_kw: '前 24 小时光伏功率',
  rolling_24h_mean_kw: '24 小时滚动平均功率',
}

const REPORT_LABELS = {
  stage4: 'LightGBM 基线预测报告',
  stage5: 'LightGBM 优化预测报告',
  stage6: 'TCN 序列预测报告',
  stage8: '表格模型对比报告',
  stage9: '主预测模型报告',
  stage10: '固定阈值调度报告',
  stage11: '阈值策略评价报告',
  stage12: '滚动优化调度报告',
  stage13: '策略评价报告',
  stage14: '深度学习预测对比报告',
  stage15: '储能配置优选报告',
  stage17: '电池退化成本评估报告',
  stage18: '参考电站仿真报告',
  stage20: '神经调度基线报告',
  stage20b: '两阶段神经调度策略报告',
  stage21: '天气与电价场景调度报告',
}

function normalizeKey(value) {
  return String(value ?? '').trim()
}

function lowerKey(value) {
  return normalizeKey(value).toLowerCase()
}

export function modelLabel(value) {
  const key = lowerKey(value)
  if (!key) return '模型数据缺失'
  return MODEL_LABELS[key] || normalizeKey(value)
}

export function featureSetLabel(value) {
  const raw = normalizeKey(value)
  if (!raw) return '特征集数据缺失'
  return FEATURE_SET_LABELS[raw] || FEATURE_SET_LABELS[lowerKey(raw)] || raw
}

export function experimentLabel(experiment) {
  if (!experiment) return '预测方案数据缺失'
  if (typeof experiment === 'object') {
    const id = normalizeKey(experiment.id || experiment.experiment)
    if (EXPERIMENT_LABELS[id]) return EXPERIMENT_LABELS[id]
    const model = modelLabel(experiment.model_name || experiment.model)
    const featureSet = featureSetLabel(experiment.feature_set)
    if (model !== '模型数据缺失' && featureSet !== '特征集数据缺失') {
      return `${model} ${featureSet}预测方案`
    }
    return id || '预测方案数据缺失'
  }
  const key = normalizeKey(experiment)
  return EXPERIMENT_LABELS[key] || key
}

export function scenarioLabel(value) {
  const raw = normalizeKey(value)
  if (!raw) return '场景数据缺失'
  return SCENARIO_LABELS[raw] || SCENARIO_LABELS[lowerKey(raw)] || raw
}

export function configLabel(value, row = {}) {
  const capacity = Number(row.capacity_kwh ?? row.battery_energy_kwh)
  const power = Number(row.max_discharge_kw ?? row.battery_power_kw ?? row.power_kw)
  if (Number.isFinite(capacity) && Number.isFinite(power)) {
    return `${formatEnergy(capacity)} / ${formatPower(power)} 储能配置`
  }

  const raw = normalizeKey(value)
  const match = raw.match(/cap([\d.]+)_pow([\d.]+)_obj(\d+)/i)
  if (match) return `容量 ${match[1]} 倍 / 功率 ${match[2]} 倍储能配置`
  return raw || '储能配置数据缺失'
}

export function featureFieldLabel(value) {
  const raw = normalizeKey(value)
  return FEATURE_FIELD_LABELS[raw] || raw || '字段数据缺失'
}

export function reportLabel(stageId, name) {
  const id = lowerKey(stageId)
  if (REPORT_LABELS[id]) return REPORT_LABELS[id]
  const rawName = normalizeKey(name)
  return rawName ? rawName.replace(/_/g, ' ') : '报告'
}

export function taskLabel(commandId) {
  const labels = {
    train_baseline: '训练主预测模型',
    compare_tabular: '运行模型对比',
    run_inference: '更新预测曲线',
    run_dispatch: '生成调度方案',
    run_strategy: '运行策略评价',
    run_rolling: '生成滚动优化调度方案',
    run_governance: '更新策略评价',
    run_sensitivity: '运行配置优选',
  }
  return labels[commandId] || '运行系统任务'
}

export function taskType(commandId) {
  if (['train_baseline', 'compare_tabular'].includes(commandId)) return 'training'
  if (commandId === 'run_inference') return 'prediction'
  if (['run_dispatch', 'run_strategy', 'run_rolling', 'run_governance', 'run_sensitivity'].includes(commandId)) return 'dispatch'
  return 'other'
}

function formatEnergy(value) {
  return value >= 1000 ? `${(value / 1000).toFixed(1)} MWh` : `${value.toFixed(0)} kWh`
}

function formatPower(value) {
  return value >= 1000 ? `${(value / 1000).toFixed(1)} MW` : `${value.toFixed(0)} kW`
}
