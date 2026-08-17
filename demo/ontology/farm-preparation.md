# Farm Preparation (out of scope for the four factories)

Harvesting & fermentation, drying & roasting, and winnowing happen near cocoa
origin (e.g. Ivory Coast, Ecuador, Ghana), weeks before material reaches any
of the four factories. None of EMEA-BCN, NA-CHI, LATAM-GRU, or APAC-SIN
perform these stages, so they are not modelled as `production_stage` rows.

They show up in the data model only as the origin of incoming material:
`material.Type = "Cocoa Nibs"` on a `supplier`-sourced lot in `material`
(`demo/ontology/ontology_config.json`) is the already-fermented, roasted,
and winnowed output of this phase.

If a later iteration wants Farm Preparation represented explicitly (e.g. for
a Supply Chain agent question like "which origin lots are still
fermenting"), add it as its own `origin` dimension feeding `material`,
not as factory production lines.
