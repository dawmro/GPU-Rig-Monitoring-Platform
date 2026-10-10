
# GPU-Rig-Monitoring-Platform — Analytics Timeseries Features
## Updated Analysis & Architecture Plan

## 1. Executive Summary

This plan covers the next stage of statistical analytics for
GPU-Rig-Monitoring-Platform.

The original planned derived charts and report metrics are considered
implemented and are excluded from the new implementation backlog.
The focus is now on statistical analysis that provides additional
information about reliability, performance stability, workload
behavior, anomalies, multi-GPU balance, and data quality.

### 1.1 Existing implementation baseline

The following features belong to the completed baseline, not the new
backlog:

- Cooling Efficiency Index
- Fan-Adjusted Cooling Response Index
- VRAM Bandwidth Saturation Index
- CPU-to-GPU Power Ratio
- Cooling Efficiency Index report aggregation
- Fan-Adjusted Cooling Response report aggregation
- Temperature-to-PowerLimit Ratio Stability
- Memory vs. Core Utilization Correlation
- Compute-to-Memory Ratio Trend
- Clock Stability Index
- Frequency-to-PowerLimit Ratio Stability
- Idle-to-Peak Power Delta
- Idle Power Waste Ratio
- Job State Transition Frequency
- Underutilization Duration
- Power-on Hours Before Restart
- Thermal Degradation Slope
- Cost per Active GPU-Hour
- Idle Power Waste Cost

These existing metrics must not be reimplemented under different names.
New statistics may reuse their underlying data and results where
appropriate, but should provide distinct analytical value.

### 1.2 Goals

1. Describe normal operating behavior using robust statistics.
2. Detect unusual readings and persistent behavioral changes.
3. Compare GPUs under comparable workloads.
4. Identify imbalances between GPUs and rigs.
5. Quantify telemetry completeness and statistical confidence.
6. Improve the reliability of existing reports and future alerts.
7. Avoid unnecessary database fields, ingest overhead, and duplicated
   calculations.

### 1.3 Report windows

Use the existing report windows consistently:

- 24 hours
- 7 days
- 30 days

Every statistic must specify its actual observation window, number of
valid observations, and any material data-quality limitations.

---

## 2. Architecture Decisions

### 2.1 Mode A — Pre-computed time-series metrics

Use ingest-time computation only when a value can be calculated
reliably from one payload or a small, explicitly defined stateful
calculation.

Suitable examples include simple derived values and detector state
that must be updated continuously.

For the new statistical backlog, most distribution statistics and
windowed analyses do not belong in this category.

Advantages:
- Immediate access to derived values.
- Useful for continuously updated detector state.
- Can reduce repeated computation.

Disadvantages:
- Additional writes and ingest complexity.
- Stateful calculations need reset and recovery behavior.
- Historical corrections and missing observations complicate results.

Decision:
Do not add a database field for every new statistic. Introduce
pre-computed state only when continuous detection, alerting, or
measured performance requirements justify it.

### 2.2 Mode B — On-demand report calculations

Calculate report statistics from historical GPUMetric and MetricSnapshot
records when the report is requested.

Use the existing report-context and caching architecture.

Suitable for:
- Percentiles and distribution summaries.
- Variability statistics.
- Outlier rates.
- Conditional distributions.
- Regression diagnostics.
- Fleet comparisons.
- Confidence intervals and sample adequacy.

Advantages:
- No additional ingest-time processing for ordinary reports.
- Existing data can be analyzed without new sensor fields.
- Statistics can evolve without adding database migrations.

Disadvantages:
- Query cost depends on the range, number of GPUs, and statistic.
- Some analyses require more than ordinary SQL aggregation.
- Compacted observations may not preserve the information required
  for exact statistical calculations.

Decision:
Prefer on-demand calculation for the new report metrics. Cache
expensive calculations using the established report-cache pattern.

### 2.3 Mode C — Background time-series analysis

