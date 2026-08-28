"""The six in-factory production stages and the sensors on each.

Farm Preparation (harvest/fermentation, drying/roasting, winnowing) happens
off-site near cocoa origin -- see ../../CHOCOLATE-FACTORY.md -- so it
has no stage here. Every factory line runs these six stages, in order, for
every batch. Ranges are grounded in the process descriptions published by
Fauchon and Alain Ducasse (see CHOCOLATE-FACTORY.md for sources).

StageId values match the `production_stage` dimension in
fabric/ontology/ontology_config.json; Metric names become the `Metric` column
of the streamed `sensor_reading` EAV rows, so a new metric here needs no
schema change downstream.
"""

import random
from dataclasses import dataclass, field


@dataclass
class SensorMetricProfile:
    """Normal operating range for one metric, plus how anomalies read."""

    Unit: str
    Min: float
    Max: float
    Variation: float
    DefectFactor: float

    def sample(self, anomaly: bool, variation_multiplier: float = 1.0) -> float:
        value = random.uniform(self.Min, self.Max)

        if anomaly:
            spread = (
                random.uniform(self.Variation * 0.5, self.Variation * 1.5)
                * variation_multiplier
            )
            value = (
                self.Max + spread if random.choice([True, False]) else self.Min - spread
            )

        return round(max(0, value), 3)

    def defect_factor(self, value: float) -> float:
        return max(
            0.0,
            (value - self.Max) / self.DefectFactor,
            (self.Min - value) / self.DefectFactor,
        )


@dataclass
class StageDefinition:
    StageId: str
    Phase: str
    Name: str
    SequenceOrder: int
    TicksPerBatch: int
    Metrics: dict[str, SensorMetricProfile] = field(default_factory=dict)

    def defect_rate(self, readings: dict[str, float]) -> float:
        """Weighted defect rate across this stage's metrics, 0..1."""
        if not readings:
            return 0.0
        factors = [
            profile.defect_factor(readings[name])
            for name, profile in self.Metrics.items()
            if name in readings
        ]
        return round(min(sum(factors) / len(factors), 1.0), 3)


def get_stages() -> "list[StageDefinition]":
    """The six in-factory stages, in process order."""
    return [
        StageDefinition(
            StageId="grinding",
            Phase="Factory Processing",
            Name="Grinding",
            SequenceOrder=1,
            TicksPerBatch=4,
            Metrics={
                "ParticleSizeMicron": SensorMetricProfile("µm", 18, 35, 3, 6),
                "MotorTemperatureC": SensorMetricProfile("°C", 55, 75, 4, 8),
                "ThroughputKgPerHr": SensorMetricProfile("kg/h", 180, 260, 15, 40),
            },
        ),
        StageDefinition(
            StageId="mixing_refining",
            Phase="Factory Processing",
            Name="Mixing & Refining",
            SequenceOrder=2,
            TicksPerBatch=4,
            Metrics={
                "ParticleSizeMicron": SensorMetricProfile("µm", 15, 25, 2, 5),
                "RollerTemperatureC": SensorMetricProfile("°C", 40, 55, 3, 7),
                "ViscosityPaS": SensorMetricProfile("Pa·s", 2, 6, 0.5, 1.5),
            },
        ),
        StageDefinition(
            StageId="conching",
            Phase="Factory Processing",
            Name="Conching",
            SequenceOrder=3,
            TicksPerBatch=8,
            Metrics={
                "TemperatureC": SensorMetricProfile("°C", 45, 70, 4, 10),
                "MoisturePercent": SensorMetricProfile("%", 0.5, 1.5, 0.15, 0.4),
                "AcidityPH": SensorMetricProfile("pH", 5.0, 5.8, 0.1, 0.3),
            },
        ),
        StageDefinition(
            StageId="tempering",
            Phase="Finishing",
            Name="Tempering",
            SequenceOrder=4,
            TicksPerBatch=3,
            Metrics={
                "TemperatureC": SensorMetricProfile("°C", 27, 32, 0.6, 1.2),
                "CrystalFormIndex": SensorMetricProfile("index", 3, 6, 0.4, 1.0),
                "ViscosityPaS": SensorMetricProfile("Pa·s", 2, 4, 0.3, 1.0),
            },
        ),
        StageDefinition(
            StageId="molding_cooling",
            Phase="Finishing",
            Name="Molding & Cooling",
            SequenceOrder=5,
            TicksPerBatch=4,
            Metrics={
                "MoldTemperatureC": SensorMetricProfile("°C", 10, 15, 1, 3),
                "TunnelTemperatureC": SensorMetricProfile("°C", 8, 12, 1, 3),
                "VibrationHz": SensorMetricProfile("Hz", 40, 60, 5, 12),
            },
        ),
        StageDefinition(
            StageId="packaging",
            Phase="Finishing",
            Name="Packaging",
            SequenceOrder=6,
            TicksPerBatch=3,
            Metrics={
                "LineSpeedUnitsPerMin": SensorMetricProfile(
                    "units/min", 60, 120, 10, 25
                ),
                "SealTemperatureC": SensorMetricProfile("°C", 130, 160, 6, 15),
                "RejectRatePercent": SensorMetricProfile("%", 0, 3, 0.4, 1.0),
            },
        ),
    ]
