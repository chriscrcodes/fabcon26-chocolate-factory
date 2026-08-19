#!/usr/bin/env python3
"""Deploy 01_tables.sql/02_gold.sql to the Fabric SQL Database and seed
its 9 tables from fabric/ontology/tables/*.csv.

Uses python-tds (pure-Python TDS protocol client) rather than pyodbc --
avoids needing the msodbcsql18 native ODBC driver installed on whatever
machine runs `terraform apply`, consistent with every other script in
this repo (azure-kusto-data, azure-storage-file-datalake, ...) staying
pure-Python. Auth is an Azure AD access token
(`AzureCliCredential`, scope `https://database.windows.net/.default`),
passed via pytds's `access_token_callable`.

Statement splitting: each .sql file uses "GO" on its own line as the
batch separator (the standard T-SQL scripting convention) -- required
for `CREATE VIEW`, which must be the only statement in its batch.

Usage:
    FABRIC_SQL_SERVER_FQDN=... FABRIC_SQL_DATABASE_NAME=... \\
        uv run --with azure-identity --with python-tds \\
        deploy_sql_database.py
"""

import csv
import os
import re
from pathlib import Path

import certifi
import pytds
from azure.identity import AzureCliCredential

HERE = Path(__file__).parent
TABLES_DIR = HERE / ".." / "ontology" / "tables"

# Load order respects FK dependencies (supplier -> material -> inventory;
# customer/product -> sales_order -> order_line/invoice).
SEED_TABLES = [
    "supplier", "material", "inventory", "shipment",
    "customer", "product", "sales_order", "order_line", "invoice",
]


def split_batches(text: str) -> list[str]:
    lines = text.splitlines()
    batches, buffer = [], []
    for line in lines:
        if line.strip().upper() == "GO":
            if buffer:
                batches.append("\n".join(buffer))
                buffer = []
            continue
        buffer.append(line)
    if buffer and "\n".join(buffer).strip():
        batches.append("\n".join(buffer))
    return batches


def run_sql_file(cursor, path: Path) -> None:
    batches = split_batches(path.read_text())
    print(f"=== {path.name}: {len(batches)} batch(es) ===")
    for i, batch in enumerate(batches, 1):
        head = re.sub(r"\s+", " ", batch.strip())[:70]
        try:
            cursor.execute(batch)
            print(f"  [{i}/{len(batches)}] OK  {head}")
        except pytds.Error as e:
            message = str(e)
            if "There is already an object named" in message:
                print(f"  [{i}/{len(batches)}] SKIP (already exists) {head}")
                continue
            print(f"  [{i}/{len(batches)}] FAIL {head}\n      {e}")
            raise


def seed_table(cursor, table: str) -> None:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    (count,) = cursor.fetchone()
    if count > 0:
        print(f"seed: {table} already has {count} row(s), skipping")
        return

    csv_path = TABLES_DIR / f"{table}.csv"
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [[value if value != "" else None for value in row] for row in reader]
    if not rows:
        return

    placeholders = ", ".join(["%s"] * len(header))
    columns = ", ".join(header)
    cursor.executemany(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", rows
    )
    print(f"seed: inserted {len(rows)} row(s) into {table}")


def main() -> None:
    # server_fqdn comes back from Fabric as "<host>,<port>" (verified
    # live -- e.g. "....database.fabric.microsoft.com,1433"), not a bare
    # hostname; pytds wants them separate.
    raw_server = os.environ["FABRIC_SQL_SERVER_FQDN"]
    server_fqdn, _, port_str = raw_server.partition(",")
    port = int(port_str) if port_str else 1433
    database_name = os.environ["FABRIC_SQL_DATABASE_NAME"]

    credential = AzureCliCredential()

    def get_token() -> str:
        return credential.get_token("https://database.windows.net/.default").token

    with pytds.connect(
        server=server_fqdn,
        database=database_name,
        port=port,
        access_token_callable=get_token,
        autocommit=True,
        # Fabric SQL Database requires TLS -- pytds only enables it when
        # `cafile` is set (there's no separate encrypt=True flag).
        # SSL_CERT_FILE lets this and every other script in this repo
        # share one CA bundle override for corporate TLS-inspecting
        # proxies (see infra/README.md's Zscaler troubleshooting
        # note); falls back to certifi's default bundle otherwise.
        cafile=os.environ.get("SSL_CERT_FILE") or certifi.where(),
        # pytds's own hostname-validation code (tls.py's validate_host)
        # hits an incompatibility with the installed pyOpenSSL/
        # cryptography version ("'X509' object has no attribute
        # 'get_extension'") -- an internal pytds bug, not something
        # fixable from here. Safe to skip: the hostname comes straight
        # from Terraform state (fabric_sql_database's own computed
        # server_fqdn), not untrusted input, and the connection is still
        # TLS-encrypted via `cafile` above.
        validate_host=False,
    ) as conn:
        with conn.cursor() as cursor:
            run_sql_file(cursor, HERE / "01_tables.sql")
            run_sql_file(cursor, HERE / "02_gold.sql")
            for table in SEED_TABLES:
                seed_table(cursor, table)


if __name__ == "__main__":
    main()