Use background jobs for computationally expensive or stateful analysis
when on-demand calculations become too expensive.

Potential workloads:
- Change-point detection.
- CUSUM monitoring.
- Seasonal analysis.
- Repeated fleet-wide regression.
- Multivariate anomaly detection.
- Historical recovery-time analysis.

Use the existing background-task infrastructure where appropriate.
Persist detector state or results only when needed for alerting,
historical event records, or avoiding repeated expensive calculations.

Do not introduce a background pipeline solely because a statistic
sounds advanced; profile its real execution cost first.

---

## 3. Data Sources and Time-Series Correctness

### 3.1 GPUMetric

Use GPUMetric as the primary source for per-GPU analysis, including:

- gpu_util_pct
- mem_controller_util_pct
- gpu_temp_c
- power_draw_w
- gpu_core_clock_mhz
- gpu_memory_clock_mhz
- fan_speed_pct
- Other available GPU-level telemetry

Use only fields that exist in the current model and have sufficient
valid observations.

### 3.2 MetricSnapshot

Use MetricSnapshot for system-level or job-state-dependent analysis,
including available fields such as:

- has_active_job
- uptime_s
- total_system_power_w
- cpu_power_w
- System-level error and resource telemetry

Use GPU-specific data from MetricSnapshot only when the actual model
schema and collection semantics support that usage.

Do not substitute MetricSnapshot for GPUMetric when the analysis
specifically requires per-GPU historical observations.

### 3.3 Compaction requirements

The existing timeseries compaction strategy must be considered when
designing every statistic.

Important distinction:

- Averages and some other aggregate statistics can be calculated
  from compacted data when the relevant aggregation values are
  retained.
- Exact percentiles, MAD, IQR, outlier counts, and distribution
  shapes cannot generally be reconstructed from averages alone.
- Autocorrelation, change points, event durations, and lagged
  relationships can be distorted by compaction.
- Sample counts, time-weighted statistics, and event boundaries
  require explicit consideration.

For every proposed statistic, specify whether it uses:

1. Raw observations.
2. Compacted observations.
3. Sufficient statistics retained during compaction.
4. Background-computed results.

Do not label a statistic exact when it is only an approximation
derived from compacted data.

For 7-day and 30-day reports, profile query cost before deciding
whether raw data, compacted buckets, or a separate analytical
aggregation is appropriate.

### 3.4 Missing data and sampling intervals

All calculations must distinguish:

- Zero: a valid measured zero.
- Null: unavailable or invalid measurement.
- Missing observation: no usable sample was recorded.
- Unobserved duration: an interval for which behavior is unknown.

Use actual timestamps rather than assuming every pair of records is
exactly one minute apart.

Do not infer hardware downtime solely from missing telemetry.

---

## 4. New Statistical Analysis Metrics

# Phase 1 — Core Distribution Statistics and Data Quality

These metrics establish the statistical foundations for subsequent
anomaly detection and fleet analytics.

### 4.1 GPU Utilization Percentiles

Priority: P1

Definition:
Calculate P50, P75, P90, P95, and P99 of gpu_util_pct per GPU
over each report window.

Data source:
GPUMetric.gpu_util_pct

Output:
Percentiles, valid sample count, and coverage.

Example:
A GPU has average utilization of 65% and P95 utilization of 99%.
The average alone hides frequent periods near full utilization.

Interpretation:
- P50 describes typical utilization.
- P95 and P99 characterize high-load behavior.
- A large difference between P50 and P95 suggests a bursty or
  highly variable workload.

Caveat:
Sample percentiles are not automatically time-weighted. Irregular
sampling can bias the distribution.

Implementation:
Use a percentile calculation over valid observations. Define
whether raw samples or a documented approximation are used.

### 4.2 GPU Utilization Coefficient of Variation

Priority: P1

Definition:

CV = standard deviation(utilization) / mean(utilization)

Data source:
GPUMetric.gpu_util_pct

Output:
CV per GPU and report window.

