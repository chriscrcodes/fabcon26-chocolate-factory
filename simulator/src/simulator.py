"""Real-time telemetry simulator for the chocolate factory production lines.

Each production line cycles continuously through the six in-factory stages
(grinding -> mixing_refining -> conching -> tempering -> molding_cooling ->
packaging) for one batch at a time. Every tick it emits one `sensor_reading`
row per metric for the line's current stage, and a `quality_check` row on
the stage's last tick. Lines also emit `batch_event` rows (Started/Completed)
at the first and last stage transitions of a batch, and occasionally go
`Down` for a maintenance window via `line_status` events -- production
(sensor readings, quality checks) pauses while a line is down. All four
record types match the matching tables/notes in
fabric/ontology/ontology_config.json, so Fabric Eventstream can map the JSON
straight into the Eventhouse without a translation layer.
"""

import argparse
import csv
import os
import random
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dashboard import MissionControlDashboard, load_factory_lookup, load_line_lookup
from event_hub_service import EventHubService
from stage_catalog import StageDefinition, get_stages

STAGES: list[StageDefinition] = get_stages()


def _load_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _new_batch_id(line_id: str) -> str:
    return f"BATCH-{line_id}-{int(time.time())}-{uuid.uuid4().hex[:4]}"


DOWNTIME_REASONS = ["Scheduled Maintenance", "Unplanned Stop", "Changeover"]


@dataclass
class LineState:
    line_id: str
    stage_index: int = 0
    ticks_in_stage: int = 0
    batch_id: str = ""
    recipe_id: str = ""
    anomaly_mode: bool = False
    is_down: bool = False
    down_ticks_remaining: int = 0

    def stage(self) -> StageDefinition:
        return STAGES[self.stage_index]

    def start_new_batch(self, recipe_ids: list[str]) -> None:
        self.batch_id = _new_batch_id(self.line_id)
        self.recipe_id = random.choice(recipe_ids)
        self.stage_index = 0
        self.ticks_in_stage = 0


