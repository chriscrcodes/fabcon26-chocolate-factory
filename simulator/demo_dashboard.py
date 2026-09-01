"""Run the mission-control dashboard against synthetic ticks only -- no Event
Hub, no Azure credentials. For recording the B-roll clip (video-01-simulator)
and for rehearsing the dashboard's look before a live demo.

Usage: uv run python demo_dashboard.py [--seconds 33] [--interval 1.0]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from rich.console import Console
from rich.live import Live

from dashboard import MissionControlDashboard, load_factory_lookup, load_line_lookup
from simulator import STAGES, LineState, Simulator, _load_csv


class _NullEventHub:
    def send_events(self, events: list[dict]) -> None:
        pass

    def close(self) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=33.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--anomaly-rate", type=float, default=0.05)
    parser.add_argument("--downtime-rate", type=float, default=0.02)
    args = parser.parse_args()

    tables_dir = Path(__file__).resolve().parent.parent / "fabric" / "ontology" / "tables"
    lines = [
        LineState(line_id=row["LineId"])
        for row in _load_csv(tables_dir / "production_line.csv")
    ]
    recipe_ids = [row["RecipeId"] for row in _load_csv(tables_dir / "recipe.csv")]

    sim = Simulator(
        lines=lines,
        recipe_ids=recipe_ids,
        event_hub=_NullEventHub(),
        interval_seconds=args.interval,
        anomaly_rate=args.anomaly_rate,
        downtime_rate=args.downtime_rate,
        tables_dir=tables_dir,
    )

    console = Console()
    dashboard = MissionControlDashboard(
        factories=load_factory_lookup(tables_dir),
        lines_meta=load_line_lookup(tables_dir),
        stages=STAGES,
        console=console,
    )

    start = time.time()
    with Live(
        dashboard.render(0, args.interval, sim.lines),
        console=console,
        refresh_per_second=4,
        screen=True,
    ) as live:
        while time.time() - start < args.seconds:
            payloads = sim.tick()
            sim.events_sent += len(payloads)
            dashboard.log_payloads(payloads)
            live.update(dashboard.render(sim.events_sent, args.interval, sim.lines))
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