Interpretation:
A higher CV indicates greater variability relative to the mean.

Caveat:
CV is misleading when mean utilization is near zero. Return null
below a defined minimum mean and show an absolute variability
measure alongside it.

Do not interpret a high CV as a fault without considering workload.

### 4.3 Median Absolute Deviation (MAD)

Priority: P1

Definition:

MAD = median(abs(x - median(x)))

Calculate independently for supported fields such as:

- gpu_util_pct
- gpu_temp_c
- power_draw_w
- gpu_core_clock_mhz
- fan_speed_pct

Output:
MAD per GPU, metric, and time window.

Interpretation:
MAD describes typical absolute deviation from the median and is
less sensitive to extreme observations than standard deviation.

Caveat:
A high MAD indicates variability, not necessarily malfunction.

Implementation:
Calculate from valid observations. Do not reconstruct MAD from
average-only compacted data.

### 4.4 Interquartile Range (IQR)

Priority: P1

Definition:

IQR = P75 - P25

Data source:
GPUMetric

Output:
IQR for utilization, temperature, power, clocks, and other
supported metrics.

Interpretation:
A large IQR indicates a wide middle distribution; a small IQR
indicates more consistent observations.

Caveat:
IQR and MAD overlap. Expose both initially only if they provide
useful distinctions in actual reports.

### 4.5 Robust Outlier Rate

Priority: P1

Definition:
Use the modified Z-score:

M = 0.6745 * (x - median(x)) / MAD

An observation is a candidate outlier when abs(M) > 3.5.

Data source:
GPUMetric

Output:
Outlier count, outlier percentage, and optionally timestamps.

Interpretation:
Quantifies how often observations differ substantially from the
metric's historical distribution.

Caveats:
- A statistical outlier is not automatically a hardware fault.
- Startup, shutdown, workload transitions, and sensor errors can
  produce legitimate outliers.
- If MAD is zero, use an explicitly defined fallback policy rather
  than dividing by zero.
- Avoid using one baseline for incompatible operating states.

### 4.6 General Threshold Exceedance Statistics

Priority: P1

Definition:
Measure how frequently and for how long configurable operational
conditions are satisfied.

Examples:
- Temperature above a configured warning threshold.
- Memory utilization above a configured limit.
- Power above a configured operational threshold.
- Unexpectedly low utilization while a job is active.

Data source:
GPUMetric and MetricSnapshot when job state is required.

Output:
- Event count.
- Total qualifying duration.
- Longest event.
- Percentage of observed eligible time.
- Event start and end timestamps when appropriate.

Implementation:
Use timestamp-aware interval processing. Define event merging,
missing-sample behavior, minimum event duration, and hysteresis.

Overlap rule:
Do not duplicate the existing Underutilization Duration report.
Generalize its interval-processing utilities where useful, but
keep distinct threshold definitions and output semantics.

### 4.7 Telemetry Coverage and Continuity

Priority: P1

Definition:
Quantify the completeness and temporal continuity of observations.

Data source:
Ingest timestamps and metric timestamps for GPUMetric and
MetricSnapshot.

Output:
- Expected and received sample counts, when the expected cadence
  is known.
- Sample coverage percentage.
- Duration-based coverage where reconstructable.
- Longest telemetry gap.
- Median and P95 sample interval.
- Number of significant gaps.

Count-based coverage:

coverage = received_samples / expected_samples * 100

Interpretation:
A report with incomplete observations should not appear equivalent
to a fully observed period.

Caveat:
Count-based coverage can misrepresent actual time coverage when
sampling intervals vary. Prefer duration-based coverage when
interval semantics can be reconstructed reliably.

### 4.8 Minimum Sample Adequacy

Priority: P1

Definition:
Determine whether a statistic has sufficient data for meaningful
interpretation.

Data source:
Shared reporting utilities.

Output:
- Valid sample count.
- Distinct timestamp count.
- Coverage.
- Number of excluded observations.
- Adequacy status.
- Optional reason when the statistic is unavailable.

