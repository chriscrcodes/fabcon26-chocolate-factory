#!/usr/bin/env python3
"""Entry point: streams production-line telemetry to Azure Event Hub."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from simulator import main  # noqa: E402

if __name__ == "__main__":
    main()
