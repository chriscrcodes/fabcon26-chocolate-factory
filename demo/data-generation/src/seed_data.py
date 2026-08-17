"""Generates the static dimension CSVs for the chocolate scenario.

Output goes to demo/ontology/tables/ -- the same files a fabric-ontology
scenario folder expects in tables/*.csv (first row = headers), so they can
be copied into sources/fabric-ontology/data/scenarios/chocolate/tables/
without modification. Run once before starting the simulator.
"""

import csv
from pathlib import Path

from entities import Factory, ProductionLine, Recipe
from stage_catalog import get_stages

FACTORIES = [
    Factory("FAC-BCN", "EMEA-BCN", "Barcelona", "Spain", "EMEA", "Europe/Madrid"),
    Factory(
        "FAC-CHI",
        "NA-CHI",
        "Chicago",
        "United States",
        "North America",
        "America/Chicago",
    ),
    Factory(
        "FAC-GRU", "LATAM-GRU", "São Paulo", "Brazil", "LATAM", "America/Sao_Paulo"
    ),
    Factory("FAC-SIN", "APAC-SIN", "Singapore", "Singapore", "APAC", "Asia/Singapore"),
]

# 2-3 lines per factory (see demo/README.md design memo).
LINES_PER_FACTORY = {
    "FAC-BCN": 2,
    "FAC-CHI": 3,
    "FAC-GRU": 2,
    "FAC-SIN": 3,
}

RECIPES = [
    Recipe("RCP-DARK70", "Dark 70%", CacaoPercent=70, MilkPercent=0, SugarPercent=30),
    Recipe("RCP-MILK35", "Milk 35%", CacaoPercent=35, MilkPercent=25, SugarPercent=40),
    Recipe("RCP-WHITE", "White", CacaoPercent=0, MilkPercent=30, SugarPercent=45),
]


def build_production_lines() -> list[ProductionLine]:
    lines = []
    for factory in FACTORIES:
        for n in range(1, LINES_PER_FACTORY[factory.FactoryId] + 1):
            lines.append(
                ProductionLine(
                    LineId=f"{factory.FactoryId}-L{n}",
                    FactoryId=factory.FactoryId,
                    LineNumber=n,
                    Name=f"{factory.Code} Line {n}",
                )
            )
    return lines


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")


def main() -> None:
    tables_dir = Path(__file__).resolve().parents[2] / "ontology" / "tables"

    write_csv(tables_dir / "factory.csv", [f.to_dict() for f in FACTORIES])
    write_csv(
        tables_dir / "production_line.csv",
        [ln.to_dict() for ln in build_production_lines()],
    )
    write_csv(
        tables_dir / "production_stage.csv",
        [
            {
                "StageId": s.StageId,
                "Phase": s.Phase,
                "Name": s.Name,
                "SequenceOrder": s.SequenceOrder,
            }
            for s in get_stages()
        ],
    )
    write_csv(tables_dir / "recipe.csv", [r.to_dict() for r in RECIPES])


if __name__ == "__main__":
    main()
