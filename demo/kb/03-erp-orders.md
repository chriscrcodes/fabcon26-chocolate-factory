# ERP / Orders Knowledge Base

For the ERP/Orders specialist agent. Covers products, customers, sales
orders, and invoicing.

> **Status:** the `customer`, `product`, `sales_order`, `order_line`, and
> `invoice` tables are defined in `demo/ontology/ontology_config.json`
> but have no generator or seed data yet (see the data model design
> memo). This document defines the intended business semantics ahead of
> that build.

## Products

A `product` is a sellable item: it references a `recipe` (see
`00-company-overview.md` for the three recipes — Dark 70%, Milk 35%,
White), a `PackagingType`, and a `SKU`. Multiple products can share a
recipe with different packaging (e.g. a bar vs. a box of individually
wrapped pieces) — when a question is really about the underlying
chocolate composition rather than the packaged product, the answer
should trace back through `RecipeId`.

## Customers

Customers are expected to fall into segments reflecting order volume and
channel — for example Retail, Wholesale, Food Service, and
Direct-to-Consumer (exact segment vocabulary to be finalized when the
generator is built). `CreditLimit` bounds how much outstanding balance a
customer can carry across open invoices; a new order that would push a
customer over their credit limit is a case worth flagging rather than
processing silently.

## Sales orders

A `sales_order` belongs to one customer and contains one or more
`order_line` rows, each referencing a `product` and a quantity/price.
Order `Status` is expected to progress through a lifecycle such as:
draft → confirmed → shipped → delivered (or cancelled at any point
before shipment). `TotalAmount`/`Currency` on the order should reconcile
with the sum of its order lines — a mismatch is a data-quality signal,
not a business one.

## Invoices

An `invoice` is generated against a `sales_order`, with an `IssueDate`,
`DueDate`, `AmountDue`, and — once settled — a `PaidDate`. An invoice
past its `DueDate` with no `PaidDate` is overdue; this is the figure
that matters for cash-flow questions ("how much is currently
outstanding," "which customers are late"), not simply counting unpaid
invoices.

## Where this connects to other domains

- A sales order's fulfillment status depends on whether the underlying
  production `batch` and `shipment` (Factory/Quality and Supply Chain
  domains) are on track — a genuinely cross-domain question belongs to
  the Coordinator, not this specialist alone.
- `product.RecipeId` is the seam back to `recipe`, which Factory/Quality
  also references via `batch.RecipeId` — the same recipe can be reasoned
  about from "what we're producing" and "what we're selling" angles.