Examples:
A P99 estimate based on 25 observations is much less reliable than
one based on thousands of representative observations.

Implementation:
Define statistic-specific minimums. Percentiles, regression,
correlation, event duration, and confidence intervals have different
requirements.

Do not silently replace an inadequate result with zero.

---

# Phase 2 — Context-Aware Diagnostics

These metrics compare behavior against relevant operating conditions
instead of relying exclusively on unconditional averages.

### 4.9 Conditional Temperature Distribution

Priority: P1

Definition:
Calculate temperature distributions for comparable operating
conditions.

Data source:
GPUMetric.gpu_temp_c, gpu_util_pct, power_draw_w, fan_speed_pct

Possible grouping variables:
- GPU utilization ranges.
- Power ranges.
- Fan-speed ranges.
- Combined power and utilization ranges.

Output:
Median temperature, P95 temperature, sample count, and coverage
for each condition group.

Example:
Two GPUs both average 75°C, but one reaches 85°C at 250 W while
the other remains at 75°C under comparable conditions.

Interpretation:
Enables fairer cooling comparisons by reducing workload differences.

Caveats:
- Require adequate observations per group.
- Compare GPUs only over overlapping operating ranges.
- Consider GPU model, power limit, fan policy, and cooler design.
- Do not infer causation from conditional associations.

### 4.10 Regression Residual Analysis

Priority: P1

Definition:
Estimate expected behavior from multiple operating variables and
measure deviations from that expectation.

Example temperature model:

T = b0 + b1*P + b2*U + b3*F + error

Where:
T = temperature
P = GPU power
U = GPU utilization
F = fan speed

Data source:
GPUMetric

Output:
- Observed and predicted temperature.
- Residual temperature.
- Residual standard deviation.
- Standardized residual.
- Sample count and model adequacy.

Example:
A GPU is consistently 8°C hotter than expected for comparable
power, utilization, and fan speed.

Caveats:
- Use compatible GPU models and operating conditions.
- Account for nonlinear effects where necessary.
- Validate model fit before using residuals for alerts.
- Do not treat a residual as proof of a cooling defect.

Implementation:
Start with a simple, validated regression model. Calculate on
demand or cache the result when repeated queries become expensive.

### 4.11 GPU-to-GPU Performance Deviation

Priority: P1

Definition:
Compare a GPU against compatible peers in the same rig or fleet.

For metric x:

deviation = GPU value - peer-group median

A normalized version can use a robust scale estimate such as MAD.

Data source:
GPUMetric, grouped by rig and compatible GPU hardware.

Candidate comparisons:
- Temperature under comparable power and utilization.
- Idle power under verified idle conditions.
- Core clock under comparable load.
- Utilization distribution.
- Robust outlier score.

Output:
Absolute deviation, normalized deviation, peer count, and
comparison conditions.

Caveats:
Account for GPU model, cooling design, PCIe position, workload,
power limits, and airflow.

### 4.12 Fleet Distribution and Fleet Outliers

Priority: P1

Definition:
Summarize the distribution of a metric across comparable GPUs
or rigs.

Data source:
GPUMetric and MetricSnapshot where relevant.

Output:
- Fleet median.
- P10/P90 and P95.
- Outlier rigs or GPUs.
- Number of compatible peers.
- Percentage outside the expected range.

Interpretation:
Identifies unusual devices that may not be obvious when examining
one rig at a time.

Caveats:
Segment by GPU model and relevant operating conditions. Distinguish
between per-sample distributions and distributions of per-GPU
summary values.

### 4.13 Intra-Rig Utilization Imbalance

Priority: P1

Definition:
Quantify differences in GPU utilization across a multi-GPU rig.

One possible measure at timestamp t:

CV_rig = stddev(U1, U2, ..., Un) / mean(U1, U2, ..., Un)

Data source:
GPUMetric.gpu_util_pct

