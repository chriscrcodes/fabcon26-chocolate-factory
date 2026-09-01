"""Mission-control style live terminal dashboard for the simulator.

Renders per-tick state from `Simulator.tick()` as a Tron/launch-control
themed table (one row per production line) plus a scrolling event feed,
using `rich.live.Live`. Pure presentation -- takes the same `LineState`
objects and payload dicts the plain-text `Simulator.run()` path already
produces, so it has zero effect on what gets sent to Event Hub.
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from rich import box
from rich.bar import Bar
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

CYAN = "bold bright_cyan"
MAGENTA = "bold bright_magenta"
GREEN = "bold bright_green"
AMBER = "bold yellow"
RED = "bold red"
DIM = "grey50"
AMBER_BAR = "bright_yellow"

STATUS_STYLE = {
    "GO": (GREEN, "● GO"),
    "ANOMALY": (AMBER, "⚠ ANOMALY"),
    "HOLD": (RED, "● HOLD"),
}


def load_factory_lookup(tables_dir: Path) -> dict[str, tuple[str, str]]:
    """FactoryId -> (Code, City), e.g. FAC-BCN -> (EMEA-BCN, Barcelona)."""
    import csv

    path = tables_dir / "factory.csv"
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {
            row["FactoryId"]: (row["Code"], row["City"])
            for row in csv.DictReader(f)
        }


def load_line_lookup(tables_dir: Path) -> dict[str, tuple[str, str]]:
    """LineId -> (FactoryId, Name)."""
    import csv

    path = tables_dir / "production_line.csv"
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {
            row["LineId"]: (row["FactoryId"], row["Name"])
            for row in csv.DictReader(f)
        }


class MissionControlDashboard:
    def __init__(
        self,
        factories: dict[str, tuple[str, str]],
        lines_meta: dict[str, tuple[str, str]],
        stages,
        console: Console | None = None,
        feed_size: int = 9,
    ):
        self.factories = factories
        self.lines_meta = lines_meta
        self.stages = stages
        self.console = console or Console()
        self.feed: deque[Text] = deque(maxlen=feed_size)
        self.start = time.time()

    def _factory_label(self, line_id: str) -> str:
        factory_id, _ = self.lines_meta.get(line_id, ("", ""))
        code, city = self.factories.get(factory_id, (factory_id, ""))
        return f"{code}\n{city}" if city else code

    def _line_label(self, line_id: str) -> str:
        _, name = self.lines_meta.get(line_id, ("", line_id))
        return name or line_id

    def log_payloads(self, payloads: list[dict]) -> None:
        for p in payloads:
            rt = p.get("RecordType")
            line_id = p.get("LineId", "")
            label = self._line_label(line_id)
            if rt == "line_status":
                if p["Status"] == "Down":
                    self.feed.append(
                        Text(f"● HOLD  {label} -- {p['Reason']}", style=RED)
                    )
                else:
                    self.feed.append(
                        Text(f"● GO    {label} -- resumed", style=GREEN)
                    )
            elif rt == "quality_check":
                if p["Result"] == "Fail":
                    self.feed.append(
                        Text(
                            f"✖ FAIL  {label} / {p['StageId']} -- "
                            f"defect {p['DefectRate']:.1%}",
                            style=RED,
                        )
                    )
                elif p.get("Notes"):
                    self.feed.append(
                        Text(
                            f"⚠ QC    {label} / {p['StageId']} -- "
                            f"anomaly, defect {p['DefectRate']:.1%}",
                            style=AMBER,
                        )
                    )
            elif rt == "batch_event" and p["EventType"] == "Completed":
                self.feed.append(
                    Text(f"✓ DONE  {label} -- batch complete", style=CYAN)
                )

    def _header(self, events_sent: int, interval_seconds: float) -> Panel:
        elapsed = time.time() - self.start
        mins, secs = divmod(int(elapsed), 60)
        hrs, mins = divmod(mins, 60)
        rate = events_sent / elapsed if elapsed > 0 else 0.0

        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            Text("\U0001f36b  CHOCOLATE FACTORY // MISSION CONTROL", style=CYAN),
            Text(f"T+{hrs:02d}:{mins:02d}:{secs:02d}", style=MAGENTA),
        )
        grid.add_row(
            Text(
                f"{len(self.lines_meta)} LINES ACROSS "
                f"{len(self.factories)} FACTORIES · TICK {interval_seconds:g}s",
                style=DIM,
            ),
            Text(f"EVENTS {events_sent:,} · {rate:0.1f}/s", style=GREEN),
        )
        return Panel(grid, border_style=CYAN, box=box.DOUBLE)

    def _line_table(self, lines) -> Table:
        table = Table(
            border_style="bright_blue",
            header_style=MAGENTA,
            expand=True,
            box=None,
            padding=(0, 1),
        )
        table.add_column("FACTORY", ratio=2)
        table.add_column("LINE", ratio=2)
        table.add_column("STATUS", ratio=2)
        table.add_column("STAGE", ratio=3)
        table.add_column("BATCH PROGRESS", ratio=4)

        for line in sorted(lines, key=lambda ln: ln.line_id):
            if line.is_down:
                style, label = STATUS_STYLE["HOLD"]
            elif line.anomaly_mode:
                style, label = STATUS_STYLE["ANOMALY"]
            else:
                style, label = STATUS_STYLE["GO"]

            stage = self.stages[line.stage_index] if not line.is_down else None
            stage_name = stage.Name.upper() if stage else "OFFLINE"

            if line.is_down:
                bar = Bar(size=1.0, begin=0, end=1.0, color="grey27", bgcolor="grey15")
            else:
                progress = min(line.ticks_in_stage / max(stage.TicksPerBatch, 1), 1.0)
                bar_color = AMBER_BAR if line.anomaly_mode else "bright_cyan"
                bar = Bar(size=1.0, begin=0, end=progress, color=bar_color, bgcolor="grey15")

            table.add_row(
                Text(self._factory_label(line.line_id), style=DIM),
                Text(self._line_label(line.line_id)),
                Text(label, style=style),
                Text(stage_name, style=DIM if not line.is_down else RED),
                bar,
            )
        return table

    def _feed_panel(self) -> Panel:
        body = Group(*self.feed) if self.feed else Text("awaiting telemetry...", style=DIM)
        return Panel(body, title="EVENT FEED", border_style="grey37", title_align="left")

    def render(self, events_sent: int, interval_seconds: float, lines) -> Group:
        return Group(
            self._header(events_sent, interval_seconds),
            self._line_table(lines),
            self._feed_panel(),
        )
