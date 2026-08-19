# sql-database

Supply Chain + ERP/Orders tables for the chocolate factory Fabric SQL
Database — the batch/transactional plane, as opposed to
[`../eventhouse/`](../eventhouse)'s streaming Factory/Quality telemetry
(see the medallion-layers design memo's two-plane design). 9 tables
across the two domains, plus 3 Gold views.

## What's here

- [`01_tables.sql`](01_tables.sql) — the 9 tables (`supplier`, `material`,
  `inventory`, `shipment`, `customer`, `product`, `sales_order`,
  `order_line`, `invoice`), matching `demo/ontology/ontology_config.json`
  column-for-column.
- [`02_gold.sql`](02_gold.sql) — `gold_inventory_position` (stockout-risk
  flag per factory/material), `gold_supplier_scorecard` (rating + volume
  per supplier), `gold_order_fulfillment_kpi` (payment status per order —
  `NotInvoiced`/`Outstanding`/`Overdue`/`Paid`).
- [`deploy_sql_database.py`](deploy_sql_database.py) — deploys both files
  and seeds the 9 tables from `demo/ontology/tables/*.csv` (written by
  `demo/data-generation/run_business_seed.py`). Uses
  [python-tds](https://python-tds.readthedocs.io/) (pure-Python TDS
  client) rather than `pyodbc`, so no native ODBC driver needs installing
  on the machine running `terraform apply` — same reasoning as every
  other script in this repo staying pure-Python. Auth is an Azure AD
  access token (`AzureCliCredential`, scope
  `https://database.windows.net/.default`).

`../infra/fabric.tf` provisions the empty `fabric_sql_database` item and
runs this script automatically via `null_resource.load_business_sql` —
see `../infra/README.md`.

## Verified against a live tenant

- `fabric_sql_database`'s computed `server_fqdn` comes back as
  `"<host>,<port>"` (e.g. `...database.fabric.microsoft.com,1433`), not a
  bare hostname — `deploy_sql_database.py` splits on the comma before
  handing it to `python-tds`.
- `python-tds` needs `cafile` set to actually enable TLS (there's no
  separate `encrypt=True` flag) — Fabric SQL Database requires it.
  `pyOpenSSL` also needs to be installed for `python-tds` to establish a
  TLS channel at all ("pyOpenSSL does not work" otherwise).
- `python-tds`'s own hostname-validation code (`tls.py`'s
  `validate_host`) hits an `AttributeError` against the installed
  pyOpenSSL/cryptography version — an internal library bug, not fixable
  from here. `deploy_sql_database.py` passes `validate_host=False` to
  skip it; safe here since the hostname comes straight from Terraform
  state, not untrusted input, and the connection is still TLS-encrypted
  via `cafile`.
- If the `local-exec` step fails with an SSL/cert error, the same
  corporate-TLS-proxy note in `../infra/README.md` applies — export
  `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`.

## Regenerating the seed data

```bash
cd ../data-generation
uv run run_business_seed.py
```

Deterministic (fixed random seed + reference date), so re-running
produces byte-identical CSVs — see `src/business_data.py`'s docstring for
the business-rule vocabulary it finalizes (shipment/order statuses,
customer segments) that the KB docs left open.