Output:
- Mean and P95 imbalance.
- Maximum observed imbalance.
- Duration above a configurable threshold.
- Number of GPUs contributing valid observations.

Interpretation:
May identify workloads that are unevenly distributed across GPUs.

Caveats:
- Exclude missing GPU observations.
- Handle a near-zero mean explicitly.
- Different GPU roles or workloads can legitimately create imbalance.
- Compare simultaneous observations, not unrelated time averages.

---

# Phase 3 — Time-Series Trends and Change Detection

### 4.14 Exponentially Weighted Moving Average (EWMA)

Priority: P2

Definition:

EWMA_t = lambda*x_t + (1-lambda)*EWMA_(t-1)

Data source:
GPUMetric

Candidate fields:
Temperature, power, utilization, and clocks.

Output:
Smoothed series, baseline deviation, and optionally a standardized
deviation score.

Purpose:
Reduce short-term noise while retaining sensitivity to recent
changes.

Caveats:
The smoothing parameter controls responsiveness. Sampling cadence
must be consistent or explicitly accounted for.

Architecture:
Use as a shared analytical utility and a building block for
anomaly detection, not necessarily as a separate report for every
sensor.

### 4.15 Change-Point Detection

Priority: P2

Definition:
Identify timestamps where the statistical behavior of a metric
changes substantially.

Candidate methods:
- CUSUM.
- PELT.
- Bayesian online change-point detection.

Data source:
Timestamp-aligned GPUMetric observations.

Output:
Change timestamp, affected metric, estimated shift, and detection
score or confidence where supported.

Examples:
- Sustained drop in operating clocks.
- Increased idle power.
- Changed temperature behavior.
- Transition from stable to bursty utilization.

Caveats:
Workload and software changes can also cause legitimate change
points. Correlate detections with operating conditions and known
maintenance events.

Implementation:
Start with a simple detector and validate its false-positive rate
before introducing more sophisticated methods.

### 4.16 CUSUM Shift Detection

Priority: P3

Definition:
Accumulate small deviations from a representative baseline to
detect persistent shifts.

One-sided form:

S_t = max(0, S_(t-1) + x_t - mu0 - k)

Where:
mu0 = expected baseline
k = tolerance parameter

Data source:
GPUMetric

Output:
Cumulative score, threshold crossing timestamp, and event duration.

Example:
A persistent temperature shift of 2°C may be operationally
meaningful even if no single observation crosses a fixed threshold.

Caveat:
The baseline must account for workload and operating conditions.

Implementation:
Initially use CUSUM for a small number of validated metrics instead
of enabling it for every sensor.

### 4.17 Autocorrelation and Lag Analysis

Priority: P3

Definition:
Measure the relationship between a metric and its previous values.

rho(k) = correlation(x_t, x_(t-k))

Data source:
GPUMetric

Output:
Autocorrelation at selected lags and an optional periodicity score.

Use cases:
- Characterize workload persistence.
- Detect repeated utilization cycles.
- Understand the temporal dependence of temperature.
- Improve confidence estimates for other statistics.

Caveats:
High autocorrelation is not itself a fault. Trends and periodic
patterns can distort interpretation. Irregular timestamps require
careful alignment or resampling.

### 4.18 Seasonal and Periodic Behavior

Priority: P3

Definition:
Characterize recurring patterns by hour of day and day of week.

Data source:
Timestamped GPUMetric and optional job-state data.

Output:
- Hour-of-day profiles.
- Weekday versus weekend profiles.
- Typical peak-load periods.
- Deviations from the expected seasonal profile.

Example:
Utilization is usually low overnight, but remains unusually high
for several consecutive nights.

Caveats:
Require sufficient complete daily or weekly cycles. Distinguish
recurring patterns from one-off workload changes.

### 4.19 Forecast Error and Prediction Intervals

Priority: P3

Definition:
Compare observed behavior with a prediction generated from
historical observations.

Residual:

