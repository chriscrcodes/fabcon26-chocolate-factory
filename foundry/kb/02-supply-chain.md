# Supply Chain Knowledge Base

For the Supply Chain specialist agent. Covers sourcing, materials,
inventory, and shipments.

> **Status:** the `supplier`, `material`, `inventory`, and `shipment`
> tables are seeded and deployed to a Fabric SQL Database (see
> `fabric/sql-database/`) — the shipment status vocabulary (Pending/
> InTransit/Delivered) is what `simulator/src/business_data.py`
> settled on. Also bound into the Fabric IQ Ontology, mirrored into the
> dimension Lakehouse (see `SETUP.md`'s "Deploying to
> Fabric IQ").

## Materials

Four material types feed the factories:

- **Cocoa Nibs** — the output of Farm Preparation (see
  `00-company-overview.md`). This is the one material the company itself
  never produces; every gram is sourced from suppliers who handle
  harvesting, fermentation, drying, roasting, and winnowing at origin.
  Questions about fermentation time or origin roasting conditions belong
  to the supplier relationship, not factory operations.
- **Sugar**
- **Milk** (powder, for recipes with milk content — see the recipe table
  in `00-company-overview.md`)
- **Packaging** materials (wrappers, boxes)

`material.LotNumber` traces a specific received lot back to its
`supplier` and `ReceivedDate` — useful for tracing a quality issue back
to a specific incoming shipment of nibs or sugar.

## Suppliers

Each supplier is scoped to one `MaterialType` and carries a `Rating`
(intended scale: 1.0–5.0, higher is better) reflecting consistency of
quality and delivery. When comparing suppliers of the same material
type, rating and lead-time reliability both matter — a marginally
cheaper or larger supplier with a lower rating is not automatically the
better choice for a factory that can't afford input variability at
Grinding (see `01-factory-quality.md` — Grinding's particle-size and
throughput readings are sensitive to input nib quality).

## Inventory

`inventory` tracks `QuantityOnHand` per factory per material, with a
`ReorderLevel` threshold. When `QuantityOnHand` approaches or drops below
`ReorderLevel`, that material is a stockout risk — and because Grinding
is the first in-factory stage, a nib shortage at a factory is the
material most likely to actually halt a production line (as opposed to
sugar/milk/packaging shortages, which can sometimes be substituted or
delayed further down the process).

## Shipments

A `shipment` moves a finished `batch` from its originating factory
(`FromFactoryId`) toward a destination (`ToLocationId` — a distribution
point, not necessarily another factory). Status progresses through the
seeded lifecycle `Pending` → `InTransit` → `Delivered`. Shipment data
is what connects a factory's production output to the ERP/Orders domain's
fulfillment picture — a question like "will this order ship on time"
spans both domains and belongs to the Coordinator.
