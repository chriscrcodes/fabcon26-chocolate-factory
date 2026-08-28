# Deck update instructions — TH11 Fabric IQ + Foundry IQ

For updating `doc/TH11-Fabric IQ + Foundry IQ_What 'Connected' Actually
Looks Like (Live from the Chocolate Factory).pptx` to match the
repo's current state (as of 2026-08-28, after the `batch_material_usage`
partial lot-traceability fix — material/supplier traceability works,
batch-level traceability doesn't yet, see item 3). All three changes below stem from the same
underlying fact: the Fabric IQ Ontology grew from 22 entities/27
relationships to **23 entities/29 relationships** today.

## 1. Slide 14 — "The deployed ontology: 22 entities, 27 relationships"

- **Title**: change `22 entities, 27 relationships` → `23 entities, 29 relationships`
- **Supply Chain box**: change the table list
  `supplier, material, inventory, shipment` →
  `supplier, material, inventory, batch_material_usage, shipment`
- Optional: add a small callout or speaker note that
  `batch_material_usage` is new — but see item 3 below before writing
  it, since the fix is real but partial (material→supplier
  traceability works, batch-level traceability doesn't yet), and it's
  easy to accidentally overclaim it here.
- **Keep as-is**: "Supply Chain and ERP never connect to each other
  directly — a real data-model gap" is still true. The new table
  connects Supply Chain (`material`) to Factory & Quality (`batch`),
  not to ERP — it doesn't touch this claim.

## 2. Slide 20 — `chocolate-factory-agent` three-tools table

- `fabric_iq_ontology` row, "GROUNDS IN" column: change
  `The 22-entity, 27-relationship graph across Factory, Quality,
  Supply Chain and ERP` → `The 23-entity, 29-relationship graph
  across Factory, Quality, Supply Chain and ERP`.

## 3. Slide 29 — "Known gaps"

The second bullet needs to change, but **verified live, the fix is
partial — don't overclaim it**:

> No recipe or batch to material or supplier relationship exists in
> the Ontology. It can answer "who are our suppliers" but not genuine
> lot-level traceability. A real data gap — never generated on either
> side, not a config issue.

**What's actually confirmed live** (tested via the deployed agent,
2026-08-28): a new `batch_material_usage` junction table's
`usage_to_material` → `material_to_supplier` chain correctly answers
"which material lot and supplier does usage record `USE-####` cover"
— e.g. `USE-0000-COCO` correctly resolves to `Cocoa Nibs` from
`Bahia Origin Farms, Brazil`, matching seed data exactly.
**`usage_to_batch` returns nothing** — its `BatchId` values are
synthetic-but-plausible (same accepted convention `shipment.BatchId`
already uses in this repo), generated in a separate seed run from the
live-streamed `Batch` entity's own IDs, so the two never overlap. In
plain terms: it answers "who supplies our materials, per usage
record" — it does **not** let you point at a batch that just streamed
live and ask who supplied its materials.

**Recommended rewrite** (replace the bullet, don't delete it — this
is still a real, worth-disclosing gap, just a narrower one than
before):

> **Partially closed during rehearsal**: a new `batch_material_usage`
> junction table now answers "which supplier fed this specific
> material lot" (verified live). It doesn't yet reach a live-streamed
> batch specifically — its `BatchId`s are synthetic, same convention
> `shipment.BatchId` already uses, so a real batch and this table's
> `BatchId` never share an ID space. Genuine end-to-end batch
> genealogy is still not there; the "which supplier, roughly"
> question now is.

Keep this bullet on the "Known gaps" slide — it's a smaller gap than
before, not a closed one. Do not move it to Slide 27's "built and
dropped" framing, since nothing here was abandoned; it's a partial,
ongoing win.

**Also do not use a specific `BatchId`-based demo question live** —
e.g. "which supplier fed batch X's materials" will return nothing for
any real, currently-streaming batch. If you want to demonstrate this
live, ask about a `batch_material_usage` record directly (e.g. "which
material and supplier does usage record USE-0000-COCO come from?") or
frame it as material-type traceability ("who supplies our Cocoa
Nibs?" — already answerable before today's change, unaffected by this
gap).

## 4. Slide 30 — "Verified, not asserted" stat block

- Change `22 ONTOLOGY ENTITIES` → `23 ONTOLOGY ENTITIES`
- Change `27 BOUND RELATIONSHIPS` → `29 BOUND RELATIONSHIPS`
- **Leave the 11/8/4 verified/candidate/known-to-fail tally
  unchanged.** The "known to fail" question this might have affected
  ("Which suppliers fed the batches in Dark 70% production?") is
  **still not answerable**, confirmed live — `batch_material_usage`
  has no `RecipeId` column, so there's no queryable path from "Dark
  70% production" to a usage record at all, independent of the
  `BatchId`-matching gap above. This question stays in the
  known-to-fail bucket; nothing in the 11/8/4 tally changes.

## Not a deck-content issue, but check before presenting

Slide 23 and Slide 29's fourth bullet describe the Operations Agent
needing a one-time manual portal step. As of 2026-08-28 the
Operations Agent in the live deployment is **Inactive**
(`shouldRun: false`, data source unbound) — the manual step needs to
be (re)done before this is demoed or referenced as currently working.
This doesn't change what the slides *say* (the manual-step claim is
still accurate), just make sure the live environment actually matches
it before going on stage. Steps are in `SETUP.md`'s Operations Agent
section.