e_t = x_t - predicted_x_t

Candidate statistics:
- Mean absolute error.
- Root mean squared error.
- Percentage of observations outside prediction intervals.

Data source:
GPUMetric

Output:
Prediction error, interval violations, and model evaluation
statistics.

Example:
Temperature repeatedly exceeds the expected range under comparable
power and utilization.

Caveats:
Evaluate predictions on held-out observations or with a rolling
historical baseline. A poorly calibrated model can create false
anomalies.

---

# Phase 4 — Reliability and Advanced Anomaly Detection

### 4.20 Robust Contextual Anomaly Score

Priority: P2

Definition:
Produce a normalized score indicating how unusual an observation
is relative to an appropriate baseline.

Data source:
GPUMetric

Candidate methods:
- Modified Z-score using median and MAD.
- Standardized regression residuals.
- Separate baselines for different workload states.

Output:
Score, severity category, affected metric, timestamp, and baseline
window.

Caveat:
A score is an investigation signal, not an automatic hardware-fault
diagnosis.

Implementation:
Build on metrics 4.5 and 4.10 instead of duplicating their
calculations.

### 4.21 Multivariate Anomaly Score

Priority: P3

Definition:
Detect unusual combinations of individually plausible observations.

Potential features:
Temperature, power, utilization, fan speed, and clocks.

One candidate is Mahalanobis distance:

D² = (x - mu)^T * inverse(Sigma) * (x - mu)

Output:
Anomaly score, contributing features where supported, and baseline
quality.

Caveats:
Requires adequate representative data. Strongly correlated
features and unstable covariance estimates can produce unreliable
scores.

Implementation:
Only introduce after validating simpler univariate and regression-
based detectors.

### 4.22 Clock Throttling Suspicion Score

Priority: P2

Definition:
Identify potentially abnormal clock behavior under comparable
operating conditions.

Candidate signals:
- Lower-than-expected core clock under high utilization.
- Sustained clock deviation from a comparable baseline.
- Relevant temperature or power-limit conditions.
- Explicit throttle-reason telemetry, if available.

Data source:
GPUMetric and available hardware status fields.

Output:
Suspicion score, duration, and supporting observations.

Caveats:
Low clocks can be normal under idle, power-saving, driver, or
workload-limited conditions.

Do not label a GPU as throttled solely because its clock is low.
Use explicit hardware throttle-reason telemetry when available.

### 4.23 Recovery Time After an Anomaly

Priority: P2

Definition:
Measure how long a detected abnormal event takes to return to
a defined normal operating state.

Data source:
GPUMetric, with optional job-state and rig-state data.

Output:
Median, P95, and maximum recovery time; event count; and the
percentage of events with measurable recovery.

Implementation:
Define event-specific recovery conditions and a minimum stable
recovery period. Handle missing observations as unknown intervals.

Do not equate thermal recovery time with recovery from a telemetry
outage or system restart.

### 4.24 Empirical Availability and Observed Downtime

Priority: P2

Definition:
Estimate availability during periods when the platform can
reliably determine whether a GPU or rig was available.

Observed availability:

available duration / eligible observed duration * 100

Data source:
Reliable heartbeat timestamps and explicit availability or
rig-status observations.

Output:
Observed availability, observed downtime, longest outage, and
number of availability events.

Caveats:
- No telemetry does not prove hardware downtime.
- Report unknown time separately unless its cause is established.
- Account for maintenance windows if recorded.
- Do not present this as an SLA guarantee without validated
  availability semantics.

### 4.25 Between-Rig Versus Within-Rig Variability

Priority: P3

Definition:
Separate variation between rigs from variation between GPUs
within a rig and variation over time.

Data source:
GPUMetric grouped by GPU and rig.

Output:
Variance components or a documented decomposition of variation.

Use case:
Determine whether variability is mainly associated with rig-level
differences or individual GPU behavior.

Caveat:
Requires comparable workloads, adequate samples, and a clearly
defined statistical model.

