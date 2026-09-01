# Ontology Viewer

A standalone visualization of the Fabric IQ ontology, the four
factories/supply-chain/orders data around it, and where the factories
actually are. No live Fabric/Foundry connection needed to *run* it -- but the
schema view mirrors a snapshot of the actually deployed Fabric IQ Ontology
item, fetched once via `fetch_deployed_schema.py`.

Three tabs, toggled at the top:

- **Schema** -- **23 entity types / 31 relationship types (29 bound)**,
  exactly matching the live deployed definition, as a force-directed graph
  colored by domain (Factory & Quality / Supply Chain / Orders). `sensor_reading`
  is deployed as 6 separate per-stage entities (`SensorReadingGrinding`,
  `SensorReadingConching`, ...), not 1 -- this view shows that split, not the
  simpler `ontology_config.json` abstraction. Edges through `Factory`,
  `Product`, and `Recipe` are highlighted with an animated pulse (the deck's
  point: supply chain and orders never connect directly). Dashed edges
  (`batch_to_line`, `batch_to_recipe`) are defined but not
  Contextualization-bound. Click a node for its full column list (name, type,
  key) and any `notes` from `ontology_config.json`; click an edge for its
  relationship detail (source, target, fromKey/toKey, bound status).
- **Instances** -- ~475 concrete rows (4 factories, 10 lines, 9 suppliers,
  36 materials, etc.), read from `fabric/ontology/tables/*.csv`. `batch`,
  `sensor_reading`, `quality_check`, and `line_status` have no static CSVs
  (simulator/runtime-only), so they only appear in the schema view.
- **Map** -- an interactive globe (pure D3 `geoOrthographic`, no WebGL) with
  the 4 factories as glowing pins. Drag to rotate (works at any zoom level);
  scroll to zoom in/out; click a pin to zoom into its country/region; a
  "🌐 BACK TO GLOBE" button stays visible whenever you're not already at the
  globe level, and the breadcrumb (top-left) also jumps back to any level.
  The site card (production lines, time zone, etc.) opens in the inspector;
  "Enter Factory" shows a simulated production-line floor: 6 stage cards
  (twin millstones, a swirling mixing vat, a rolling conching drum with
  rising steam, a tempering tank with a rising/falling level, a misty
  molding/cooling tunnel, a pick-arm packaging station) laid out in a
  boustrophedon (snake) grid -- 1→2→3 left to right along the top row, down
  to 4, then 4→5→6 right to left along the bottom row, connected by pulsing
  ▸▾◂ arrows -- so it fills the available width edge to edge with no
  horizontal scrolling. Each card carries a live telemetry readout (3
  metrics, same names/units/ranges as `simulator/src/stage_catalog.py`, e.g.
  grinding's Particle Size in µm) refreshing every ~1.4s with a brief flash,
  plus a per-card LIVE/IDLE status dot -- a "⏸ SIMULATION: ON/OFF" button
  (top-right of the floor) pauses/resumes the whole floor at once. Purely
  illustrative/animated, not driven by the real simulator or any live data.
  **Coordinates in
  `factory-coords.json` are manually authored, public-knowledge city
  coordinates -- nothing in this repo's data provides factory lat/long**,
  see that file's own `_note`.

Other things worth knowing about while demoing:
- **Search box** (header) dims every node/edge whose label doesn't match.
- **Node size** is proportional to its number of connections (degree) --
  the busiest entities are visibly bigger.
- **Click a node** to highlight it and its direct (1-hop) neighbors and dim
  the rest; click it again (or empty canvas) to clear.
- **Verified demo paths** (bottom of the inspector panel) are two buttons
  that highlight the graph traversal for two of the deck's actual demo-beat
  questions ("who are our suppliers, and what do they supply?" and the
  worst-quality/inventory centerpiece question) -- driven by hardcoded exact
  relationship IDs (not just a node set), so a highlighted node can never
  end up without a highlighted edge to another highlighted node. Not a live
  query engine.
- **Stats badges** (header) show live entity counts per domain for whichever
  view is open.

## Run it

```bash
python3 -m http.server 8000   # serve locally -- opening index.html via file:// blocks the fetch()
```

Then open http://localhost:8000/index.html (add `#instances` or `#map` to
land on that view directly).

## Refreshing the schema snapshot

`deployed_schema.json` is a point-in-time snapshot, not a live query -- it
won't notice a redeploy on its own. Re-fetch it after any ontology change:

```bash
az login   # or already-logged-in AzureCliCredential
# capacity must be resumed, not paused, or getDefinition calls fail/timeout
uv run --with azure-identity --with requests ../../fabric/manage_capacity.py resume  # if paused
FABRIC_WORKSPACE_ID=<workspace-id> uv run --with azure-identity --with requests fetch_deployed_schema.py
python3 build_graph_data.py
```

If `deployed_schema.json` is deleted or was never fetched, `build_graph_data.py`
falls back to deriving the schema from `ontology_config.json` alone (18
entities / 22 relationships) -- correct as a source-of-truth summary, but it
will under-count relative to what's actually live, since it doesn't know
about the per-stage sensor_reading split. The status line at the bottom of
the page and the inspector panel both say which source is in use.

## Vendored / bundled assets (all offline, no CDN)

- `vendor/d3.v7.min.js` -- D3 core.
- `vendor/topojson-client.min.js` + `vendor/countries-110m.json` -- world
  atlas for the Map tab (Natural Earth 110m resolution via `world-atlas`;
  small enough to survive conference wifi). Note: Singapore doesn't render
  as a country polygon at this resolution (too small an island) -- its pin
  and zoom still work, there's just no country fill to highlight.
