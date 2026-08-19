"""Generates the Supply Chain + ERP/Orders seed CSVs (9 tables) for the
chocolate scenario -- the two domains the medallion-layers design memo
puts on a Fabric SQL Database plane rather than the Eventhouse, since
they're batch/transactional business data, not streaming telemetry.

Business semantics follow foundry/kb/02-supply-chain.md and
foundry/kb/03-erp-orders.md (materials, supplier rating, inventory reorder
logic, shipment/order/invoice lifecycles) -- those docs left some
vocabulary "to be finalized when the generator is built"; this is where
that happens:

- shipment.Status: Pending -> InTransit -> Delivered
- customer.Segment: Retail, Wholesale, FoodService, DirectToConsumer
- sales_order.Status: Draft -> Confirmed -> Shipped -> Delivered
  (or Cancelled before shipping)

Deterministic (fixed random seed) so re-running produces the same data,
matching seed_data.py/generate_rdf.py's reproducibility. Output goes to
fabric/ontology/tables/ alongside the existing Factory/Quality dimension
CSVs -- loaded into the Fabric SQL Database by
fabric/sql-database/deploy_sql_database.py.
"""

import csv
import random
import uuid
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
# Fixed reference date, not datetime.now() -- keeps output byte-for-byte
# reproducible across re-runs, matching the seed value above.

MATERIAL_TYPES = ["Cocoa Nibs", "Sugar", "Milk", "Packaging"]
FACTORY_IDS = ["FAC-BCN", "FAC-CHI", "FAC-GRU", "FAC-SIN"]
RECIPE_IDS = ["RCP-DARK70", "RCP-MILK35", "RCP-WHITE"]
CUSTOMER_SEGMENTS = ["Retail", "Wholesale", "FoodService", "DirectToConsumer"]
TODAY = date(2026, 8, 19)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")


def build_suppliers() -> list[dict]:
    names = {
        "Cocoa Nibs": ["Orinoco Cacao Cooperative", "Ashanti Bean Traders", "Bahia Origin Farms"],
        "Sugar": ["Cristal Sugar Mills", "Valle Dulce Refinery"],
        "Milk": ["Alpine Dairy Co-op", "Nordland Milk Powder"],
        "Packaging": ["Wrapline Packaging", "BoxCraft Industrial"],
    }
    countries = {
        "Orinoco Cacao Cooperative": "Venezuela", "Ashanti Bean Traders": "Ghana",
        "Bahia Origin Farms": "Brazil", "Cristal Sugar Mills": "Brazil",
        "Valle Dulce Refinery": "Mexico", "Alpine Dairy Co-op": "Switzerland",
        "Nordland Milk Powder": "Denmark", "Wrapline Packaging": "Germany",
        "BoxCraft Industrial": "Poland",
    }
    suppliers = []
    for material_type, supplier_names in names.items():
        for name in supplier_names:
            suppliers.append(
                {
                    "SupplierId": f"SUP-{uuid.uuid5(uuid.NAMESPACE_DNS, name).hex[:8].upper()}",
                    "Name": name,
                    "Country": countries[name],
                    "MaterialType": material_type,
                    "Rating": round(random.uniform(2.8, 4.9), 1),
                }
            )
    return suppliers


def build_materials(suppliers: list[dict]) -> list[dict]:
    materials = []
    for supplier in suppliers:
        for i in range(random.randint(3, 5)):
            received = TODAY - timedelta(days=random.randint(1, 120))
            materials.append(
                {
                    "MaterialId": f"MAT-{uuid.uuid4().hex[:10].upper()}",
                    "SupplierId": supplier["SupplierId"],
                    "Type": supplier["MaterialType"],
                    "LotNumber": f"LOT-{received.strftime('%Y%m%d')}-{i:02d}",
                    "ReceivedDate": received.isoformat(),
                    "QuantityKg": round(random.uniform(500, 5000), 1),
                }
            )
    return materials