### 4.26 Configuration-Change Impact Analysis

Priority: P3

Definition:
Compare operating behavior before and after a recorded event,
such as a driver update, maintenance action, or cooling change.

Data source:
GPUMetric and timestamps of known configuration changes.

Output:
Changes in median, P95, variability, and conditional regression
residuals.

Example:
After a cooling change, median temperature under comparable power
falls by 4°C while fan speed increases by 3 percentage points.

Caveats:
Workload changes may explain the difference. Match or adjust for
relevant operating conditions. A before-and-after difference does
not establish causation.

Dependency:
This becomes more useful if maintenance and configuration-change
events are recorded consistently.

---

# Phase 5 — Statistical Confidence and Comparison Quality

### 4.27 Confidence Intervals

Priority: P2

Definition:
Quantify uncertainty around a statistic or a comparison between
GPUs.

Candidate outputs:
- Confidence interval for median temperature.
- Confidence interval for mean power.
- Confidence interval for the difference between comparable GPUs.
- Confidence interval for changes before and after maintenance.

Data source:
The relevant GPUMetric or MetricSnapshot observations.

Implementation:
Use bootstrap methods appropriate to the statistic. For
autocorrelated time series, prefer time-block bootstrap or another
method that accounts for temporal dependence.

Caveat:
Do not assume ordinary independent-sample confidence intervals
are reliable for consecutive telemetry samples.

### 4.28 Statistical Significance of Change

Priority: P3

Definition:
Assess whether a difference between two comparable periods is
larger than expected under an appropriate statistical model.

Candidate methods:
- Bootstrap confidence intervals.
- Permutation tests.
- Regression-based comparisons.

Output:
Effect size, confidence interval, and optional significance
statistic.

Caveats:
Statistical significance is not the same as operational
importance. Always report the effect size and uncertainty.

Avoid repeatedly testing many metrics without considering
multiple-comparison effects.

### 4.29 Effective Sample Size

Priority: P3

Definition:
Estimate how much independent information a time series provides
after accounting for temporal dependence.

Data source:
Timestamp-aligned GPUMetric observations and autocorrelation
analysis.

Output:
Estimated effective sample size for a given statistic and window.

Use case:
Improve confidence intervals and determine whether a trend or
correlation has adequate independent information.

Caveat:
The estimate depends on the assumed temporal dependence and
statistical method. It is not interchangeable with the raw
observation count.

---

## 5. Recommended Implementation Order

### Phase 1 — Core statistics

1. GPU utilization percentiles.
2. MAD and IQR utilities.
3. Robust outlier rate.
4. General threshold exceedance statistics.
5. Telemetry coverage and continuity.
6. Minimum sample adequacy.

Goal:
Establish reliable distribution and data-quality primitives.

### Phase 2 — Context-aware and fleet diagnostics

7. Conditional temperature distribution.
8. Regression residual analysis.
9. GPU-to-GPU performance deviation.
10. Fleet distribution and fleet outliers.
11. Intra-rig utilization imbalance.

Goal:
Make reports more actionable and comparisons fairer.

### Phase 3 — Trends and operational reliability

12. EWMA.
13. Change-point detection.
14. Clock throttling suspicion score.
15. Recovery time after anomalies.
16. Empirical availability and observed downtime.
17. Confidence intervals.

Goal:
Identify persistent behavioral changes and quantify their
operational impact.

### Phase 4 — Advanced analytics

18. CUSUM.
19. Autocorrelation and lag analysis.
20. Seasonal behavior.
21. Forecast error and prediction intervals.
22. Multivariate anomaly score.
23. Between-rig versus within-rig variability.
24. Configuration-change impact analysis.
25. Statistical significance of change.
26. Effective sample size.

Goal:
Introduce advanced methods only after the underlying statistics,
data coverage, and baselines have been validated.

---

## 6. Django Implementation Architecture

### 6.1 Shared statistical utilities