@dataclass
class Simulator:
    lines: list[LineState]
    recipe_ids: list[str]
    event_hub: EventHubService
    interval_seconds: float
    anomaly_rate: float
    downtime_rate: float = 0.0
    tables_dir: Path | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    events_sent: int = field(default=0)

    def _start_batch(self, line: LineState, now: str, payloads: list[dict]) -> None:
        line.start_new_batch(self.recipe_ids)
        payloads.append(
            {
                "RecordType": "batch_event",
                "EventId": str(uuid.uuid4()),
                "BatchId": line.batch_id,
                "LineId": line.line_id,
                "RecipeId": line.recipe_id,
                "EventType": "Started",
                "Timestamp": now,
            }
        )

    def tick(self) -> list[dict]:
        now = self.clock().isoformat()
        payloads: list[dict] = []

        for line in self.lines:
            # Downtime state machine -- a down line produces nothing.
            if line.is_down:
                line.down_ticks_remaining -= 1
                if line.down_ticks_remaining <= 0:
                    line.is_down = False
                    payloads.append(
                        {
                            "RecordType": "line_status",
                            "EventId": str(uuid.uuid4()),
                            "LineId": line.line_id,
                            "Status": "Running",
                            "Reason": "",
                            "Timestamp": now,
                        }
                    )
                continue

            if self.downtime_rate and random.random() < self.downtime_rate:
                line.is_down = True
                line.down_ticks_remaining = random.randint(3, 8)
                payloads.append(
                    {
                        "RecordType": "line_status",
                        "EventId": str(uuid.uuid4()),
                        "LineId": line.line_id,
                        "Status": "Down",
                        "Reason": random.choice(DOWNTIME_REASONS),
                        "Timestamp": now,
                    }
                )
                continue

            if not line.batch_id:
                self._start_batch(line, now, payloads)

            stage = line.stage()
            anomaly = line.anomaly_mode or random.random() < self.anomaly_rate

            readings: dict[str, float] = {}
            for metric_name, profile in stage.Metrics.items():
                value = profile.sample(anomaly=anomaly)
                readings[metric_name] = value
                payloads.append(
                    {
                        "RecordType": "sensor_reading",
                        "ReadingId": str(uuid.uuid4()),
                        "LineId": line.line_id,
                        "StageId": stage.StageId,
                        "BatchId": line.batch_id,
                        "Timestamp": now,
                        "Metric": metric_name,
                        "Value": value,
                        "Unit": profile.Unit,
                    }
                )

            line.ticks_in_stage += 1
            is_last_tick_of_stage = line.ticks_in_stage >= stage.TicksPerBatch

            if is_last_tick_of_stage:
                defect_rate = stage.defect_rate(readings)
                payloads.append(
                    {
                        "RecordType": "quality_check",
                        "CheckId": str(uuid.uuid4()),
                        "BatchId": line.batch_id,
                        "LineId": line.line_id,
                        "StageId": stage.StageId,
                        "Timestamp": now,
                        "DefectRate": defect_rate,
                        "Result": "Fail" if defect_rate > 0.3 else "Pass",
                        "Notes": "anomaly injected" if anomaly else "",
                    }
                )

                line.ticks_in_stage = 0
                line.stage_index += 1
                if line.stage_index >= len(STAGES):
                    completed_batch_id = line.batch_id
                    payloads.append(
                        {
                            "RecordType": "batch_event",
                            "EventId": str(uuid.uuid4()),
                            "BatchId": completed_batch_id,
                            "LineId": line.line_id,
                            "RecipeId": line.recipe_id,
                            "EventType": "Completed",
                            "Timestamp": now,
                        }
                    )
                    self._start_batch(line, now, payloads)

        return payloads

    def run(self, max_runtime_seconds: int = None, plain: bool = False) -> None:
        if plain or not sys.stdout.isatty():
            self._run_plain(max_runtime_seconds)
        else:
            self._run_dashboard(max_runtime_seconds)

    def _run_plain(self, max_runtime_seconds: int = None) -> None:
        start = time.time()
        print(
            f"streaming telemetry for {len(self.lines)} lines "
            f"every {self.interval_seconds}s "
            f"(anomaly rate {self.anomaly_rate:.0%}, "
            f"downtime rate {self.downtime_rate:.0%}) -- Ctrl+C to stop"
        )
        try:
            while True:
                payloads = self.tick()
                self.event_hub.send_events(payloads)
                self.events_sent += len(payloads)
                elapsed = time.time() - start
                print(
                    f"[{elapsed:6.1f}s] sent {len(payloads):3d} events "
                    f"(total {self.events_sent})"
                )

                if max_runtime_seconds and elapsed >= max_runtime_seconds:
                    print(f"max runtime ({max_runtime_seconds}s) reached")
                    break

                time.sleep(self.interval_seconds)
        except KeyboardInterrupt:
            print("\nstopped by user")
        finally:
            self.event_hub.close()

    def _run_dashboard(self, max_runtime_seconds: int = None) -> None:
        from rich.live import Live

        tables_dir = self.tables_dir or (
            Path(__file__).resolve().parents[2] / "fabric" / "ontology" / "tables"
        )
        dashboard = MissionControlDashboard(
            factories=load_factory_lookup(tables_dir),
            lines_meta=load_line_lookup(tables_dir),
            stages=STAGES,
        )

        start = time.time()
        try:
            with Live(
                dashboard.render(0, self.interval_seconds, self.lines),
                console=dashboard.console,
                refresh_per_second=4,
                screen=True,
            ) as live:
                while True:
                    payloads = self.tick()
                    self.event_hub.send_events(payloads)
                    self.events_sent += len(payloads)
                    dashboard.log_payloads(payloads)
                    live.update(
                        dashboard.render(
                            self.events_sent, self.interval_seconds, self.lines
                        )
                    )

                    elapsed = time.time() - start
                    if max_runtime_seconds and elapsed >= max_runtime_seconds:
                        break

                    time.sleep(self.interval_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            self.event_hub.close()

    def _send_with_retry(self, payloads: list[dict], max_retries: int = 5) -> None:
        """send_events(), retrying on Event Hub throttling.

        Backfill sends batches back-to-back with no pacing, which can
        exceed a Standard-tier namespace's throughput units (verified
        live: a 72h/30s backfill hit "com.microsoft:server-busy" /
        error code 50002 well before finishing). Throttling is
        transient -- back off and retry rather than failing the whole
        backfill; a genuinely undersized `sku_capacity` still surfaces
        after `max_retries`.
        """
        for attempt in range(max_retries):
            try:
                self.event_hub.send_events(payloads)
                return
            except Exception as e:
                message = str(e).lower()
                if "server-busy" not in message and "throttl" not in message:
                    raise
                if attempt == max_retries - 1:
                    raise
                wait = 2**attempt  # 1s, 2s, 4s, 8s, 16s
                print(f"  throttled by Event Hub, retrying in {wait}s...")
                time.sleep(wait)

    def run_backfill(self, hours: float, batch_ticks: int = 20) -> None:
        """Generate `hours` of history as fast as possible (no sleep).

        Ticks advance a virtual clock by `interval_seconds` each time,
        same as real-time mode, so stage progression (TicksPerBatch,
        anomaly/downtime rates) is unaffected -- only wall-clock pacing
        is skipped. Payloads from `batch_ticks` ticks are grouped into
        one send_events() call to cut down network round-trips for a
        large backfill. If a namespace is too small for the volume of
        a large/fast backfill, raise its `sku_capacity` in
        `infra/terraform.tfvars` and re-`apply`, or use a lighter
        `--interval`/smaller `--backfill-batch-ticks`.
        """
        total_ticks = int(hours * 3600 / self.interval_seconds)
        virtual_time = datetime.now(UTC) - timedelta(hours=hours)
        print(
            f"backfilling {hours}h of history for {len(self.lines)} lines "
            f"({total_ticks} ticks at {self.interval_seconds}s/tick, "
            f"starting {virtual_time.isoformat()})"
        )
        try:
            batch: list[dict] = []
            for i in range(1, total_ticks + 1):
                self.clock = lambda vt=virtual_time: vt
                batch.extend(self.tick())
                virtual_time += timedelta(seconds=self.interval_seconds)

                if len(batch) and (i % batch_ticks == 0 or i == total_ticks):
                    self._send_with_retry(batch)
                    self.events_sent += len(batch)
                    batch = []
                    time.sleep(0.2)  # light pacing -- avoids constant throttling

                if i % max(1, total_ticks // 20) == 0 or i == total_ticks:
                    print(
                        f"[{i}/{total_ticks}] backfilled up to "
                        f"{virtual_time.isoformat()} (total events {self.events_sent})"
                    )
        except KeyboardInterrupt:
            print("\nstopped by user")
        finally:
            self.event_hub.close()


def build_simulator(
    tables_dir: Path,
    interval: float,
    anomaly_rate: float,
    downtime_rate: float = 0.0,
) -> Simulator:
    lines_rows = _load_csv(tables_dir / "production_line.csv")
    recipes_rows = _load_csv(tables_dir / "recipe.csv")

    if not lines_rows:
        raise RuntimeError(
            f"No production lines found in {tables_dir}/production_line.csv -- "
            "run seed_data.py first."
        )

    connection_string = os.getenv("AZURE_EVENT_HUB_CONNECTION_STRING")
    namespace = os.getenv("AZURE_EVENT_HUB_NAMESPACE_HOSTNAME")
    event_hub_name = os.getenv("AZURE_EVENT_HUB_NAME")

    event_hub = EventHubService(
        connection_string=connection_string,
        fully_qualified_namespace=namespace,
        event_hub_name=event_hub_name,
    )

    lines = [LineState(line_id=row["LineId"]) for row in lines_rows]
    recipe_ids = [row["RecipeId"] for row in recipes_rows]

    return Simulator(
        lines=lines,
        recipe_ids=recipe_ids,
        event_hub=event_hub,
        interval_seconds=interval,
        anomaly_rate=anomaly_rate,
        downtime_rate=downtime_rate,
        tables_dir=tables_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chocolate factory production-line telemetry simulator"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("SIMULATION_INTERVAL", "5.0")),
        help="Seconds between ticks (default: 5.0)",
    )
    parser.add_argument(
        "--anomaly-rate",
        type=float,
        default=float(os.getenv("ANOMALY_RATE", "0.03")),
        help="Probability a given line/tick is anomalous (default: 0.03)",
    )
    parser.add_argument(
        "--downtime-rate",
        type=float,
        default=float(os.getenv("DOWNTIME_RATE", "0.015")),
        help="Probability a running line enters a maintenance window on a "
        "given tick (default: 0.015)",
    )
    parser.add_argument(
        "--max-runtime",
        type=int,
        default=(
            int(os.getenv("MAX_RUNTIME_SECONDS"))
            if os.getenv("MAX_RUNTIME_SECONDS")
            else None
        ),
        help="Stop after N seconds (default: unlimited)",
    )
    parser.add_argument(
        "--tables-dir",
        type=str,
        default=None,
        help="Path to fabric/ontology/tables (default: auto-detected)",
    )
    parser.add_argument(
        "--backfill-hours",
        type=float,
        default=None,
        help="Generate this many hours of history as fast as possible "
        "(no sleep between ticks) instead of streaming in real time. "
        "Mutually exclusive with --max-runtime.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Plain scrolling text output instead of the live mission-control "
        "dashboard (always used automatically when stdout isn't a terminal)",
    )
    parser.add_argument(
        "--backfill-batch-ticks",
        type=int,
        default=20,
        help="Group this many ticks' events into one send_events() call "
        "during --backfill-hours, to cut down network round-trips "
        "(default: 20)",
    )
    args = parser.parse_args()

    if args.backfill_hours and args.max_runtime:
        parser.error("--backfill-hours and --max-runtime are mutually exclusive")

    tables_dir = (
        Path(args.tables_dir)
        if args.tables_dir
        else Path(__file__).resolve().parents[2] / "fabric" / "ontology" / "tables"
    )

    simulator = build_simulator(
        tables_dir, args.interval, args.anomaly_rate, args.downtime_rate
    )
    if args.backfill_hours:
        simulator.run_backfill(args.backfill_hours, args.backfill_batch_ticks)
    else:
        simulator.run(max_runtime_seconds=args.max_runtime, plain=args.plain)


if __name__ == "__main__":
    main()
