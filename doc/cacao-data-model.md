# Cacao Data Model

FabCon Europe 2026 · Barcelona · Design memo

Draft entity model and repo-adaptation plan for the "Charlie and the
Chocolate Factory" multi-agent demo — Microsoft Fabric for data, Microsoft
Foundry for a Coordinator plus three domain specialists.

Live version: the published artifact (design doc) tracks this file;
treat this copy as the source of truth for the repo.

## 1. Factories

Region + city codes, ERP-style. All four factories run the identical
six-stage in-factory line (see §2) — no specialization — so every
specialist agent reasons over the same schema regardless of factory.

| Code | City | Country / Region |
|---|---|---|
| `EMEA-BCN` | Barcelona | Spain · EMEA |
| `NA-CHI` | Chicago | United States · North America |
| `LATAM-GRU` | São Paulo | Brazil · LATAM |
| `APAC-SIN` | Singapore | Singapore · APAC |

Each factory runs 2–3 production lines (`production_line`), not one:
`EMEA-BCN` and `LATAM-GRU` have 2 lines, `NA-CHI` and `APAC-SIN` have 3 —
10 lines total. See `simulator/src/seed_data.py`.

## 2. Production line: bean to bar, nine stages, one phase off-site

Cross-checked against the stage breakdowns published by
[Fauchon](https://www.fauchon.com/en/blogs/news/the-different-stages-of-chocolate-making)
and
[Alain Ducasse](https://www.lechocolat-alainducasse.com/fr/fabrication-chocolat).

**Farm Preparation happens near cocoa origin, not at any of the four
factories** — documented for context but not modelled as a factory
production stage. See `fabric/ontology/farm-preparation.md`.

### Farm Preparation — off-site, near origin, not a factory stage

1. **Harvesting & Fermentation** — pods opened, beans fermented 5–7 days
   to build flavor and cut bitterness.
2. **Drying & Roasting** — beans dried to reduce moisture, then roasted at
   120–140°C.
3. **Winnowing** — cracked and winnowed to remove shell, leaving cocoa
   nibs.

### Factory Processing — nib → chocolate mass, at the factory

4. **Grinding** — nibs ground into cocoa liquor: cocoa solids plus cocoa
   butter.
5. **Mixing & Refining** — sugar, milk powder and cocoa butter added,
   particle size reduced for smoothness.
6. **Conching** — agitated and heated for hours to days to finish flavor
   and remove acidity.

### Finishing — mass → product, at the factory

7. **Tempering** — heated and cooled to stabilize cocoa-butter crystals
   for gloss and snap.
8. **Molding & Cooling** — poured into molds, vibrated to clear air,
   cooled to solidify.
9. **Packaging** — demolded, quality-inspected, packaged for
   distribution.

Only stages 4–9 exist as `production_stage` rows (`Phase`: "Factory
Processing" or "Finishing"). `production_line` is the physical-line
dimension (2–3 per factory); `production_stage` is a shared 6-row
catalog every line steps through per batch.

## 3. Agent architecture: one Coordinator, three specialists

The Coordinator routes and synthesizes; each specialist owns a domain's
tables and reasoning. `factory`, `batch`, and `recipe` are shared keys
all three specialists reference — that's what lets the Coordinator join
their answers (at the agent level — see the medallion-layers memo's
cross-plane decision).

| Role | Domain | Owns |
|---|---|---|
| Orchestrator | **Coordinator** | Routes questions, calls specialists, synthesizes a cross-domain answer |
| Specialist | **Factory / Quality** | Production lines, batches, sensor telemetry, defect and yield |
| Specialist | **Supply Chain** | Suppliers, raw materials, inventory, inter-factory shipments |
| Specialist | **ERP / Orders** | Customers, sales orders, products, invoicing |

```
                    Coordinator
                   /     |      \
        Factory/Quality  |   ERP/Orders
                   Supply Chain
                        |
            shared: factory · batch · recipe
```

## 4. Entity model

In `ontology_config.json` shape (`fabric/ontology/ontology_config.json`).
Grouped by owning specialist.

### Factory / Quality

| Table | Key columns | Notes |
|---|---|---|
| `factory` | FactoryId (pk), Code, City, Country, Region, TimeZone | Shared across all domains |
| `recipe` | RecipeId (pk), Name, CacaoPercent, MilkPercent, SugarPercent | Shared with ERP (`product`) |
| `production_line` | LineId (pk), FactoryId, LineNumber, Name | 2–3 rows per factory — the physical line, not a stage |
| `production_stage` | StageId (pk), Phase, Name, SequenceOrder | 6 shared rows — every line steps through all 6 |
| `batch` | BatchId (pk), LineId, RecipeId, StartTime, EndTime, QuantityKg, Status | Materialized from `bronze_batch_event` lifecycle events, not inferred from readings |
| `sensor_reading` | ReadingId (pk), LineId, StageId, BatchId, Timestamp, Metric, Value, Unit | Streamed to Azure Event Hub, EAV shape. **Built.** |
| `quality_check` | CheckId (pk), BatchId, LineId, StageId, Timestamp, DefectRate, Result, Notes | Emitted on each stage's last tick. **Built.** |
| `line_status` | EventId (pk), LineId, Status, Reason, Timestamp | Running/Down state-change log. **Built.** |

### Supply Chain

| Table | Key columns | Notes |
|---|---|---|
| `supplier` | SupplierId (pk), Name, Country, MaterialType, Rating | |
| `material` | MaterialId (pk), SupplierId, Type, LotNumber, ReceivedDate, QuantityKg | Type includes "Cocoa Nibs" — incoming Farm Preparation output |
| `inventory` | InventoryId (pk), FactoryId, MaterialId, QuantityOnHand, ReorderLevel, LastUpdated | |
| `shipment` | ShipmentId (pk), FromFactoryId, ToLocationId, BatchId, Carrier, DepartDate, ArriveDate, Status | Links finished batches to distribution |

### ERP / Orders

| Table | Key columns | Notes |
|---|---|---|
| `customer` | CustomerId (pk), Name, Country, Segment, CreditLimit | |
| `product` | ProductId (pk), Name, RecipeId, PackagingType, SKU | Links back to recipe |
| `sales_order` | OrderId (pk), CustomerId, OrderDate, Status, TotalAmount, Currency | |
| `order_line` | OrderLineId (pk), OrderId, ProductId, QuantityKg, UnitPrice | |
| `invoice` | InvoiceId (pk), OrderId, IssueDate, DueDate, AmountDue, PaidDate | |

### Cross-domain relationships

```
production_line.FactoryId   -> factory.FactoryId
batch.LineId                -> production_line.LineId
batch.RecipeId               -> recipe.RecipeId
sensor_reading.LineId        -> production_line.LineId
sensor_reading.StageId       -> production_stage.StageId
sensor_reading.BatchId       -> batch.BatchId
quality_check.StageId        -> production_stage.StageId
quality_check.BatchId        -> batch.BatchId
quality_check.LineId         -> production_line.LineId
line_status.LineId           -> production_line.LineId
material.SupplierId          -> supplier.SupplierId
inventory.MaterialId         -> material.MaterialId
inventory.FactoryId          -> factory.FactoryId
shipment.FromFactoryId       -> factory.FactoryId
shipment.BatchId              -> batch.BatchId
product.RecipeId             -> recipe.RecipeId
sales_order.CustomerId       -> customer.CustomerId
order_line.OrderId            -> sales_order.OrderId
order_line.ProductId          -> product.ProductId
invoice.OrderId               -> sales_order.OrderId
```

## 5. Repo adaptation status

`sources/` holds unmodified upstream clones — each is its own git repo,
never edited in place. All chocolate-scenario code is authored at the
repo root instead, organized by layer (`simulator/`, `fabric/`,
`foundry/`, plus cross-cutting `infra/`/`doc/`). Infra/deployment layers
in both upstream repos are reusable as-is once this content is deployed
into them.

| Area | Status | Notes |
|---|---|---|
| `fabric/ontology` | **Config done** | `ontology_config.json` — all 18 tables, 3 domains. Supply Chain / ERP dimension CSVs, `scenarios.json` registration, and 4-agent provisioning in `fabric-ontology` still to do |
| `simulator` | **Built, verified against a live Event Hub** | Streams `sensor_reading`, `quality_check`, `batch_event`, `line_status`. Supply Chain / ERP generators not started. Dashboard tiles not started |
| `fabric/eventhouse` | **KQL + Eventstream definition written, not yet run against a live Eventhouse** | Bronze/Silver/Gold + `eventstream.json` — see `cacao-medallion-layers.md` |
| `foundry/agents` | **Not started** | Coordinator + 3 specialist instruction sets, orchestration/handoff logic, chat API routing |
