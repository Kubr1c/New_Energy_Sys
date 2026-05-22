<template>
  <div class="dispatch">
    <PageState v-if="loading" type="loading" title="正在加载调度分析" message="正在读取策略评价、参考电站仿真和储能运行指标。" />
    <PageState v-else-if="error" type="error" title="调度分析加载失败" :message="error.message" retryable @retry="loadScorecard" />
    <PageState
      v-else-if="!scorecard.length && !referenceAvailable && !weatherPriceAvailable"
      type="empty"
      title="暂无调度策略数据"
      message="当前接口没有返回可展示的策略评价或参考电站仿真结果。"
      retryable
      @retry="loadScorecard"
    />
    <template v-else>

      <el-tabs v-model="activeTab" class="dispatch-tabs">
        <el-tab-pane label="调度结论" name="conclusion" lazy>
          <section v-if="showcaseScenarios.length" class="conclusion-stack">
            <MetricGrid :items="showcaseKpiCards" min-width="200px" @item-click="openShowcaseDetail" />

            <ChartCard title="情景收益对比">
              <v-chart class="showcase-chart" :option="showcaseNetRevenueChartOption" theme="dark-tech" autoresize />
            </ChartCard>

            <ChartCard title="情景展示表">
              <el-table :data="showcaseScenarios" size="small" stripe class="showcase-table">
                <el-table-column prop="scenario_name" label="情景名称" min-width="160" show-overflow-tooltip>
                  <template #default="{ row }">{{ displayScenarioName(row.scenario_name) }}</template>
                </el-table-column>
                <el-table-column prop="scenario_type" label="情景类型" width="130">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.scenario_type === 'baseline' ? 'info' : 'primary'">{{ scenarioTypeLabel(row.scenario_type) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="gross_incremental_revenue_eur" label="毛增量收益" width="120" :formatter="fmtEur" sortable />
                <el-table-column prop="degradation_cost_eur" label="退化成本" width="120" :formatter="fmtEur" sortable />
                <el-table-column prop="additional_revenue_eur" label="额外收益" width="110" :formatter="fmtEur" sortable />
                <el-table-column prop="net_incremental_revenue_eur" label="相比无储能收益" width="150" sortable>
                  <template #default="{ row }">
                    <strong :style="{ color: Number(row.net_incremental_revenue_eur) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }">
                      {{ fmtCurrency(row.net_incremental_revenue_eur) }}
                    </strong>
                  </template>
                </el-table-column>
                <el-table-column prop="soh_end" label="SOH 终点" width="100" :formatter="fmtPercent" sortable />
                <el-table-column prop="equivalent_full_cycles" label="等效满循环" width="110" :formatter="fmtNumber" sortable />
                <el-table-column type="expand" width="40">
                  <template #default="{ row }">
                    <div class="showcase-detail">
                      <span><strong>调度方式：</strong>{{ strategyDisplayLabel(row.strategy_label) }}</span>
                      <span><strong>情景解释：</strong>{{ scenarioExplanation(row) }}</span>
                      <span><strong>更换成本：</strong>{{ formatYuanPerUnitFromEur(row.replacement_cost_eur_per_kwh, 'kWh') }}</span>
                      <span><strong>循环寿命倍数：</strong>{{ row.cycle_life_multiplier }}×</span>
                      <span><strong>年历衰减：</strong>{{ row.calendar_fade_rate }}/年</span>
                      <span><strong>容量价值：</strong>{{ formatYuanPerUnitFromEur(row.capacity_value_eur_per_kw_year, 'kW·年') }}</span>
                      <span><strong>约束通过：</strong>{{ row.constraints_passed ? '是' : '否' }}</span>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
              <div class="scenario-explain-list">
                <h4>情景说明</h4>
                <div v-for="row in showcaseScenarios" :key="row.scenario_name" class="scenario-explain-item">
                  <strong>{{ displayScenarioName(row.scenario_name) }}</strong>
                  <p>{{ scenarioExplanation(row) }}</p>
                  <small>{{ scenarioBoundaryText(row) }}</small>
                </div>
              </div>
            </ChartCard>

            <el-dialog v-model="showcaseDetailVisible" title="调度结论详情" width="920px" class="showcase-dialog">
              <el-tabs v-model="showcaseDetailTab">
                <el-tab-pane label="正净增量情景" name="positive">
                  <el-table :data="positiveShowcaseRows" size="small" stripe max-height="440">
                    <el-table-column prop="scenario_name" label="情景名称" min-width="170" show-overflow-tooltip>
                      <template #default="{ row }">{{ displayScenarioName(row.scenario_name) }}</template>
                    </el-table-column>
                    <el-table-column prop="typeLabel" label="情景类型" width="130" />
                    <el-table-column prop="net_incremental_revenue_eur" label="相比无储能收益" width="150" :formatter="fmtEur" sortable />
                    <el-table-column prop="gross_incremental_revenue_eur" label="毛增量收益" width="130" :formatter="fmtEur" sortable />
                    <el-table-column prop="degradation_cost_eur" label="退化成本" width="120" :formatter="fmtEur" sortable />
                    <el-table-column prop="soh_end" label="SOH" width="90" :formatter="fmtPercent" />
                  </el-table>
                </el-tab-pane>
                <el-tab-pane label="情景类型统计" name="types">
                  <el-table :data="showcaseTypeRows" size="small" stripe max-height="440">
                    <el-table-column prop="typeLabel" label="情景类型" min-width="150" />
                    <el-table-column prop="count" label="情景数量" width="100" sortable />
                    <el-table-column prop="positiveCount" label="正净增量数" width="120" sortable />
                    <el-table-column prop="bestNetEur" label="最高收益变化" width="150" :formatter="fmtEur" sortable />
                    <el-table-column prop="averageNetEur" label="平均收益变化" width="150" :formatter="fmtEur" sortable />
                  </el-table>
                </el-tab-pane>
              </el-tabs>
            </el-dialog>
          </section>
          <PageState
            v-else
            type="empty"
            title="暂无 Stage23 调度展示数据"
            message="请运行 stage23_scenario_dispatch_showcase 生成展示数据。数据缺失时仍可在天气驱动实验台中进行交互式调度仿真。"
          />
        </el-tab-pane>

        <el-tab-pane label="天气驱动实验台" name="weather-price" lazy>
          <section v-if="weatherPriceAvailable" class="experiment-stack">
            <div class="experiment-hero glass-panel compact-hero">
              <div>
                <span class="kicker">实时天气预报驱动的储能优化调度实验台</span>
                <h2>参考电站天气联动调度演示</h2>
                <p>{{ experimentBoundaryText }}</p>
              </div>
            </div>

            <div v-if="experimentRunError" class="experiment-error glass-panel">
              <strong>实时天气预报调度失败</strong>
              <p>{{ experimentRunError.message }}</p>
              <small v-if="experimentRunError.suggestedAction">建议：{{ experimentRunError.suggestedAction }}</small>
              <small v-if="experimentRunError.errorCode">错误码：{{ experimentRunError.errorCode }}</small>
            </div>

            <section class="control-panel glass-panel">
              <div class="control-header">
                <div class="section-title">
                  <span>调度控制</span>
                  <h3>调度控制</h3>
                  <p>{{ currentStorageSummary }}</p>
                </div>
                <div class="run-state inline-state" :class="{ running: isRunningExperiment }">
                  <span>运行状态</span>
                  <strong>{{ experimentStatusText }}</strong>
                  <small>{{ lastRunTimeText }}</small>
                </div>
              </div>
              <div class="control-grid">
                <label class="control-field">
                  <span>调度日期</span>
                  <el-date-picker v-model="experimentForm.dispatchDate" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" />
                </label>
                <label class="control-field">
                  <span>调度时段</span>
                  <el-radio-group v-model="experimentForm.horizonHours" size="small">
                    <el-radio-button :value="24">24h</el-radio-button>
                    <el-radio-button :value="48">48h</el-radio-button>
                    <el-radio-button :value="72">72h</el-radio-button>
                  </el-radio-group>
                </label>
                <label class="control-field">
                  <span>天气场景</span>
                  <el-select v-model="experimentForm.weatherScenario" @change="runExperiment">
                    <el-option v-for="item in weatherScenarioOptions" :key="item.value" :label="item.label" :value="item.value" />
                  </el-select>
                </label>
                <label class="control-field">
                  <span>电价场景</span>
                  <el-select v-model="experimentForm.priceScenario">
                    <el-option v-for="scenario in priceScenarios" :key="scenario.id" :label="scenario.label" :value="scenario.id" />
                  </el-select>
                </label>
                <label class="control-field">
                  <span>优化目标</span>
                  <el-select v-model="experimentForm.objective">
                    <el-option v-for="item in objectiveOptions" :key="item.value" :label="item.label" :value="item.value" />
                  </el-select>
                </label>
                <label class="control-field">
                  <span>优化算法</span>
                  <el-select v-model="experimentForm.algorithm">
                    <el-option v-for="item in algorithmOptions" :key="item.value" :label="item.label" :value="item.value" />
                  </el-select>
                </label>
              </div>

              <div class="storage-grid">
                <label class="control-field">
                  <span>储能容量（kWh）</span>
                  <el-input-number v-model="experimentForm.batteryEnergyKwh" :min="500" :max="8000" :step="250" controls-position="right">
                    <template #suffix>kWh</template>
                  </el-input-number>
                </label>
                <label class="control-field">
                  <span>储能功率（kW）</span>
                  <el-input-number v-model="experimentForm.batteryPowerKw" :min="200" :max="5000" :step="100" controls-position="right">
                    <template #suffix>kW</template>
                  </el-input-number>
                </label>
                <label class="control-field">
                  <span>充电效率</span>
                  <el-input-number v-model="experimentForm.chargeEfficiency" :min="0.7" :max="0.99" :step="0.01" controls-position="right" />
                </label>
                <label class="control-field">
                  <span>放电效率</span>
                  <el-input-number v-model="experimentForm.dischargeEfficiency" :min="0.7" :max="0.99" :step="0.01" controls-position="right" />
                </label>
                <label class="control-field">
                  <span>初始 SOC</span>
                  <el-input-number v-model="experimentForm.initialSoc" :min="0.05" :max="0.95" :step="0.05" controls-position="right" />
                </label>
                <label class="control-field">
                  <span>SOC 下限</span>
                  <el-input-number v-model="experimentForm.socMin" :min="0.05" :max="0.8" :step="0.05" controls-position="right" />
                </label>
                <label class="control-field">
                  <span>SOC 上限</span>
                  <el-input-number v-model="experimentForm.socMax" :min="0.2" :max="0.98" :step="0.05" controls-position="right" />
                </label>
              </div>

              <div class="action-row">
                <el-button type="primary" :icon="VideoPlay" :loading="isRunningExperiment" @click="runExperiment">执行调度</el-button>
                <el-button :icon="Refresh" @click="resetExperiment">恢复默认</el-button>
                <el-dropdown @command="exportExperimentResult">
                  <el-button :icon="Download">导出结果</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="json">完整 JSON</el-dropdown-item>
                      <el-dropdown-item command="csv" :disabled="!backendExperiment?.run_id">时序 CSV</el-dropdown-item>
                      <el-dropdown-item command="summary">答辩摘要</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </section>

            <div class="experiment-main">
              <div class="experiment-left">
                <ChartCard title="调度结果">
                  <template #actions>
                    <el-tabs v-model="experimentResultTab" class="mini-tabs">
                      <el-tab-pane label="功率曲线" name="power" />
                      <el-tab-pane label="SOC" name="soc" />
                      <el-tab-pane label="收益对比" name="price" />
                      <el-tab-pane label="方案对比" name="compare" />
                    </el-tabs>
                  </template>
                  <p class="tab-description">{{ resultTabDescription }}</p>
                  <v-chart v-if="experimentResultTab === 'power'" class="chart large-chart" :option="powerDispatchOption" theme="dark-tech" autoresize @click="handleExperimentChartClick" />
                  <p v-if="experimentResultTab === 'power'" class="chart-caption">储能充电会降低并网功率，放电会提高并网功率。</p>
                  <v-chart v-else-if="experimentResultTab === 'soc'" class="chart large-chart" :option="socCurveOption" theme="dark-tech" autoresize @click="handleExperimentChartClick" />
                  <v-chart v-else-if="experimentResultTab === 'price'" class="chart large-chart" :option="priceRevenueOption" theme="dark-tech" autoresize @click="handleExperimentChartClick" />
                  <v-chart v-else class="chart large-chart" :option="comparisonOption" theme="dark-tech" autoresize />
                </ChartCard>

                <section class="weather-input-grid">
                  <div class="weather-summary glass-card">
                    <span class="kicker">选中调度时刻天气预报</span>
                    <h3>{{ weatherScenarioLabel(appliedExperiment.weatherScenario) }}</h3>
                    <div class="weather-now-grid">
                      <div><span>温度</span><strong>{{ formatTemperature(selectedMoment.temperatureC) }}</strong></div>
                      <div><span>云量</span><strong>{{ formatPercentWhole(selectedMoment.cloudCoverPct) }}</strong></div>
                      <div><span>辐照度</span><strong>{{ formatIrradiance(selectedMoment.ghiWm2) }}</strong></div>
                      <div><span>风速</span><strong>{{ formatWind(selectedMoment.windSpeedMs) }}</strong></div>
                      <div><span>湿度</span><strong>{{ formatPercentWhole(selectedMoment.humidityPct) }}</strong></div>
                    </div>
                  </div>
                  <ChartCard title="天气趋势">
                    <v-chart class="chart input-chart" :option="weatherTrendOption" theme="dark-tech" autoresize @click="handleExperimentChartClick" />
                  </ChartCard>
                  <ChartCard title="光伏出力预测曲线">
                    <v-chart class="chart input-chart" :option="pvForecastOption" theme="dark-tech" autoresize @click="handleExperimentChartClick" />
                  </ChartCard>
                </section>
              </div>

              <aside class="kpi-panel glass-panel">
                <div class="section-title">
                  <span>调度指标</span>
                  <h3>核心结果</h3>
                </div>
                <div class="kpi-section" v-for="group in experimentKpiGroups" :key="group.title">
                  <h4>{{ group.title }}</h4>
                  <div class="kpi-grid" :class="group.compact ? 'compact-kpis' : ''">
                    <div v-for="item in group.items" :key="item.label" class="kpi-card" :class="item.tone">
                      <span>{{ item.label }}</span>
                      <strong>{{ item.value }}</strong>
                      <small>{{ item.delta }}</small>
                    </div>
                  </div>
                </div>
                <div class="moment-card">
                  <span>选中时刻（电站当地时间）</span>
                  <strong>{{ selectedMoment.timeLabel }}</strong>
                  <p>天气 {{ formatIrradiance(selectedMoment.ghiWm2) }} / {{ formatPercentWhole(selectedMoment.cloudCoverPct) }} 云量，光伏 {{ formatKw(selectedMoment.pvKw) }}，SOC {{ formatPercent(selectedMoment.soc) }}，电价 {{ formatPrice(selectedMoment.priceEurMwh) }}。</p>
                </div>
              </aside>
            </div>

            <section class="analysis-grid">
              <ChartCard title="参数影响">
                <v-chart class="chart" :option="sensitivityOption" theme="dark-tech" autoresize />
              </ChartCard>
              <div class="analysis-panel glass-card">
                <div class="section-title">
                  <span>方案对比</span>
                  <h3>方案对比</h3>
                </div>
                <el-table class="compare-table" :data="experimentComparisonRows" size="small" :row-class-name="comparisonRowClassName">
                  <el-table-column label="方案" min-width="130">
                    <template #default="{ row }">
                      <span class="scheme-name">{{ row.label }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="方案结论" min-width="110">
                    <template #default="{ row }">
                      <span class="scheme-badge" :class="row.recommended ? 'recommended' : row.incrementalRevenueEur < 0 ? 'not-recommended' : ''">{{ row.conclusion }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="总收益" min-width="110"><template #default="{ row }">{{ formatCurrency(row.totalRevenueEur) }}</template></el-table-column>
                  <el-table-column label="相比无储能收益" min-width="130"><template #default="{ row }"><span :class="row.incrementalRevenueEur >= 0 ? 'positive' : 'negative'">{{ formatCurrency(row.incrementalRevenueEur) }}</span></template></el-table-column>
                  <el-table-column label="削峰效果" min-width="90">
                    <template #header>
                      <el-tooltip content="削峰效果表示相对无储能基准的并网峰值下降比例。" placement="top">
                        <span>削峰效果</span>
                      </el-tooltip>
                    </template>
                    <template #default="{ row }">{{ formatPercent(row.peakShavingRatio) }}</template>
                  </el-table-column>
                  <el-table-column label="平滑效果" min-width="90">
                    <template #header>
                      <el-tooltip content="平滑效果表示并网功率平均爬坡波动相对光伏预测功率的下降比例。" placement="top">
                        <span>平滑效果</span>
                      </el-tooltip>
                    </template>
                    <template #default="{ row }">{{ formatPercent(row.smoothingRatio) }}</template>
                  </el-table-column>
                </el-table>
                <p class="table-note">收益为负表示当前参数下没有超过无储能方案。</p>
              </div>
            </section>

            <section class="history-panel glass-card">
              <div class="section-title">
                <span>运行历史</span>
                <h3>历史记录</h3>
              </div>
              <el-table class="history-table" :data="experimentRunHistoryRows" size="small" v-loading="historyLoading">
                <el-table-column label="运行时间" min-width="160"><template #default="{ row }">{{ formatRunDateSecond(row.created_at) }}</template></el-table-column>
                <el-table-column label="天气" min-width="90"><template #default="{ row }">{{ weatherScenarioLabel(row.weather_scenario) }}</template></el-table-column>
                <el-table-column label="电价" min-width="150"><template #default="{ row }">{{ priceScenarioLabel(row.price_scenario || row.price_scenario_label) }}</template></el-table-column>
                <el-table-column label="时段" min-width="70"><template #default="{ row }">{{ row.horizon_hours }}h</template></el-table-column>
                <el-table-column label="参数摘要" min-width="220"><template #default="{ row }">{{ historyParameterSummary(row) }}</template></el-table-column>
                <el-table-column label="相比无储能收益" min-width="140"><template #default="{ row }"><span :class="Number(row.incremental_revenue_eur || 0) >= 0 ? 'positive' : 'negative'">{{ formatCurrency(row.incremental_revenue_eur) }}</span></template></el-table-column>
                <el-table-column label="运行状态" min-width="90"><template #default="{ row }">{{ row.status === 'success' ? '成功' : row.status }}</template></el-table-column>
                <el-table-column label="操作" min-width="120" fixed="right">
                  <template #default="{ row }">
                    <el-tooltip content="加载该历史方案的调度参数与实验配置" placement="left">
                      <el-button link type="primary" @click="loadExperimentHistoryRun(row.run_id)">恢复该方案</el-button>
                    </el-tooltip>
                  </template>
                </el-table-column>
              </el-table>
            </section>
          </section>
          <PageState v-else type="empty" title="暂无天气与电价场景结果" message="未读取到天气驱动与电价场景调度结果。" />
        </el-tab-pane>

        <el-tab-pane label="收益与退化" name="revenue-degradation" lazy>
          <section v-if="referenceAvailable" class="tab-stack">
            <div class="reference-hero glass-panel">
              <div>
                <span class="kicker">参考电站调度仿真</span>
                <h2>参考光伏电站公开参数仿真</h2>
                <p>{{ referenceSite.location || 'Larimer County, Colorado' }}</p>
              </div>
              <div class="reference-facts">
                <div><span>光伏容量</span><strong>{{ formatMw(referenceSite.pv_capacity_kw_ac) }}</strong></div>
                <div><span>储能配置</span><strong>{{ storageConfigText }}</strong></div>
                <div><span>投运年份</span><strong>{{ referenceSite.commercial_operation_year || '2021' }}</strong></div>
                <div><span>仿真周期</span><strong>{{ simulationPeriodText }}</strong></div>
              </div>
            </div>

            <div class="boundary-panel glass-panel">
              <strong>说明</strong>
              <p>仿真结果用于策略对比和系统功能验证，不代表实际市场收益。参考电站参数来自公开容量信息，发电曲线和电价曲线用于方法演示。</p>
            </div>

            <div class="metric-grid">
              <RevenueCard v-for="item in revenueCards" :key="item.label" :item="item" />
            </div>

            <ChartCard title="相对无储能基准的增量收益对比">
              <p class="chart-note">所有柱状图金额单位为元，口径为基于 OPSD 映射或项目代理电价并按固定汇率换算的仿真增量收益；是否扣除退化成本见卡片说明。</p>
              <v-chart class="chart" :option="referenceRevenueOption" theme="dark-tech" autoresize />
            </ChartCard>
          </section>
          <PageState v-else type="empty" title="暂无参考电站仿真数据" message="未读取到参考电站仿真报告或指标文件。" />

          <section class="tab-stack" style="margin-top: var(--space-lg)">
            <ChartCard title="电池退化成本评估">
              <div class="metric-grid">
                <RevenueCard :item="degradationRevenueCard" />
                <div class="revenue-card glass-card">
                  <span>电池健康状态</span>
                  <strong>{{ formatPercent(degradationRecommended.soh_start) }} -> {{ formatPercent(degradationRecommended.soh_end) }}</strong>
                  <small>仿真周期：{{ simulationPeriodText }}</small>
                </div>
                <div class="revenue-card glass-card">
                  <span>等效满循环</span>
                  <strong>{{ formatNumber(degradationRecommended.equivalent_full_cycles, 1) }}</strong>
                  <small>用于估算循环退化成本</small>
                </div>
              </div>
            </ChartCard>
          </section>
        </el-tab-pane>

        <el-tab-pane label="配置与策略" name="config-strategy" lazy>
          <section class="tab-stack">
            <ChartCard title="储能配置敏感性分析">
              <div class="pareto-summary">
                <span>候选配置</span>
                <strong>{{ configLabel(bestConfig.config_id, bestConfig) }}</strong>
                <p>该配置在当前仿真周期下取得 {{ formatCurrency(bestConfig.incremental_revenue_eur) }} 的相对无储能基准增量收益，未单独扣除电池退化成本。</p>
              </div>
              <el-table class="pareto-table" :data="configRows" size="small">
                <el-table-column label="储能配置" min-width="210"><template #default="{ row }">{{ configLabel(row.config_id, row) }}</template></el-table-column>
                <el-table-column label="容量" min-width="90"><template #default="{ row }">{{ formatKwh(row.capacity_kwh) }}</template></el-table-column>
                <el-table-column label="功率" min-width="90"><template #default="{ row }">{{ formatKw(row.max_discharge_kw) }}</template></el-table-column>
                <el-table-column label="相对无储能基准的增量收益" min-width="180"><template #default="{ row }"><span class="money" :class="Number(row.incremental_revenue_eur) >= 0 ? 'positive' : 'negative'">{{ formatCurrency(row.incremental_revenue_eur) }}</span></template></el-table-column>
                <el-table-column label="较优组合" min-width="90"><template #default="{ row }">{{ isTrue(row.pareto_front) ? '是' : '-' }}</template></el-table-column>
              </el-table>
            </ChartCard>
          </section>

          <section v-if="scorecard.length" class="tab-stack" style="margin-top: var(--space-lg)">
            <ChartCard title="策略评价">
              <div class="strategy-row">
                <div v-for="strategy in scorecard" :key="strategy.scenario_id" class="strategy-card glass-card" :class="decisionClass(strategy.governance_decision)">
                  <div class="sc-header">
                    <span class="sc-decision">{{ decisionLabel(strategy.governance_decision) }}</span>
                    <span class="sc-score display-number">{{ formatNumber(strategy.governance_score, 1) }}</span>
                  </div>
                  <h4>{{ scenarioLabel(strategy.scenario_id) }}</h4>
                  <p class="sc-type">{{ strategyTypeLabel(strategy.strategy_type) }}</p>
                  <div class="sc-metrics">
                    <div><span>总调度收入</span><strong>{{ formatCurrency(strategy.total_storage_revenue_eur) }}</strong></div>
                    <div><span>相对无储能基准的增量收益</span><strong :class="Number(strategy.incremental_revenue_eur) >= 0 ? 'positive' : 'negative'">{{ formatCurrency(strategy.incremental_revenue_eur) }}</strong></div>
                    <div><span>循环次数</span><strong>{{ formatNumber(strategy.cycle_equivalent_count, 1) }}</strong></div>
                    <div><span>平均 SOC</span><strong>{{ formatPercent(strategy.mean_soc) }}</strong></div>
                  </div>
                  <p class="sc-reason">{{ strategy.decision_reason }}</p>
                </div>
              </div>
              <div class="chart-row">
                <ChartCard title="策略评分对比">
                  <p class="chart-note">经济性、约束满足和风险控制均为评分项，分值越高表示该项评价越好。</p>
                  <v-chart class="chart" :option="scoreBarOption" theme="dark-tech" autoresize />
                </ChartCard>
                <ChartCard title="三维评分雷达">
                  <p class="chart-note">该雷达图展示策略评分结构，越外表示评分越高；与模型误差雷达图的“越靠近中心越好”方向不同。</p>
                  <v-chart class="chart" :option="radarOption" theme="dark-tech" autoresize />
                </ChartCard>
              </div>
            </ChartCard>
          </section>
          <PageState v-else type="empty" title="暂无策略评价评分" message="当前接口未返回策略评价评分。" />
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { DataAnalysis, Download, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart, RadarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, RadarComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ChartCard from '../components/ChartCard.vue'
import MetricGrid from '../components/MetricGrid.vue'
import PageState from '../components/PageState.vue'
import {
  buildExperimentComparisonOption,
  buildExperimentPowerDispatchOption,
  buildExperimentPriceRevenueOption,
  buildExperimentPvForecastOption,
  buildExperimentSensitivityOption,
  buildExperimentSocOption,
  buildExperimentWeatherTrendOption,
  buildGovernanceRadarOption,
  buildRawhideRevenueOption,
  buildScoreBarOption,
} from '../charts/dispatchCharts'
import { exportWeatherDispatchExperimentRun, fetchGovernanceScorecard, fetchRawhideDegradationMetrics, fetchRawhideDispatchMetrics, fetchRawhideReport, fetchRawhideSensitivityMetrics, fetchShowcaseScenarios, fetchStage21DispatchMetrics, fetchStage21DispatchResults, fetchStage21PriceScenarios, fetchStage21Report, fetchStage21WeatherPredictions, fetchWeatherDispatchExperimentRun, fetchWeatherDispatchExperimentRuns, runWeatherDispatchExperiment } from '../services/dispatchService'
import { normalizeApiError } from '../utils/api'
import { eurToCny, formatYuan, formatYuanFromEur, formatYuanPerMwhFromEur, formatYuanPerUnitFromEur, replaceEurUnitsInText } from '../utils/currency'
import { configLabel, scenarioLabel } from '../utils/displayLabels'
import { formatReferenceSiteHour, REFERENCE_SITE_TIME_LABEL } from '../utils/siteTime'

use([CanvasRenderer, BarChart, LineChart, RadarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, RadarComponent])

const RevenueCard = defineComponent({
  props: { item: { type: Object, required: true } },
  setup(props) {
    return () => h('div', { class: 'revenue-card glass-card' }, [
      h('span', props.item.label),
      h('strong', { class: ['display-number', props.item.className || ''] }, props.item.value),
      h('small', props.item.hint),
      h('ul', { class: 'revenue-basis' }, props.item.meta.map(row => h('li', row))),
    ])
  },
})

const weatherScenarioOptions = [
  { value: 'realtime', label: '实时天气预报' },
  { value: 'clear', label: '晴天' },
  { value: 'cloudy', label: '多云' },
  { value: 'overcast', label: '阴天' },
  { value: 'custom', label: '自定义' },
]
const objectiveOptions = [
  { value: 'economic', label: '经济性优先' },
  { value: 'smooth', label: '平滑优先' },
  { value: 'balanced', label: '综合优化' },
]
const algorithmOptions = [
  { value: 'rule', label: '规则策略' },
  { value: 'rolling', label: '滚动优化' },
  { value: 'multi', label: '多目标优化' },
]
const defaultPriceScenarios = [
  { id: 'tou_peak_valley', label: '峰谷分时电价场景' },
  { id: 'flat_proxy_30', label: '固定电价场景' },
  { id: 'high_volatility_stress', label: '高波动电价场景' },
  { id: 'synthetic_scenario', label: '合成电价场景' },
  { id: 'solar_duck_curve', label: '光伏鸭形曲线电价场景' },
]
const weatherScenarioProfiles = {
  realtime: { ghi: 1, cloud: 1, temp: 0, humidity: 0, wind: 1 },
  clear: { ghi: 1.18, cloud: 0.45, temp: 2, humidity: -8, wind: 0.9 },
  cloudy: { ghi: 0.74, cloud: 1.35, temp: -1, humidity: 5, wind: 1.08 },
  overcast: { ghi: 0.42, cloud: 1.65, temp: -3, humidity: 12, wind: 1.12 },
  custom: { ghi: 1, cloud: 1, temp: 0, humidity: 0, wind: 1 },
}

function defaultExperimentForm() {
  return {
    dispatchDate: new Date().toISOString().slice(0, 10),
    horizonHours: 24,
    weatherScenario: 'realtime',
    priceScenario: 'high_volatility_stress',
    batteryEnergyKwh: 4000,
    batteryPowerKw: 2000,
    chargeEfficiency: 0.95,
    dischargeEfficiency: 0.95,
    initialSoc: 0.5,
    socMin: 0.1,
    socMax: 0.9,
    objective: 'smooth',
    algorithm: 'rule',
  }
}

const scorecard = ref([])
const rawhideReport = ref(null)
const referenceMetrics = ref([])
const configMetrics = ref([])
const degradationMetrics = ref([])
const weatherReport = ref(null)
const weatherPredictions = ref([])
const priceScenarioRows = ref([])
const dispatchResults = ref([])
const weatherDispatchMetrics = ref([])
// Stage23 showcase
const showcaseScenarios = ref([])
const activeTab = ref('weather-price')
const experimentResultTab = ref('power')
const showcaseDetailVisible = ref(false)
const showcaseDetailTab = ref('positive')
const loading = ref(false)
const error = ref(null)
const isRunningExperiment = ref(false)
const experimentRunId = ref(0)
const selectedTimeIndex = ref(0)
const experimentForm = reactive(defaultExperimentForm())
const appliedExperiment = ref(defaultExperimentForm())
const backendExperiment = ref(null)
const experimentRunError = ref(null)
const experimentRunHistory = ref([])
const historyLoading = ref(false)
let runTimer = null

const scoreBarOption = computed(() => buildScoreBarOption(scorecard.value))
const radarOption = computed(() => buildGovernanceRadarOption(scorecard.value))
const referenceRevenueOption = computed(() => buildRawhideRevenueOption(referenceMetrics.value))
const referenceAvailable = computed(() => Boolean(rawhideReport.value && referenceMetrics.value.length))
const weatherPriceAvailable = computed(() => true)
const referenceSite = computed(() => rawhideReport.value?.reference_site || {})
const bestConfig = computed(() => rawhideReport.value?.recommended_pareto_config || configRows.value[0] || {})
const degradationRecommended = computed(() => rawhideReport.value?.degradation_recommended_metrics || degradationMetrics.value.find(item => item.scenario === 'rolling_with_rainflow_degradation') || {})
const rollingMetric = computed(() => referenceMetrics.value.find(item => item.scenario === 'rolling_optimization') || {})
const thresholdMetric = computed(() => referenceMetrics.value.find(item => item.scenario === 'stage11_best_threshold_q40_q95') || {})
const fixedMetric = computed(() => referenceMetrics.value.find(item => item.scenario === 'stage10_fixed_threshold') || {})
const configRows = computed(() => configMetrics.value.filter(item => isTrue(item.pareto_front)).slice(0, 6))
const simulationPeriodText = computed(() => rawhideReport.value?.simulation_period || rawhideReport.value?.period || '报告覆盖周期')
const storageConfigText = computed(() => configLabel('', {
  battery_power_kw: referenceSite.value.battery_power_kw,
  battery_energy_kwh: referenceSite.value.battery_energy_kwh,
}))
const priceScenarios = computed(() => {
  const seen = new Map()
  for (const row of priceScenarioRows.value) {
    if (!seen.has(row.price_scenario_id)) seen.set(row.price_scenario_id, { id: row.price_scenario_id, label: priceScenarioLabel(row.price_scenario_id || row.price_scenario_label) })
  }
  const rows = Array.from(seen.values())
  return rows.length ? rows : defaultPriceScenarios
})
const appliedPriceScenarioLabel = computed(() => priceScenarioLabel(appliedExperiment.value.priceScenario))
const experimentBoundaryText = '本页基于实时天气预报和设定电价场景进行储能调度仿真。光伏出力为模型估算值，收益结果用于策略对比和系统功能验证，不代表真实电站结算收益。'
const currentStorageSummary = computed(() => `当前配置：${formatKwh(appliedExperiment.value.batteryEnergyKwh)} / ${formatKw(appliedExperiment.value.batteryPowerKw)}，SOC ${formatPercent(appliedExperiment.value.socMin)} - ${formatPercent(appliedExperiment.value.socMax)}。`)
// Stage23 showcase computed
const SCENARIO_TYPE_MAP = { baseline: '基准纯套利', price_volatility: '价格波动增强', capacity_revenue: '容量价值叠加', cost_improvement: '退化成本改善', pure_arbitrage_best: '最优纯套利', degradation_aware: '退化约束主动循环', aggressive_baseline: '激进策略对照' }
const showcaseKpiCards = computed(() => {
  const positiveCount = showcaseScenarios.value.filter(s => Number(s.net_incremental_revenue_eur) > 0).length
  const best = [...showcaseScenarios.value].sort((a, b) => Number(b.net_incremental_revenue_eur) - Number(a.net_incremental_revenue_eur))[0]
  return [
    { label: '最优情景净增量', value: best ? fmtCurrency(best.net_incremental_revenue_eur) : '—', icon: 'Coin', gradient: 'var(--gradient-cyan)' },
    { key: 'positive-scenarios', label: '正净增量数', value: `${positiveCount} / ${showcaseScenarios.value.length}`, icon: 'DataAnalysis', gradient: 'var(--gradient-green)', clickable: true, detailTab: 'positive' },
    { key: 'scenario-types', label: '情景类型数', value: new Set(showcaseScenarios.value.map(s => s.scenario_type)).size, icon: 'Histogram', gradient: 'var(--gradient-purple)', clickable: true, detailTab: 'types' },
  ]
})
const positiveShowcaseRows = computed(() => showcaseScenarios.value
  .filter(row => Number(row.net_incremental_revenue_eur) > 0)
  .map(row => ({ ...row, typeLabel: scenarioTypeLabel(row.scenario_type) }))
  .sort((a, b) => Number(b.net_incremental_revenue_eur) - Number(a.net_incremental_revenue_eur)))
const showcaseTypeRows = computed(() => {
  const groups = new Map()
  for (const row of showcaseScenarios.value) {
    const type = row.scenario_type || 'unknown'
    const group = groups.get(type) || { scenarioType: type, typeLabel: scenarioTypeLabel(type), count: 0, positiveCount: 0, netValues: [] }
    const net = Number(row.net_incremental_revenue_eur)
    group.count += 1
    if (Number.isFinite(net)) {
      group.netValues.push(net)
      if (net > 0) group.positiveCount += 1
    }
    groups.set(type, group)
  }
  return [...groups.values()]
    .map(group => ({
      ...group,
      bestNetEur: group.netValues.length ? Math.max(...group.netValues) : null,
      averageNetEur: group.netValues.length ? group.netValues.reduce((sum, value) => sum + value, 0) / group.netValues.length : null,
    }))
    .sort((a, b) => b.positiveCount - a.positiveCount || b.count - a.count || a.typeLabel.localeCompare(b.typeLabel, 'zh-CN'))
})
const showcaseNetRevenueChartOption = computed(() => ({
  tooltip: { trigger: 'axis', valueFormatter: value => formatYuan(value) },
  grid: { left: 80, right: 40, top: 20, bottom: 100 },
  xAxis: { type: 'category', data: showcaseScenarios.value.map(s => displayScenarioName(s.scenario_name)), axisLabel: { rotate: 30, fontSize: 10 } },
  yAxis: { type: 'value', name: '元', axisLabel: { formatter: value => formatYuan(value, 0) } },
  series: [{
    type: 'bar', data: showcaseScenarios.value.map(s => ({
      value: eurToCny(s.net_incremental_revenue_eur),
      itemStyle: { color: Number(s.net_incremental_revenue_eur) >= 0 ? '#00f5a0' : '#ff6b6b' },
    })),
  }],
}))
function scenarioTypeLabel(type) { return SCENARIO_TYPE_MAP[type] || type }
function openShowcaseDetail(item) {
  if (!item?.detailTab) return
  showcaseDetailTab.value = item.detailTab
  showcaseDetailVisible.value = true
}
function fmtEur(row, col, value) {
  return formatYuanFromEur(value, 2, '—')
}
function fmtCurrency(value) {
  return formatYuanFromEur(value, 2, '—')
}
function displayScenarioName(value) {
  return replaceEurUnitsInText(value)
}
function strategyDisplayLabel(value) {
  const labels = {
    zero_cycle_lower_bound: '零循环保护策略',
    best_active_config: '价差放大下的主动循环策略',
    best_active_cycling: '容量价值叠加下的主动循环策略',
    optimal_pure_arbitrage: '纯套利最优筛选策略',
    degradation_aware_active: '退化约束主动循环策略',
    stage15_aggressive: '不计退化惩罚的激进对照策略',
  }
  return labels[value] || replaceEurUnitsInText(value || '—')
}
function scenarioExplanation(row = {}) {
  const type = row.scenario_type
  if (type === 'baseline') return '仅采用基准代理电价和默认退化参数，主动循环收益不足以覆盖电池老化，因此作为纯套利基准参照。'
  if (type === 'price_volatility') return '将代理电价波动放大到 3 倍，用于模拟价差更明显的市场条件，检验储能低价充电、高价放电后能否覆盖退化成本。'
  if (type === 'capacity_revenue') return `在电价套利之外叠加容量价值，当前情景容量价值为 ${formatYuanPerUnitFromEur(row.capacity_value_eur_per_kw_year, 'kW·年')}，用于说明容量支撑收益对净增量的影响。`
  if (type === 'cost_improvement') return `假设电池更换成本降低至 ${formatYuanPerUnitFromEur(row.replacement_cost_eur_per_kwh, 'kWh')}，循环寿命提升至 ${row.cycle_life_multiplier || '—'} 倍，年历衰减降至 ${row.calendar_fade_rate || '—'}/年，用于观察电池经济性改善后的收益边界。`
  if (type === 'pure_arbitrage_best') return '在成本和寿命改善后的纯套利组合中选择表现较优的结果，用于说明单纯套利在更有利电池经济条件下也可能形成正净增量。'
  if (type === 'degradation_aware') return '在调度目标中加入退化约束，λ=1.0 表示对循环退化进行惩罚，使策略减少无效循环，但在基准代理电价下仍可能无法转正。'
  if (type === 'aggressive_baseline') return 'λ=0 表示不对退化成本施加惩罚，策略更追求毛收益；该对照用于说明只看毛收益会导致循环过多和净收益恶化。'
  return '该情景用于比较不同经济假设下的收益变化，结果仅作为参考仿真展示。'
}
function scenarioBoundaryText(row = {}) {
  if (row.boundary_note) return `${row.boundary_note}；该结果为参考仿真，不代表真实电站运行或市场结算。`
  return '该结果为参考仿真，不代表真实电站运行或市场结算。'
}
function fmtPercent(row, col, value) { const n = Number(value); return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '—' }
function fmtNumber(row, col, value) { const n = Number(value); return Number.isFinite(n) ? n.toFixed(1) : '—' }

const dispatchInsight = computed(() => ({
  title: showcaseScenarios.value.length
    ? `调度结论展示 ${showcaseScenarios.value.length} 个收益情景，${showcaseKpiCards.value[1]?.value || '0'} 个取得正净增量。`
    : `当前调度实验采用${weatherScenarioLabel(appliedExperiment.value.weatherScenario)}预测，优化后相对无储能基准的增量收益为 ${formatCurrency(experimentKpis.value.incrementalRevenueEur)}。`,
  tone: showcaseScenarios.value.filter(s => Number(s.net_incremental_revenue_eur) > 0).length > 0 ? 'positive' : 'warning',
  items: [
    '所有收益均为相对无储能基准的仿真增量，单位为元。Rawhide 相关为公开容量参数参照场景，不构成真实电站运行数据或真实市场结算结果。',
    '容量价值叠加、价差放大或电池成本改善等情景用于比较不同经济假设下的收益变化。',
    `当前储能参数：${formatKwh(appliedExperiment.value.batteryEnergyKwh)} / ${formatKw(appliedExperiment.value.batteryPowerKw)}，调度窗口：${appliedExperiment.value.horizonHours} 小时。`,
  ],
}))
const revenueCards = computed(() => [
  revenueCard('相对无储能基准的增量收益', rollingMetric.value.incremental_revenue_eur, false, '滚动优化调度方案', rollingMetric.value),
  revenueCard('相对无储能基准的增量收益', thresholdMetric.value.incremental_revenue_eur, false, '离线阈值对照方案', thresholdMetric.value),
  revenueCard('相对无储能基准的增量收益', fixedMetric.value.incremental_revenue_eur, false, '固定阈值调度方案', fixedMetric.value),
])
const degradationRevenueCard = computed(() => revenueCard('扣除电池退化成本后的净收益', degradationRecommended.value.net_incremental_revenue_eur, true, '滚动优化调度方案', degradationRecommended.value))

const experimentKpis = computed(() => mapBackendKpis(backendExperiment.value?.kpis))
const experimentSeriesRows = computed(() => mapBackendDispatchRows(backendExperiment.value?.dispatch_rows || []))
const selectedMoment = computed(() => {
  const rows = experimentSeriesRows.value
  const index = Math.min(Math.max(selectedTimeIndex.value, 0), Math.max(rows.length - 1, 0))
  return rows[index] || {}
})
const experimentComparisonRows = computed(() => {
  const rows = mapBackendComparisonRows(backendExperiment.value?.comparison || [])
  const best = rows
    .filter(row => row.label !== '无储能')
    .reduce((winner, row) => (row.incrementalRevenueEur > (winner?.incrementalRevenueEur ?? Number.NEGATIVE_INFINITY) ? row : winner), null)
  return rows.map(row => {
    const recommended = Boolean(best && row.label === best.label)
    return {
      ...row,
      recommended,
      conclusion: row.label === '无储能' ? '基准方案' : recommended ? '当前较优' : row.incrementalRevenueEur < 0 ? '待谨慎' : '可对照',
    }
  })
})
const sensitivityRows = computed(() => mapBackendSensitivityRows(backendExperiment.value?.sensitivity || []))
const experimentStatusText = computed(() => {
  if (isRunningExperiment.value) return '运行中'
  if (backendExperiment.value?.run_id) return '运行成功'
  if (backendExperiment.value) return '任务已完成'
  return '待运行'
})
const lastRunTimeText = computed(() => backendExperiment.value?.created_at ? `最近运行 ${formatRunDateSecond(backendExperiment.value.created_at)}` : '尚未运行调度任务')
const resultTabDescription = computed(() => ({
  power: '展示储能调度如何改变并网功率；上下图共享时间轴，但分别采用电站侧出力尺度和储能设备净功率尺度。',
  soc: '展示储能荷电状态是否在 SOC 上下限范围内运行。',
  price: '展示电价、小时收益和储能调度带来的收益贡献。',
  compare: '展示各策略相对无储能基准的增量收益，便于比较策略优劣。',
})[experimentResultTab.value] || '')
const experimentKpiGroups = computed(() => [
  {
    title: '核心指标',
    items: [
      kpiCard('相比无储能收益', formatCurrency(experimentKpis.value.incrementalRevenueEur), experimentKpis.value.incrementalRevenueEur, '当前参数结果'),
      kpiCard('总收益', formatCurrency(experimentKpis.value.totalRevenueEur), experimentKpis.value.totalRevenueEur - experimentKpis.value.noStorageRevenueEur, '较无储能收益'),
      kpiCard('弃光率', formatPercent(experimentKpis.value.curtailmentRate), -experimentKpis.value.curtailmentRate, '越低越好'),
      kpiCard('SOH变化', formatSohImpact(experimentKpis.value.sohImpact), -experimentKpis.value.sohImpact, '窗口内退化估计'),
    ],
  },
])
const experimentRunHistoryRows = computed(() => dedupeHistoryRows(experimentRunHistory.value))
const weatherTrendOption = computed(() => buildExperimentWeatherTrendOption(experimentSeriesRows.value, selectedTimeIndex.value))
const pvForecastOption = computed(() => buildExperimentPvForecastOption(experimentSeriesRows.value, selectedTimeIndex.value))
const powerDispatchOption = computed(() => buildExperimentPowerDispatchOption(experimentSeriesRows.value, selectedTimeIndex.value))
const socCurveOption = computed(() => buildExperimentSocOption(experimentSeriesRows.value, selectedTimeIndex.value))
const priceRevenueOption = computed(() => buildExperimentPriceRevenueOption(experimentSeriesRows.value, selectedTimeIndex.value))
const comparisonOption = computed(() => buildExperimentComparisonOption(experimentComparisonRows.value))
const sensitivityOption = computed(() => buildExperimentSensitivityOption(sensitivityRows.value))

function revenueCard(label, value, degradationDeducted, scenario, row = {}) {
  return {
    label,
    value: formatCurrency(value),
    className: Number(value || 0) >= 0 ? 'positive' : 'negative',
    hint: scenario,
    meta: [
      '对比基准：无储能运行',
      `仿真周期：${simulationPeriodText.value}`,
      `退化成本：${degradationDeducted ? '已扣除' : '未扣除'}`,
      `储能配置：${configLabel(row.config_id, row) || storageConfigText.value}`,
      '收益单位：元（基于 OPSD 映射或项目代理电价并按固定汇率换算）',
    ],
  }
}

function mapBackendDispatchRows(rows) {
  return rows.map(row => {
    const sourceTime = row.source_forecast_valid_time || row.forecast_valid_time || row.time
    const time = sourceTime || row.time
    return {
      time,
      sourceTime,
      timeLabel: formatDateHour(time),
      ghiWm2: numberOr(row.ghi_wm2, 0),
      pvKw: numberOr(row.pv_kw, 0),
      temperatureC: numberOr(row.temperature_c, 20),
      cloudCoverPct: numberOr(row.cloud_cover_pct, 0),
      humidityPct: numberOr(row.relative_humidity_pct, 0),
      windSpeedMs: numberOr(row.wind_speed_ms, 0),
      priceEurMwh: numberOr(row.price_eur_mwh, 0),
      chargeKw: numberOr(row.charge_kw, 0),
      dischargeKw: numberOr(row.discharge_kw, 0),
      gridKw: numberOr(row.grid_kw, 0),
      soc: numberOr(row.soc, 0),
      socPct: numberOr(row.soc_pct, 0),
      socMinPct: numberOr(row.soc_min_pct, 0),
      socMaxPct: numberOr(row.soc_max_pct, 0),
      revenueEur: numberOr(row.revenue_eur, 0),
      noStorageRevenueEur: numberOr(row.no_storage_revenue_eur, 0),
      incrementalRevenueEur: numberOr(row.incremental_revenue_eur, 0),
      curtailmentKw: numberOr(row.curtailment_kw, 0),
      degradationCostEur: numberOr(row.degradation_cost_eur, 0),
    }
  })
}

function mapBackendKpis(kpis = {}) {
  return {
    totalRevenueEur: numberOr(kpis.total_revenue_eur, 0),
    noStorageRevenueEur: numberOr(kpis.no_storage_revenue_eur, 0),
    incrementalRevenueEur: numberOr(kpis.incremental_revenue_eur, 0),
    degradationCostEur: numberOr(kpis.degradation_cost_eur, 0),
    equivalentCycles: numberOr(kpis.equivalent_cycles, 0),
    sohImpact: numberOr(kpis.soh_impact, 0),
    curtailmentRate: numberOr(kpis.curtailment_rate, 0),
    curtailmentReductionRatio: numberOr(kpis.curtailment_reduction_ratio, 0),
    peakShavingRatio: numberOr(kpis.peak_shaving_ratio, 0),
    smoothingRatio: numberOr(kpis.smoothing_ratio, 0),
  }
}

function mapBackendComparisonRows(rows) {
  return rows.map((row, index) => ({
    label: comparisonSchemeLabel(row.label, index),
    ...mapBackendKpis(row),
  }))
}

function mapBackendSensitivityRows(rows) {
  return rows.map(row => ({
    label: row.label || '-',
    capacityRevenueEur: numberOr(row.capacity_revenue_eur, 0),
    powerRevenueEur: numberOr(row.power_revenue_eur, 0),
  }))
}

function buildExperimentRows(form) {
  const sourceRows = weatherPredictions.value.length ? weatherPredictions.value : []
  if (!sourceRows.length) return []
  const profile = weatherScenarioProfiles[form.weatherScenario] || weatherScenarioProfiles.realtime
  const priceMap = new Map(
    priceScenarioRows.value
      .filter(row => row.price_scenario_id === form.priceScenario)
      .map(row => [String(row.timestamp), numberOr(row.price_eur_mwh, 0)]),
  )
  const horizon = Math.min(Math.max(Number(form.horizonHours) || 24, 1), 72)
  const capacityKw = numberOr(referenceSite.value.pv_capacity_kw_ac, 22000)
  return Array.from({ length: horizon }, (_, index) => {
    const source = sourceRows[index % sourceRows.length]
    const sourceTime = source.weather_valid_time || source.timestamp
    const time = sourceTime
    const baseGhi = numberOr(source.ghi_wm2, 0)
    const ghiWm2 = clamp(baseGhi * profile.ghi, 0, 1100)
    const pvKw = clamp(capacityKw * ghiWm2 / 1000 * numberOr(source.performance_ratio, 0.82), 0, capacityKw)
    return {
      time,
      sourceTime,
      timeLabel: formatDateHour(time),
      ghiWm2,
      pvKw,
      temperatureC: numberOr(source.temperature_c, 20) + profile.temp,
      cloudCoverPct: clamp(numberOr(source.cloud_cover_pct, 30) * profile.cloud, 0, 100),
      humidityPct: clamp(numberOr(source.relative_humidity_pct, 45) + profile.humidity, 0, 100),
      windSpeedMs: clamp(numberOr(source.wind_speed_ms, 3.5) * profile.wind, 0, 35),
      priceEurMwh: priceMap.get(String(sourceTime)) ?? syntheticPrice(index, form.priceScenario),
    }
  })
}

function simulateExperiment(rows, form) {
  if (!rows.length) return { rows: [], kpis: emptyKpis() }
  const energyKwh = clamp(numberOr(form.batteryEnergyKwh, 2000), 1, 20000)
  const powerKw = clamp(numberOr(form.batteryPowerKw, 1000), 0, 10000)
  const chargeEff = clamp(numberOr(form.chargeEfficiency, 0.95), 0.5, 1)
  const dischargeEff = clamp(numberOr(form.dischargeEfficiency, 0.95), 0.5, 1)
  const socMin = clamp(Math.min(numberOr(form.socMin, 0.1), numberOr(form.socMax, 0.9) - 0.02), 0, 0.95)
  const socMax = clamp(Math.max(numberOr(form.socMax, 0.9), socMin + 0.02), 0.05, 1)
  let soc = clamp(numberOr(form.initialSoc, 0.5), socMin, socMax)
  const prices = rows.map(row => row.priceEurMwh).sort((a, b) => a - b)
  const priceLow = quantile(prices, 0.28)
  const priceHigh = quantile(prices, 0.72)
  const priceMean = prices.reduce((sum, value) => sum + value, 0) / prices.length
  const pvMean = rows.reduce((sum, row) => sum + row.pvKw, 0) / rows.length
  const gridLimit = Math.max(pvMean * 1.15, numberOr(referenceSite.value.pv_capacity_kw_ac, 22000) * 0.72)
  let totalRevenueEur = 0
  let noStorageRevenueEur = 0
  let degradationCostEur = 0
  let throughputKwh = 0
  let pvEnergyKwh = 0
  let curtailedKwh = 0
  let noStorageCurtailedKwh = 0

  const simulated = rows.map((row, index) => {
    const decision = dispatchDecision(row, index, rows, { ...form, priceLow, priceHigh, priceMean, pvMean, gridLimit, powerKw })
    const availableChargeKw = Math.max(0, (socMax - soc) * energyKwh / chargeEff)
    const availableDischargeKw = Math.max(0, (soc - socMin) * energyKwh * dischargeEff)
    const chargeKw = clamp(Math.min(decision.chargeKw, row.pvKw, availableChargeKw), 0, powerKw)
    soc = clamp(soc + (chargeKw * chargeEff) / energyKwh, socMin, socMax)
    const dischargeKw = clamp(Math.min(decision.dischargeKw, availableDischargeKw), 0, powerKw)
    soc = clamp(soc - (dischargeKw / dischargeEff) / energyKwh, socMin, socMax)
    const noStorageGridKw = Math.min(row.pvKw, gridLimit)
    const rawGridKw = row.pvKw - chargeKw + dischargeKw
    const gridKw = clamp(Math.min(rawGridKw, gridLimit), 0, gridLimit)
    const curtailmentKw = Math.max(0, rawGridKw - gridLimit)
    const revenueEur = gridKw * row.priceEurMwh / 1000
    const noStorageRevenue = noStorageGridKw * row.priceEurMwh / 1000
    const incrementalRevenueEur = revenueEur - noStorageRevenue
    const throughput = chargeKw + dischargeKw
    const degradationCost = throughput / 1000 * 6
    totalRevenueEur += revenueEur
    noStorageRevenueEur += noStorageRevenue
    degradationCostEur += degradationCost
    throughputKwh += throughput
    pvEnergyKwh += row.pvKw
    curtailedKwh += curtailmentKw
    noStorageCurtailedKwh += Math.max(0, row.pvKw - gridLimit)
    return {
      ...row,
      chargeKw,
      dischargeKw,
      gridKw,
      soc,
      socPct: soc * 100,
      socMinPct: socMin * 100,
      socMaxPct: socMax * 100,
      revenueEur,
      noStorageRevenueEur: noStorageRevenue,
      incrementalRevenueEur,
      curtailmentKw,
      degradationCostEur: degradationCost,
    }
  })
  const gridSeries = simulated.map(row => row.gridKw)
  const pvSeries = simulated.map(row => row.pvKw)
  const noStoragePeak = Math.max(...pvSeries, 1)
  const gridPeak = Math.max(...gridSeries, 0)
  const pvRamp = averageRamp(pvSeries)
  const gridRamp = averageRamp(gridSeries)
  const equivalentCycles = throughputKwh / Math.max(energyKwh * 2, 1)
  return {
    rows: simulated,
    kpis: {
      totalRevenueEur,
      noStorageRevenueEur,
      incrementalRevenueEur: totalRevenueEur - noStorageRevenueEur - degradationCostEur,
      degradationCostEur,
      equivalentCycles,
      sohImpact: equivalentCycles * 0.00008,
      curtailmentRate: pvEnergyKwh ? curtailedKwh / pvEnergyKwh : 0,
      curtailmentReductionRatio: noStorageCurtailedKwh ? clamp((noStorageCurtailedKwh - curtailedKwh) / noStorageCurtailedKwh, 0, 1) : 0,
      peakShavingRatio: clamp((noStoragePeak - gridPeak) / noStoragePeak, 0, 1),
      smoothingRatio: pvRamp ? clamp((pvRamp - gridRamp) / pvRamp, 0, 1) : 0,
    },
  }
}

function dispatchDecision(row, index, rows, ctx) {
  if (ctx.algorithm === 'none') return { chargeKw: 0, dischargeKw: 0 }
  const nextPrice = rows[index + 1]?.priceEurMwh ?? row.priceEurMwh
  const pvAboveMean = row.pvKw > ctx.pvMean * 1.08
  const lowPrice = row.priceEurMwh <= ctx.priceLow || row.priceEurMwh < ctx.priceMean && nextPrice >= row.priceEurMwh
  const highPrice = row.priceEurMwh >= ctx.priceHigh
  let chargeKw = 0
  let dischargeKw = 0

  if (ctx.algorithm === 'rule') {
    chargeKw = lowPrice ? ctx.powerKw * 0.62 : 0
    dischargeKw = highPrice ? ctx.powerKw * 0.62 : 0
  } else if (ctx.algorithm === 'multi') {
    chargeKw = (lowPrice || pvAboveMean) ? ctx.powerKw * 0.76 : 0
    dischargeKw = highPrice && row.pvKw < ctx.pvMean * 0.9 ? ctx.powerKw * 0.68 : 0
  } else {
    chargeKw = (lowPrice || pvAboveMean) ? ctx.powerKw * 0.72 : 0
    dischargeKw = highPrice ? ctx.powerKw * 0.72 : 0
  }

  if (ctx.objective === 'economic') {
    dischargeKw *= 1.18
    chargeKw *= lowPrice ? 1.08 : 0.78
  } else if (ctx.objective === 'smooth') {
    chargeKw = Math.max(chargeKw, row.pvKw > ctx.gridLimit * 0.82 ? ctx.powerKw * 0.7 : chargeKw * 0.92)
    dischargeKw *= row.pvKw < ctx.pvMean * 0.55 ? 0.85 : 0.55
  } else {
    chargeKw *= 1
    dischargeKw *= 0.95
  }
  return { chargeKw, dischargeKw }
}

function buildComparisonRows() {
  const rows = experimentRows.value
  const base = appliedExperiment.value
  const variants = [
    { label: '无储能', algorithm: 'none', objective: 'balanced' },
    { label: '固定阈值', algorithm: 'rule', objective: 'economic' },
    { label: '滚动优化', algorithm: 'rolling', objective: 'economic' },
    { label: '多目标优化', algorithm: 'multi', objective: 'balanced' },
  ]
  return variants.map(variant => {
    const result = simulateExperiment(rows, { ...base, algorithm: variant.algorithm, objective: variant.objective })
    return { label: variant.label, ...result.kpis }
  })
}

function buildSensitivityRows() {
  const base = appliedExperiment.value
  const rows = experimentRows.value
  return [0.6, 0.8, 1, 1.2, 1.4].map(factor => {
    const capacity = simulateExperiment(rows, { ...base, batteryEnergyKwh: base.batteryEnergyKwh * factor }).kpis.incrementalRevenueEur
    const power = simulateExperiment(rows, { ...base, batteryPowerKw: base.batteryPowerKw * factor }).kpis.incrementalRevenueEur
    return { label: `${Math.round(factor * 100)}%`, capacityRevenueEur: capacity, powerRevenueEur: power }
  })
}

async function runExperiment() {
  if (runTimer) clearTimeout(runTimer)
  isRunningExperiment.value = true
  experimentRunError.value = null
  const next = normalizeExperimentForm(experimentForm)
  try {
    const result = await runWeatherDispatchExperiment(buildExperimentRequest(next))
    if (!result) throw backendContractError()
    backendExperiment.value = result
    appliedExperiment.value = next
    selectedTimeIndex.value = 0
    experimentRunId.value += 1
    void loadExperimentRuns()
  } catch (e) {
    const normalized = e.normalized || normalizeApiError(e)
    experimentRunError.value = normalized
  } finally {
    isRunningExperiment.value = false
  }
}

function resetExperiment() {
  const next = defaultExperimentForm()
  if (priceScenarios.value.length && !priceScenarios.value.some(item => item.id === next.priceScenario)) next.priceScenario = priceScenarios.value[0].id
  Object.assign(experimentForm, next)
  appliedExperiment.value = normalizeExperimentForm(next)
  experimentRunError.value = null
  selectedTimeIndex.value = 0
}

async function exportExperimentResult(format = 'json') {
  const runId = backendExperiment.value?.run_id
  if (runId && ['json', 'csv'].includes(format)) {
    try {
      const { blob, headers } = await exportWeatherDispatchExperimentRun(runId, format)
      downloadBlob(blob, filenameFromDisposition(headers?.['content-disposition']) || `dispatch_experiment_${runId}.${format}`)
      return
    } catch (e) {
      experimentRunError.value = e.normalized || normalizeApiError(e)
      return
    }
  }

  const payload = buildLocalExportPayload(format)
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  downloadBlob(blob, `dispatch_experiment_${format}_${experimentRunId.value}.json`)
}

function buildLocalExportPayload(format = 'json') {
  const base = {
    generated_at: new Date().toISOString(),
    run_id: backendExperiment.value?.run_id || null,
    boundary: backendExperiment.value?.boundary || {
      is_measured_generation: false,
      is_real_settlement_revenue: false,
      message: '参考仿真：天气估算 PV 出力与可配置电价场景，不代表实测电站发电或实际市场收益。',
    },
    parameters: appliedExperiment.value,
    source: backendExperiment.value?.source || null,
    kpis: experimentKpis.value,
  }
  if (format === 'summary') {
    return {
      ...base,
      comparison: experimentComparisonRows.value,
      summary: `当前方案增量收益 ${formatCurrency(experimentKpis.value.incrementalRevenueEur)}，削峰效果 ${formatPercent(experimentKpis.value.peakShavingRatio)}，平滑效果 ${formatPercent(experimentKpis.value.smoothingRatio)}。`,
    }
  }
  return {
    ...base,
    rows: experimentSeriesRows.value,
    comparison: experimentComparisonRows.value,
    sensitivity: sensitivityRows.value,
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function filenameFromDisposition(value) {
  const match = String(value || '').match(/filename="?([^"]+)"?/i)
  return match?.[1] || ''
}

function buildExperimentRequest(form) {
  return {
    dispatchDate: form.dispatchDate,
    horizonHours: form.horizonHours,
    weatherScenario: form.weatherScenario,
    priceScenario: form.priceScenario,
    batteryEnergyKwh: form.batteryEnergyKwh,
    batteryPowerKw: form.batteryPowerKw,
    chargeEfficiency: form.chargeEfficiency,
    dischargeEfficiency: form.dischargeEfficiency,
    initialSoc: form.initialSoc,
    socMin: form.socMin,
    socMax: form.socMax,
    objective: form.objective,
    algorithm: form.algorithm,
    capacityKw: numberOr(referenceSite.value.pv_capacity_kw_ac, 22000),
  }
}

async function loadExperimentRuns() {
  historyLoading.value = true
  try {
    const summaries = await fetchWeatherDispatchExperimentRuns(20)
    experimentRunHistory.value = summaries.map(row => mergeHistoryDetail(row, null))
  } catch {
    experimentRunHistory.value = []
  } finally {
    historyLoading.value = false
  }
}

function mergeHistoryDetail(summary, detail) {
  const parameters = detail?.parameters || {}
  return {
    ...summary,
    battery_energy_kwh: parameters.battery_energy_kwh,
    battery_power_kw: parameters.battery_power_kw,
    objective: summary.objective || parameters.objective,
    algorithm: summary.algorithm || parameters.algorithm,
  }
}

async function loadExperimentHistoryRun(runId) {
  if (!runId) return
  historyLoading.value = true
  experimentRunError.value = null
  try {
    const result = await fetchWeatherDispatchExperimentRun(runId)
    if (!result) throw backendContractError()
    applyBackendExperiment(result)
  } catch (e) {
    experimentRunError.value = e.normalized || normalizeApiError(e)
  } finally {
    historyLoading.value = false
  }
}

function applyBackendExperiment(result) {
  if (!result) return
  backendExperiment.value = result
  const form = mapBackendParametersToForm(result.parameters)
  appliedExperiment.value = form
  Object.assign(experimentForm, form)
  selectedTimeIndex.value = 0
}

function backendContractError() {
  const message = '后端调度接口返回格式异常，请确认 FastAPI 后端已更新并重启。'
  const error = new Error(message)
  error.normalized = {
    status: 0,
    message,
    errorCode: 'BACKEND_CONTRACT_MISMATCH',
    provider: null,
    retryable: true,
    suggestedAction: '重启后端服务后刷新调度分析页面。',
    requestId: null,
    isAuthError: false,
  }
  return error
}

function mapBackendParametersToForm(parameters = {}) {
  return normalizeExperimentForm({
    dispatchDate: parameters.dispatch_date || defaultExperimentForm().dispatchDate,
    horizonHours: parameters.horizon_hours,
    weatherScenario: parameters.weather_scenario,
    priceScenario: parameters.price_scenario,
    batteryEnergyKwh: parameters.battery_energy_kwh,
    batteryPowerKw: parameters.battery_power_kw,
    chargeEfficiency: parameters.charge_efficiency,
    dischargeEfficiency: parameters.discharge_efficiency,
    initialSoc: parameters.initial_soc,
    socMin: parameters.soc_min,
    socMax: parameters.soc_max,
    objective: parameters.objective,
    algorithm: parameters.algorithm,
  })
}

function handleExperimentChartClick(params) {
  const index = Number(params?.dataIndex)
  if (Number.isInteger(index)) selectedTimeIndex.value = clamp(index, 0, Math.max(experimentSeriesRows.value.length - 1, 0))
}

function normalizeExperimentForm(source) {
  const normalized = { ...defaultExperimentForm(), ...source }
  normalized.horizonHours = [24, 48, 72].includes(Number(normalized.horizonHours)) ? Number(normalized.horizonHours) : 24
  normalized.batteryEnergyKwh = clamp(numberOr(normalized.batteryEnergyKwh, 2000), 500, 8000)
  normalized.batteryPowerKw = clamp(numberOr(normalized.batteryPowerKw, 1000), 200, 5000)
  normalized.chargeEfficiency = clamp(numberOr(normalized.chargeEfficiency, 0.95), 0.7, 0.99)
  normalized.dischargeEfficiency = clamp(numberOr(normalized.dischargeEfficiency, 0.95), 0.7, 0.99)
  normalized.socMin = clamp(numberOr(normalized.socMin, 0.1), 0.05, 0.8)
  normalized.socMax = clamp(numberOr(normalized.socMax, 0.9), normalized.socMin + 0.05, 0.98)
  normalized.initialSoc = clamp(numberOr(normalized.initialSoc, 0.5), normalized.socMin, normalized.socMax)
  return normalized
}

function kpiCard(label, value, rawDelta, hint) {
  const positive = Number(rawDelta) >= 0
  return {
    label,
    value,
    delta: hint,
    tone: positive ? 'positive-card' : 'warning-card',
  }
}
function formatSohImpact(value) {
  const n = Math.abs(Number(value))
  if (!Number.isFinite(n)) return 'N/A'
  return n > 0 && n * 100 < 0.01 ? '<0.01%' : formatPercent(n)
}
function decisionLabel(decision) {
  const labels = { reject: '不宜采用', pilot_candidate: '可试点', baseline: '基准方案', analysis_upper_bound: '分析上界' }
  return labels[decision] || decision || '-'
}
function strategyTypeLabel(value) {
  const labels = { baseline: '基准方案', rolling: '滚动优化方案', threshold: '阈值调度方案' }
  return labels[value] || value || '-'
}
function decisionClass(decision) {
  const map = { reject: 'decision-reject', pilot_candidate: 'decision-pilot', baseline: 'decision-baseline', analysis_upper_bound: 'decision-upper' }
  return map[decision] || ''
}
function weatherScenarioLabel(value) {
  return weatherScenarioOptions.find(item => item.value === value)?.label || value || '天气场景'
}
function priceScenarioLabel(value) {
  const labels = {
    solar_duck_curve: '光伏鸭形曲线电价场景',
    duck_curve_proxy: '光伏鸭形曲线电价场景',
    'Solar duck-curve proxy': '光伏鸭形曲线电价场景',
    '光伏鸭形曲线场景': '光伏鸭形曲线电价场景',
    tou_peak_valley: '峰谷分时电价场景',
    flat_proxy_30: '固定电价场景',
    flat_price: '固定电价场景',
    high_volatility_stress: '高波动电价场景',
    synthetic_scenario: '合成电价场景',
  }
  const raw = String(value || '').trim()
  return labels[raw] || labels[raw.toLowerCase()] || raw || '电价场景'
}
function objectiveLabel(value) {
  return objectiveOptions.find(item => item.value === value)?.label || value || '优化目标'
}
function algorithmLabel(value) {
  return algorithmOptions.find(item => item.value === value)?.label || value || '优化算法'
}
function comparisonSchemeLabel(value, index = 0) {
  const fallback = ['无储能', '固定阈值', '滚动优化', '多目标优化']
  const raw = String(value || '').trim()
  const labels = {
    none: '无储能',
    no_storage: '无储能',
    rule: '固定阈值',
    threshold: '固定阈值',
    rolling: '滚动优化',
    multi: '多目标优化',
    balanced: '多目标优化',
    无储能: '无储能',
    固定阈值: '固定阈值',
    滚动优化: '滚动优化',
    多目标优化: '多目标优化',
  }
  return labels[raw] || labels[raw.toLowerCase()] || fallback[index] || raw || '方案'
}
function comparisonRowClassName({ row }) {
  return row?.recommended ? 'recommended-row' : ''
}
function dedupeHistoryRows(rows) {
  const seen = new Set()
  return rows.filter(row => {
    const key = row.run_id || `${row.created_at || ''}|${row.weather_scenario || ''}|${row.price_scenario || row.price_scenario_label || ''}|${row.horizon_hours || ''}|${row.algorithm || ''}|${row.objective || ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}
function historyParameterSummary(row) {
  const storage = row.battery_energy_kwh && row.battery_power_kw
    ? `${formatKwh(row.battery_energy_kwh)} / ${formatKw(row.battery_power_kw)}`
    : '2.0 MWh / 1.0 MW'
  return `${storage}，${objectiveLabel(row.objective)}，${algorithmLabel(row.algorithm)}`
}
function isTrue(value) { return value === true || value === 'True' || value === 'true' || value === 1 || value === '1' }
function numberOr(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}
function toNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}
function clamp(value, min, max) {
  return Math.min(Math.max(Number(value), min), max)
}
function quantile(values, q) {
  if (!values.length) return 0
  const index = Math.min(values.length - 1, Math.max(0, Math.floor((values.length - 1) * q)))
  return values[index]
}
function averageRamp(values) {
  if (values.length < 2) return 0
  let total = 0
  for (let i = 1; i < values.length; i += 1) total += Math.abs(values[i] - values[i - 1])
  return total / (values.length - 1)
}
function syntheticPrice(index, scenario) {
  const hour = index % 24
  if (scenario === 'flat_proxy_30') return 30
  if (scenario === 'high_volatility_stress') return hour >= 18 && hour <= 21 ? 140 : hour >= 10 && hour <= 15 ? -20 : 35
  if (scenario === 'tou_peak_valley') return hour >= 18 && hour <= 22 ? 90 : hour <= 6 ? 18 : 42
  return hour >= 17 && hour <= 22 ? 105 : hour >= 10 && hour <= 15 ? 5 : 38
}
function emptyKpis() {
  return {
    totalRevenueEur: 0,
    noStorageRevenueEur: 0,
    incrementalRevenueEur: 0,
    degradationCostEur: 0,
    equivalentCycles: 0,
    sohImpact: 0,
    curtailmentRate: 0,
    curtailmentReductionRatio: 0,
    peakShavingRatio: 0,
    smoothingRatio: 0,
  }
}
function firstWeatherDate() {
  const source = weatherPredictions.value[0]?.weather_valid_time || weatherPredictions.value[0]?.timestamp
  if (!source) return ''
  const date = new Date(source)
  return Number.isNaN(date.getTime()) ? '' : date.toISOString().slice(0, 10)
}
function formatDateHour(value) {
  return formatReferenceSiteHour(value)
}
function formatRunDateSecond(value) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value || '-')
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
function formatNumber(value, digits = 2) {
  const n = toNumber(value)
  return n === null ? 'N/A' : n.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits })
}
function formatCurrency(value) {
  return formatYuanFromEur(value, 2, 'N/A')
}
function formatPercent(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `${(n * 100).toFixed(1)}%`
}
function formatPercentWhole(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `${n.toFixed(0)}%`
}
function formatTemperature(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `${n.toFixed(1)} ℃`
}
function formatIrradiance(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `${n.toFixed(0)} W/m2`
}
function formatWind(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `${n.toFixed(1)} m/s`
}
function formatPrice(value) {
  return formatYuanPerMwhFromEur(value, 1, 'N/A')
}
function formatKw(value) {
  const n = toNumber(value)
  if (n === null) return 'N/A'
  return n >= 1000 ? `${(n / 1000).toFixed(1)} MW` : `${n.toFixed(0)} kW`
}
function formatMw(value) {
  const n = toNumber(value)
  return n === null ? 'N/A' : `${(n / 1000).toFixed(1)} MW_AC`
}
function formatKwh(value) {
  const n = toNumber(value)
  if (n === null) return 'N/A'
  return n >= 1000 ? `${(n / 1000).toFixed(1)} MWh` : `${n.toFixed(0)} kWh`
}
async function optionalRequest(request) {
  try { return await request() } catch { return null }
}
async function loadScorecard() {
  loading.value = true
  error.value = null
  try {
    const [scorecardData, reportData, dispatchData, sensitivityData, degradationData, weatherReportData, weatherData, priceData, resultsData, weatherMetricsData, showcaseData] = await Promise.all([
      optionalRequest(fetchGovernanceScorecard),
      optionalRequest(fetchRawhideReport),
      optionalRequest(fetchRawhideDispatchMetrics),
      optionalRequest(fetchRawhideSensitivityMetrics),
      optionalRequest(fetchRawhideDegradationMetrics),
      optionalRequest(fetchStage21Report),
      optionalRequest(fetchStage21WeatherPredictions),
      optionalRequest(fetchStage21PriceScenarios),
      optionalRequest(fetchStage21DispatchResults),
      optionalRequest(fetchStage21DispatchMetrics),
      optionalRequest(fetchShowcaseScenarios),
    ])
    scorecard.value = scorecardData || []
    rawhideReport.value = reportData
    referenceMetrics.value = dispatchData || []
    configMetrics.value = sensitivityData || []
    degradationMetrics.value = degradationData || []
    weatherReport.value = weatherReportData
    weatherPredictions.value = weatherData || []
    priceScenarioRows.value = priceData || []
    dispatchResults.value = resultsData || []
    weatherDispatchMetrics.value = weatherMetricsData || []
    showcaseScenarios.value = Array.isArray(showcaseData) ? showcaseData : []
    resetExperiment()
    void loadExperimentRuns()
    await runExperiment()
  } catch (e) {
    error.value = e.normalized || normalizeApiError(e)
  } finally {
    loading.value = false
  }
}
onMounted(loadScorecard)
onBeforeUnmount(() => {
  if (runTimer) clearTimeout(runTimer)
})
</script>

<style scoped>
.dispatch { display: flex; flex-direction: column; gap: var(--space-lg); }
.dispatch-tabs { min-width: 0; }
.dispatch :deep(.dispatch-tabs > .el-tabs__header) { display: none; }
.dispatch-tabs :deep(.el-tabs__nav) { display: flex; }
.dispatch-tabs :deep(.el-tabs__item[aria-controls="pane-weather-price"]) { order: -4; }
.dispatch-tabs :deep(.el-tabs__item[aria-controls="pane-conclusion"]) { order: -3; }
.dispatch :deep(.chart-header),
.dispatch :deep(.section-header) {
  border-bottom: 1px solid #ebeef5;
  margin: 0;
  min-height: 58px;
  padding: 0 20px;
}
.dispatch :deep(.chart-header h3),
.dispatch :deep(.section-header h3) {
  color: #303133;
  font-size: 15px;
  font-weight: 600;
}
.tab-stack,
.experiment-stack { display: flex; flex-direction: column; gap: var(--space-lg); }
:deep(.el-tabs__nav-wrap::after) { background: var(--border-glass); }
:deep(.el-tabs__item) { color: var(--text-secondary); font-weight: 700; }
:deep(.el-tabs__item.is-active) { color: var(--accent-cyan); }
:deep(.el-tabs__active-bar) { background: var(--accent-cyan); }
:deep(.el-input-number),
:deep(.el-select),
:deep(.el-date-editor.el-input) { width: 100%; }
.reference-hero,
.experiment-hero { align-items: center; display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 0.6fr); gap: var(--space-lg); padding: var(--space-xl); }
.experiment-hero { display: none; }
.experiment-hero.compact-hero { display: none; }
.experiment-hero h2,
.reference-hero h2 { color: var(--text-primary); font-size: 26px; line-height: 1.2; margin-bottom: 8px; }
.compact-hero h2 { font-size: 22px; margin-bottom: 6px; }
.experiment-hero p,
.reference-hero p,
.chart-note,
.boundary-panel p,
.pareto-summary p,
.moment-card p,
.run-state { background: var(--bg-input); border: 1px solid var(--border-glass); border-radius: var(--radius-md); padding: var(--space-lg); }
.inline-state { min-width: 220px; padding: 12px; }
.run-state.running { border-color: rgba(0, 212, 255, 0.48); box-shadow: 0 0 0 1px rgba(0, 212, 255, 0.08); }
.run-state strong { color: var(--accent-cyan); display: block; font-size: 20px; line-height: 1.2; }
.run-state span,
.control-field span,
.reference-facts span,
.revenue-card span,
.kpi-card span,
.moment-card span,
.weather-now-grid span { color: var(--text-secondary); display: block; font-size: 12px; line-height: 1.4; }
.control-field span { color: var(--text-secondary); font-weight: 600; margin-bottom: 6px; }
.reference-facts span,
.revenue-card span,
.kpi-card span,
.moment-card span,
.weather-now-grid span { color: var(--text-tertiary); }
.inline-state strong { font-size: 18px; }
.run-state small,
.revenue-card small,
.kpi-card small { color: var(--text-tertiary); display: block; font-size: 11px; margin-top: 8px; }
.kicker,
.section-title span,
.pareto-summary span { color: var(--accent-cyan); display: block; font-size: 11px; font-weight: 800; letter-spacing: 0.08em; margin-bottom: 6px; text-transform: uppercase; }
.section-title h3 { color: var(--text-primary); font-size: 18px; font-weight: 700; }
.boundary-panel { border-color: rgba(255, 167, 38, 0.28); padding: var(--space-md) var(--space-lg); }
.boundary-panel strong { color: var(--accent-orange); display: block; font-size: 13px; margin-bottom: 4px; }
.experiment-error { border-color: rgba(255, 82, 82, 0.36); padding: var(--space-md) var(--space-lg); }
.experiment-error strong { color: var(--accent-red); display: block; font-size: 13px; margin-bottom: 4px; }
.experiment-error p { color: var(--text-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.experiment-error small { color: var(--text-tertiary); display: block; font-size: 11px; line-height: 1.5; margin-top: 4px; }
.control-panel { padding: 0; }
.control-header { align-items: center; border-bottom: 1px solid #ebeef5; display: flex; gap: var(--space-md); justify-content: space-between; min-height: 58px; padding: 0 20px; }
.section-title p,
.tab-description,
.chart-caption,
.table-note { color: var(--text-tertiary); font-size: 12px; line-height: 1.55; margin-top: 6px; }
.experiment-left > :deep(.chart-card:first-child) .tab-description { display: none; }
.text-help { background: transparent; border: 0; color: var(--accent-cyan); cursor: help; font-size: 12px; margin-top: 6px; padding: 0; }
.control-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: var(--space-md); padding: 20px 20px 0; }
.storage-grid { border-top: 1px solid #ebeef5; display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: var(--space-md); margin: 18px 20px 0; padding-top: 18px; }
.control-field { min-width: 0; }
.action-row { align-items: center; display: flex; flex-wrap: wrap; gap: var(--space-sm); justify-content: flex-end; padding: 18px 20px 20px; }
.experiment-main { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: var(--space-lg); }
.experiment-left { display: flex; flex-direction: column; gap: var(--space-lg); min-width: 0; }
.weather-input-grid { display: grid; grid-template-columns: 220px minmax(340px, 1fr) minmax(340px, 1fr); gap: var(--space-lg); }
.weather-summary { padding: var(--space-lg); }
.weather-summary h3 { color: var(--text-primary); font-size: 20px; margin-bottom: var(--space-md); }
.weather-now-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
.weather-now-grid div { background: var(--bg-input); border: 1px solid var(--border-glass); border-radius: var(--radius-sm); padding: 10px; }
.weather-now-grid strong { color: var(--text-primary); font-size: 15px; }
.kpi-panel { align-self: start; padding: 0; position: sticky; top: var(--space-lg); }
.kpi-panel .section-title { border-bottom: 1px solid #ebeef5; min-height: 58px; padding: 14px 20px 0; }
.kpi-section { border-top: 0; margin: 0; padding: 18px 20px 0; }
.kpi-section h4 { color: var(--text-primary); font-size: 13px; margin-bottom: 8px; }
.kpi-grid { display: grid; grid-template-columns: 1fr; gap: 8px; }
.compact-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.kpi-card { background: #f7f8fa; border: 0; border-radius: 2px; padding: 16px; }
.kpi-card strong { color: #409eff; display: block; font-size: 24px; line-height: 1.2; white-space: nowrap; }
.kpi-card.positive-card strong { color: var(--accent-green); }
.kpi-card.warning-card strong { color: var(--accent-orange); }
.moment-card { border-top: 1px solid #ebeef5; margin: 16px 20px 0; padding: 16px 0 20px; }
.moment-card strong { color: var(--accent-cyan); display: block; font-size: 16px; margin-bottom: 6px; }
.mini-tabs { min-width: 360px; }
.mini-tabs :deep(.el-tabs__header) { margin: 0; }
.mini-tabs :deep(.el-tabs__item) { font-size: 12px; height: 28px; line-height: 28px; }
.analysis-grid { display: grid; grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr); gap: var(--space-lg); }
.analysis-panel { min-width: 0; padding: var(--space-lg); }
.history-panel { min-width: 0; padding: var(--space-lg); }
.history-table { margin-top: var(--space-md); }
.scheme-name { color: var(--text-primary); font-weight: 700; }
.showcase-detail {
  display: grid;
  gap: 8px 14px;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  padding: 12px 16px;
}
.showcase-detail span {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.55;
}
.showcase-detail strong { color: var(--text-primary); }
.scenario-explain-list {
  border-top: 1px solid var(--border-glass);
  display: grid;
  gap: 10px;
  margin-top: var(--space-md);
  padding-top: var(--space-md);
}
.scenario-explain-list h4 {
  color: var(--text-primary);
  font-size: 13px;
  margin: 0;
}
.scenario-explain-item {
  background: var(--bg-input);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-sm);
  padding: 12px;
}
.scenario-explain-item strong {
  color: var(--text-primary);
  display: block;
  font-size: 13px;
  margin-bottom: 4px;
}
.scenario-explain-item p {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
  margin: 0;
}
.scenario-explain-item small {
  color: var(--text-tertiary);
  display: block;
  font-size: 11px;
  line-height: 1.5;
  margin-top: 6px;
}
.scheme-badge { background: rgba(255,255,255,0.08); border-radius: var(--radius-full); color: var(--text-secondary); display: inline-flex; font-size: 11px; font-weight: 700; padding: 2px 8px; white-space: nowrap; }
.scheme-badge.recommended { background: rgba(0, 255, 136, 0.14); color: var(--accent-green); }
.scheme-badge.not-recommended { background: rgba(255, 82, 82, 0.12); color: var(--accent-red); }
:deep(.recommended-row) { background: rgba(0, 255, 136, 0.04); }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-lg); }
.revenue-card { padding: var(--space-lg); }
.revenue-card strong { display: block; font-size: 20px; line-height: 1.25; white-space: nowrap; }
.revenue-basis { color: var(--text-tertiary); font-size: 11px; line-height: 1.55; list-style: none; margin-top: 10px; padding: 0; }
.money { white-space: nowrap; }
.positive { color: var(--accent-green) !important; }
.negative { color: var(--accent-red) !important; }
.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-lg); }
.chart { height: 320px; width: 100%; }
.compact-chart { height: 260px; }
.input-chart { height: 300px; }
.large-chart { height: 390px; }
.reference-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.reference-facts div { background: var(--bg-input); border: 1px solid var(--border-glass); border-radius: var(--radius-sm); padding: 12px; }
.reference-facts strong { color: var(--text-primary); display: block; font-size: 15px; white-space: nowrap; }
.pareto-summary { border-bottom: 1px solid var(--border-glass); margin-bottom: var(--space-md); padding-bottom: var(--space-md); }
.pareto-summary strong { color: var(--text-primary); display: block; font-size: 16px; margin-bottom: 4px; }
.strategy-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--space-lg); }
.strategy-card { border-top: 3px solid var(--text-tertiary); padding: var(--space-lg); }
.strategy-card.decision-upper { border-top-color: var(--accent-green); }
.strategy-card.decision-pilot { border-top-color: var(--accent-cyan); }
.strategy-card.decision-baseline { border-top-color: var(--accent-orange); }
.strategy-card.decision-reject { border-top-color: var(--accent-red); }
.sc-header { align-items: center; display: flex; justify-content: space-between; margin-bottom: 8px; }
.sc-decision { background: rgba(255,255,255,0.08); border-radius: var(--radius-full); color: var(--text-secondary); font-size: 11px; font-weight: 700; padding: 2px 10px; }
.sc-score { font-size: 22px; }
.strategy-card h4 { color: var(--text-primary); font-size: 13px; margin-bottom: 2px; }
.sc-type { color: var(--text-tertiary); font-size: 11px; margin-bottom: 12px; }
.sc-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; }
.sc-metrics div { display: flex; flex-direction: column; }
.sc-metrics span { color: var(--text-tertiary); font-size: 10px; }
.sc-metrics strong { color: var(--text-primary); font-size: 14px; white-space: nowrap; }
.sc-reason { border-top: 1px solid var(--border-glass); color: var(--text-secondary); font-size: 11px; line-height: 1.5; padding-top: 10px; }

@media (max-width: 1399px) {
  .control-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .storage-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .weather-input-grid { grid-template-columns: 1fr 1fr; }
  .weather-summary { grid-column: 1 / -1; }
  .weather-now-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }
}

@media (max-width: 1199px) {
  .reference-hero,
  .experiment-hero,
  .control-header,
  .experiment-main,
  .analysis-grid,
  .chart-row { grid-template-columns: 1fr; }
  .control-header { display: grid; }
  .kpi-panel { position: static; }
}

@media (max-width: 767px) {
  .control-grid,
  .storage-grid,
  .weather-input-grid,
  .weather-now-grid,
  .reference-facts,
  .sc-metrics { grid-template-columns: 1fr; }
  .experiment-hero,
  .reference-hero,
  .boundary-panel,
  .control-panel,
  .revenue-card,
  .kpi-panel,
  .analysis-panel { padding: var(--space-md); }
  .history-panel { padding: var(--space-md); }
  .mini-tabs { min-width: 0; width: 100%; }
  .chart,
  .compact-chart,
  .input-chart,
  .large-chart { height: 300px; }
}
</style>
