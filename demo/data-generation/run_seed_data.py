#!/usr/bin/env python3
"""Entry point: writes the dimension CSVs to demo/ontology/tables/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from seed_data import main  # noqa: E402

if __name__ == "__main__":
    main()