def build_inventory(materials: list[dict]) -> list[dict]:
    # One inventory row per factory per material type -- ReorderLevel is
    # fixed per type, QuantityOnHand randomized so a few rows land below
    # it (stockout-risk demo narrative, especially for Cocoa Nibs, per
    # the KB doc's note that it's the material most likely to actually
    # halt a line).
    reorder_levels = {"Cocoa Nibs": 800.0, "Sugar": 500.0, "Milk": 300.0, "Packaging": 1000.0}
    inventory = []
    for factory_id in FACTORY_IDS:
        for material_type in MATERIAL_TYPES:
            reorder_level = reorder_levels[material_type]
            low_stock = random.random() < 0.2
            quantity = (
                round(random.uniform(reorder_level * 0.4, reorder_level * 0.95), 1)
                if low_stock
                else round(random.uniform(reorder_level * 1.1, reorder_level * 4), 1)
            )
            lots_of_type = [m for m in materials if m["Type"] == material_type]
            inventory.append(
                {
                    "InventoryId": f"INV-{factory_id}-{material_type.replace(' ', '')}",
                    "FactoryId": factory_id,
                    "MaterialId": random.choice(lots_of_type)["MaterialId"],
                    "QuantityOnHand": quantity,
                    "ReorderLevel": reorder_level,
                    "LastUpdated": (TODAY - timedelta(days=random.randint(0, 3))).isoformat(),
                }
            )
    return inventory


def build_shipments() -> list[dict]:
    # BatchId references are synthetic (plausible-looking, matching the
    # simulator's own ID shape) rather than literal live streamed
    # BatchIds -- this is a one-time seed, not wired to the running
    # simulator's actual batch stream.
    carriers = ["Maersk Line", "DHL Freight", "FedEx Trade Networks", "Kuehne+Nagel"]
    statuses = ["Pending", "InTransit", "Delivered"]
    shipments = []
    for i in range(24):
        factory_id = random.choice(FACTORY_IDS)
        depart = TODAY - timedelta(days=random.randint(0, 30))
        status = random.choices(statuses, weights=[0.2, 0.3, 0.5])[0]
        shipments.append(
            {
                "ShipmentId": f"SHIP-{i:04d}",
                "FromFactoryId": factory_id,
                "ToLocationId": f"DC-{random.choice(['EU', 'NA', 'LATAM', 'APAC'])}-{random.randint(1, 3)}",
                "BatchId": f"BATCH-{factory_id}-L{random.randint(1, 3)}-{depart.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4]}",
                "Carrier": random.choice(carriers),
                "DepartDate": depart.isoformat(),
                "ArriveDate": (depart + timedelta(days=random.randint(3, 14))).isoformat()
                if status != "Pending"
                else "",
                "Status": status,
            }
        )
    return shipments


def build_customers() -> list[dict]:
    names = [
        "Nordic Confectionery Group", "Iberia Retail Alliance", "Great Lakes Foodservice",
        "Pacific Rim Distributors", "Andes Gourmet Imports", "Sunset Boulevard Cafes",
        "Baltic Sweets Wholesale", "Delta Direct-to-Door", "Highland Chocolatiers",
        "Meridian Hospitality Group", "Coastal Trade Partners", "Riverside Specialty Foods",
    ]
    countries = ["Sweden", "Spain", "United States", "Singapore", "Peru", "United States",
                 "Poland", "Canada", "Scotland", "United Arab Emirates", "Portugal", "Australia"]
    customers = []
    for name, country in zip(names, countries, strict=True):
        customers.append(
            {
                "CustomerId": f"CUST-{uuid.uuid5(uuid.NAMESPACE_DNS, name).hex[:8].upper()}",
                "Name": name,
                "Country": country,
                "Segment": random.choice(CUSTOMER_SEGMENTS),
                "CreditLimit": round(random.choice([10000, 25000, 50000, 100000, 250000]), 2),
            }
        )
    return customers


