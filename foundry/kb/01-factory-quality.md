# Factory / Quality Knowledge Base

For the Factory/Quality specialist agent. Explains what the production
telemetry means, since the structured tables (`gold_defect_rate_by_stage_daily`,
`gold_line_throughput_hourly()`, `gold_batch_summary()`,
`gold_factory_oee_daily()`, and the underlying Silver tables) carry
numbers, not explanations.

## The six in-factory stages

Every batch moves through these in order, on one production line:
Grinding → Mixing & Refining → Conching → Tempering → Molding & Cooling →
Packaging. See `00-company-overview.md` for what each stage physically
does.

## Sensor metrics reference

Each stage streams its own set of metrics (`sensor_reading.Metric`,
pivoted into named columns in the matching `silver_<stage>` table). The
ranges below are normal operating bounds — a reading outside its range
indicates a potential quality or equipment issue, not necessarily a
defect on its own.

### Grinding

| Metric | Unit | Normal range | What it means |
|---|---|---|---|
| ParticleSizeMicron | µm | 18–35 | Coarseness of the ground liquor. Too coarse (>35) means chocolate will taste gritty downstream |
| MotorTemperatureC | °C | 55–75 | Grinder motor temperature. Sustained high readings suggest mechanical wear |
| ThroughputKgPerHr | kg/h | 180–260 | Feed rate. Low throughput reduces the line's effective capacity |

### Mixing & Refining

| Metric | Unit | Normal range | What it means |
|---|---|---|---|
| ParticleSizeMicron | µm | 15–25 | Finer than Grinding's — this stage's job is to refine further |
| RollerTemperatureC | °C | 40–55 | Refiner roller temperature |
| ViscosityPaS | Pa·s | 2–6 | Mixture thickness. Too high can indicate under-refined material or excess cocoa butter loss |

### Conching

| Metric | Unit | Normal range | What it means |
|---|---|---|---|
| TemperatureC | °C | 45–70 | Conche temperature. Runs longest of any stage (hours) to develop flavor |
| MoisturePercent | % | 0.5–1.5 | Residual moisture. Too high affects shelf life and texture |
| AcidityPH | pH | 5.0–5.8 | Conching reduces volatile acids from fermentation; a pH outside range suggests conching time or temperature was off |

### Tempering

| Metric | Unit | Normal range | What it means |
|---|---|---|---|
| TemperatureC | °C | 27–32 | The narrowest, most critical range in the whole line — this is the crystallization curve that gives chocolate its snap and gloss |
| CrystalFormIndex | index (0–6) | 3–6 | Cocoa-butter crystal form. Target is Form V (~5); off-target values predict bloom (dull, streaky finish) after cooling |
| ViscosityPaS | Pa·s | 2–4 | |

### Molding & Cooling

| Metric | Unit | Normal range | What it means |
|---|---|---|---|
| MoldTemperatureC | °C | 10–15 | |
| TunnelTemperatureC | °C | 8–12 | Cooling tunnel temperature. Too fast/cold a cool-down can cause cracking or condensation (sugar bloom) |
| VibrationHz | Hz | 40–60 | De-airing vibration. Insufficient vibration leaves air bubbles in the finished bar |

### Packaging

| Metric | Unit | Normal range | What it means |
|---|---|---|---|
| LineSpeedUnitsPerMin | units/min | 60–120 | |
| SealTemperatureC | °C | 130–160 | Wrapper seal temperature. Out-of-range seals risk failing in transit |
| RejectRatePercent | % | 0–3 | Units rejected by inline inspection before shipment |

## Quality checks

`quality_check` rows are emitted at the end of every stage for every
batch, carrying a `DefectRate` (0–1) computed from how far that stage's
readings sat outside normal range. `Result` is `"Fail"` when
`DefectRate > 0.3`, otherwise `"Pass"`. A `Notes` field flags
`"anomaly injected"` when the underlying reading was itself an injected
anomaly (demo/testing data only — not a real quality signal in
production).

`gold_defect_rate_by_stage_daily` rolls these up by factory, stage, and
day — the first place to look for "where are we losing chocolate."

## Batches

A batch is one production run through all six stages on one line.
`batch_event` rows mark `Started` (first tick of Grinding) and
`Completed` (last tick of Packaging); `silver_batch` derives `Status`
(`InProgress` or `Completed`) and timing directly from these events — not
inferred from sensor data. `gold_batch_summary()` adds cycle time and
aggregated defect stats per batch.

## Line status and downtime

A line is either `Running` or `Down`. While down, it produces nothing —
no sensor readings, no quality checks. `Reason` on a `Down` event is one
of: `Scheduled Maintenance`, `Unplanned Stop`, or `Changeover` (switching
between recipes/products). `Unplanned Stop` is the one worth flagging
proactively; the other two are expected operational pauses.

## OEE — Overall Equipment Effectiveness

`gold_factory_oee_daily()` computes OEE as **Availability × Performance ×
Quality**, per factory per day:

- **Availability** = 1 − (total downtime minutes ÷ 1440), i.e. the
  fraction of the day a factory's lines were actually running.
- **Performance** = average Grinding throughput ÷ 260 kg/h (the ideal
  rate — Grinding's normal-range upper bound), capped at 1.0.
- **Quality** = 1 − average defect rate for the day.

As a rule of thumb, world-class manufacturing OEE is considered to be
around 85%; anything notably below that across a full day is worth
investigating, and a sharp single-day drop is more often a downtime
event (see `line_status`) than a quality problem.