Create reusable, tested functions for:

- Percentiles and quantiles.
- Median and MAD.
- IQR and variability.
- Robust outlier scoring.
- Sample adequacy and coverage.
- Timestamp-aware duration calculations.
- Regression and residual statistics.
- Confidence intervals.

Keep the statistical functions independent of HTTP views and
templates where practical.

### 6.2 Report-context integration

Extend the existing report-context builder with calculated values
and metadata.

For each result, return the value together with relevant supporting
information, such as sample count, coverage, and adequacy status.

Reuse existing querysets and aggregations where safe. Avoid
repeating expensive queries for every metric.

Use the existing report-cache pattern, including its current TTL,
unless profiling justifies a different policy.

### 6.3 Database changes

Do not add a model field for every calculated statistic.

Prefer on-demand calculation for ordinary reports.

Consider persistent models or fields only when required for:

- Stateful anomaly detection.
- Alert history.
- Historical event records.
- Expensive calculations that benefit from caching.
- Configuration-change records.

### 6.4 Compaction and system checks

When adding a persistent metric or state field:

1. Update the appropriate Django model.
2. Create and apply the migration.
3. Update the correct COMPACT_TABLES configuration if applicable.
4. Configure valid aggregation semantics.
5. Update the relevant ChartDataView mapping if the metric is charted.
6. Update and run system checks.
7. Test compaction and raw-to-compacted transitions.

Do not assume that adding a field to a model automatically makes it
available through compaction or chart endpoints.

### 6.5 Performance strategy

Start with on-demand calculations over the smallest suitable dataset.

- Use SQL aggregation for statistics that SQL can calculate correctly.
- Use Python for calculations that require ordered observations or
  more specialized statistical methods.
- Use raw samples when the requested statistic cannot be recovered
  from compacted aggregates.
- Introduce background calculations only when measured query cost
  justifies them.
- Test 24-hour, 7-day, and 30-day ranges independently.

Do not approximate event counts, transitions, percentiles, or
correlations without explicitly identifying and validating the
approximation.

---

## 7. Validation and Testing Requirements

Every new statistic must be tested for:

1. Empty data windows.
2. Null and invalid observations.
3. Constant-valued series.
4. Near-zero denominators where applicable.
5. Missing timestamps and irregular intervals.
6. Insufficient samples.
7. Correct per-GPU and per-rig grouping.
8. Correct 24h, 7d, and 30d boundaries.
9. Raw versus compacted data behavior.
10. Numerical stability and expected units.
11. Correct interpretation of zero versus unknown.
12. Acceptable query and response time.

For anomaly detection, additionally test:
- Known injected anomalies.
- Gradual shifts.
- Abrupt shifts.
- Legitimate workload transitions.
- Telemetry gaps.
- False-positive rates under normal operation.

For fleet comparisons, test:
- Identical GPUs.
- Different GPU models.
- Missing peer observations.
- Unequal workloads.
- Different sample counts.

---

## 8. Success Criteria

The new analytics phase is successful when:

- Reports describe variability, not only averages.
- Outlier statistics are robust and explainable.
- Missing telemetry cannot silently appear as healthy operation.
- Comparisons account for relevant workload differences.
- Fleet-level deviations identify actionable candidates for review.
- Statistical results expose inadequate samples and coverage.
- Expensive analytics do not significantly degrade report response
  times.
- Advanced detectors are validated before being used for alerts.
- No completed metric is duplicated under a different name.

## 9. Final Recommendation

Implement Phase 1 first, followed by conditional temperature
analysis and fleet-level deviation.

The most important architectural prerequisite is reliable data
quality and correct handling of compacted timeseries. Percentiles,
outlier rates, event durations, and anomaly scores are only useful
when the observations and the analysis window are trustworthy.

Keep ordinary statistics on demand, reserve persistent state for
genuinely stateful detectors, and introduce advanced anomaly
detection only after the simpler methods have been validated.