def build_products() -> list[dict]:
    packaging = {"RCP-DARK70": ["Bar", "GiftBox"], "RCP-MILK35": ["Bar", "IndividuallyWrapped"], "RCP-WHITE": ["Bar"]}
    names = {"RCP-DARK70": "Dark 70%", "RCP-MILK35": "Milk 35%", "RCP-WHITE": "White"}
    products = []
    for recipe_id, pack_types in packaging.items():
        for pack in pack_types:
            products.append(
                {
                    "ProductId": f"PROD-{recipe_id}-{pack}",
                    "Name": f"{names[recipe_id]} {pack}",
                    "RecipeId": recipe_id,
                    "PackagingType": pack,
                    "SKU": f"SKU-{recipe_id[-6:]}-{pack[:3].upper()}",
                }
            )
    return products


def build_orders_lines_invoices(customers: list[dict], products: list[dict]) -> tuple[list, list, list]:
    orders, lines, invoices = [], [], []
    statuses_open = ["Draft", "Confirmed", "Shipped", "Delivered", "Cancelled"]
    for i in range(40):
        customer = random.choice(customers)
        order_date = TODAY - timedelta(days=random.randint(1, 60))
        status = random.choices(statuses_open, weights=[0.05, 0.15, 0.2, 0.5, 0.1])[0]
        order_id = f"ORD-{i:05d}"

        n_lines = random.randint(1, 3)
        chosen_products = random.sample(products, n_lines)
        total = 0.0
        for j, product in enumerate(chosen_products):
            quantity = round(random.uniform(50, 500), 1)
            unit_price = round(random.uniform(8, 22), 2)
            total += quantity * unit_price
            lines.append(
                {
                    "OrderLineId": f"{order_id}-L{j + 1}",
                    "OrderId": order_id,
                    "ProductId": product["ProductId"],
                    "QuantityKg": quantity,
                    "UnitPrice": unit_price,
                }
            )

        orders.append(
            {
                "OrderId": order_id,
                "CustomerId": customer["CustomerId"],
                "OrderDate": order_date.isoformat(),
                "Status": status,
                "TotalAmount": round(total, 2),
                "Currency": "USD",
            }
        )

        if status in ("Confirmed", "Shipped", "Delivered"):
            issue_date = order_date + timedelta(days=random.randint(1, 3))
            due_date = issue_date + timedelta(days=30)
            # Overdue on purpose for a chunk of these -- the KB doc's
            # cash-flow narrative needs some to actually be overdue.
            paid = due_date >= TODAY or random.random() > 0.35
            paid_date = (
                due_date - timedelta(days=random.randint(0, 10))
            ).isoformat() if paid and status == "Delivered" else ""
            invoices.append(
                {
                    "InvoiceId": f"INV-{order_id}",
                    "OrderId": order_id,
                    "IssueDate": issue_date.isoformat(),
                    "DueDate": due_date.isoformat(),
                    "AmountDue": round(total, 2),
                    "PaidDate": paid_date,
                }
            )

    return orders, lines, invoices


def main() -> None:
    tables_dir = Path(__file__).resolve().parents[2] / "fabric" / "ontology" / "tables"

    suppliers = build_suppliers()
    materials = build_materials(suppliers)
    inventory = build_inventory(materials)
    shipments = build_shipments()
    customers = build_customers()
    products = build_products()
    orders, lines, invoices = build_orders_lines_invoices(customers, products)

    write_csv(tables_dir / "supplier.csv", suppliers)
    write_csv(tables_dir / "material.csv", materials)
    write_csv(tables_dir / "inventory.csv", inventory)
    write_csv(tables_dir / "shipment.csv", shipments)
    write_csv(tables_dir / "customer.csv", customers)
    write_csv(tables_dir / "product.csv", products)
    write_csv(tables_dir / "sales_order.csv", orders)
    write_csv(tables_dir / "order_line.csv", lines)
    write_csv(tables_dir / "invoice.csv", invoices)


if __name__ == "__main__":
    main()
