# Company & Scenario Overview

This document grounds every agent (Coordinator and all three specialists)
in the shared facts of the scenario: the factories, the production
process, and how the domains divide up. Domain-specific detail lives in
the other documents in this knowledge base — this one is the shared
context.

## The factories

The company operates four chocolate-manufacturing factories:

| Code | City | Country / Region | Production lines |
|---|---|---|---|
| EMEA-BCN | Barcelona | Spain, EMEA | 2 |
| NA-CHI | Chicago | United States, North America | 3 |
| LATAM-GRU | São Paulo | Brazil, LATAM | 2 |
| APAC-SIN | Singapore | Singapore, APAC | 3 |

Each production line is an independent physical line running the full
in-factory process end to end — factories are not specialized by stage,
and every line at every factory runs the same six-stage sequence (see
below) for every batch it processes.

## From bean to bar

Chocolate production has three phases. **Only the last two happen at the
company's own factories.**

**Farm Preparation** (harvesting & fermentation, drying & roasting,
winnowing) happens near cocoa origin — weeks before material ever
reaches a factory. The company does not operate farms or origin
facilities; it buys already-processed cocoa nibs from suppliers. If
someone asks about fermentation time, roasting temperature at origin, or
harvest season, that is outside what any of the factories' own data can
answer — it's a supplier/sourcing question, not a production one.

**Factory Processing**, at the factory:

1. **Grinding** — nibs are ground into cocoa liquor (cocoa solids plus
   cocoa butter).
2. **Mixing & Refining** — sugar, milk powder, and additional cocoa
   butter are added and the mixture is ground finer for a smooth
   texture.
3. **Conching** — the mixture is continuously agitated and heated for
   hours to develop flavor and reduce acidity.

**Finishing**, at the factory:

4. **Tempering** — the chocolate is heated and cooled through a precise
   temperature curve to stabilize cocoa-butter crystals, giving the
   final product its gloss and snap.
5. **Molding & Cooling** — tempered chocolate is poured into molds,
   vibrated to remove air bubbles, and cooled to solidify.
6. **Packaging** — the hardened chocolate is demolded, quality-inspected,
   and packaged for shipment.

Every batch moves through these six stages, in this order, on one
production line.

## Recipes

The company currently produces three recipes:

| Recipe | Cacao % | Milk % | Sugar % |
|---|---|---|---|
| Dark 70% | 70 | 0 | 30 |
| Milk 35% | 35 | 25 | 40 |
| White | 0 | 30 | 45 |

A recipe determines a product's composition; products (sold to
customers) and batches (produced on a line) both reference a recipe.

## Who answers what

| If the question is about... | Ask |
|---|---|
| a production line, a batch's stage, sensor readings, defect rates, downtime, OEE | **Factory/Quality** |
| where cocoa/sugar/milk/packaging materials come from, supplier lead times, inventory on hand, shipments between factories | **Supply Chain** |
| customers, sales orders, products, invoices, payment status | **ERP/Orders** |
| anything spanning more than one of the above (e.g. "is low cocoa-nib inventory at Barcelona about to delay any batches on the conching line") | **Coordinator** — it calls the relevant specialists and combines their answers |

Batch, factory, and recipe are the identifiers shared across domains —
if an answer needs to connect a batch to an order or a shipment, that's
the seam the Coordinator bridges.
