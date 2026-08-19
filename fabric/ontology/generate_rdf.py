#!/usr/bin/env python3
"""Converts ontology_config.json to RDF/XML (OWL) for microsoft/Ontology-Playground.

The Playground's hosted site (microsoft.github.io/Ontology-Playground) only
accepts RDF/XML/.owl/.iq import by default -- its JSON import is a
disabled-by-default legacy path with a different shape (entityTypes/
properties/cardinality) than our tables/columns/types. RDF/XML is also, per
the Playground's own docs, "the exact format Microsoft Fabric IQ expects,"
so this is the more useful artifact either way.

Usage:
    python generate_rdf.py [ontology_config.json] [chocolate.rdf]
"""

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

BASE = "http://example.org/ontology/chocolate-factory/"

# our type -> (Playground propertyType, XSD range)
TYPE_MAP = {
    "String": ("string", "xsd:string"),
    "Int": ("integer", "xsd:int"),
    "BigInt": ("integer", "xsd:long"),
    "Float": ("decimal", "xsd:float"),
    "Double": ("double", "xsd:double"),
    "Boolean": ("boolean", "xsd:boolean"),
    "DateTime": ("datetime", "xsd:dateTime"),
    "Date": ("date", "xsd:date"),
    "Time": ("string", "xsd:time"),
}

# domain -> (icon, color), purely cosmetic in the Playground canvas
DOMAIN_STYLE = {
    "factory_quality": ("🏭", "#A5522A"),
    "supply_chain": ("🚚", "#2F6E63"),
    "erp_orders": ("🧾", "#5B4B8A"),
}

TABLE_DOMAIN = {
    "factory": "factory_quality",
    "production_line": "factory_quality",
    "production_stage": "factory_quality",
    "recipe": "factory_quality",
    "batch": "factory_quality",
    "sensor_reading": "factory_quality",
    "quality_check": "factory_quality",
    "supplier": "supply_chain",
    "material": "supply_chain",
    "inventory": "supply_chain",
    "shipment": "supply_chain",
    "customer": "erp_orders",
    "product": "erp_orders",
    "sales_order": "erp_orders",
    "order_line": "erp_orders",
    "invoice": "erp_orders",
}


def class_name(table: str) -> str:
    """snake_case table name -> PascalCase OWL class name."""
    return "".join(part.capitalize() for part in table.split("_"))


def _comment_safe(text: str) -> str:
    """XML comments can't contain '--' -- collapse it so text stays valid."""
    return text.replace("--", "-")


def build_rdf(config: dict) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rdf:RDF',
        f'    xml:base="{BASE}"',
        '    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
        '    xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"',
        '    xmlns:owl="http://www.w3.org/2002/07/owl#"',
        '    xmlns:xsd="http://www.w3.org/2001/XMLSchema#"',
        f'    xmlns:ont="{BASE}">',
        "",
        f"    <!-- {_comment_safe(config.get('name', 'Ontology'))} -->",
        f"    <!-- {_comment_safe(config.get('description', ''))} -->",
        "",
        "    <!-- Classes -->",
        "",
    ]

    tables = config["tables"]

    for table_name, table in tables.items():
        cls = class_name(table_name)
        domain = TABLE_DOMAIN.get(table_name, "factory_quality")
        icon, color = DOMAIN_STYLE[domain]
        notes = table.get("notes", "")
        lines += [
            f'    <owl:Class rdf:about="{BASE}{cls}">',
            f"        <rdfs:label>{escape(cls)}</rdfs:label>",
            f"        <rdfs:comment>{escape(notes or table_name)}</rdfs:comment>",
            f"        <ont:icon>{icon}</ont:icon>",
            f"        <ont:color>{color}</ont:color>",
            "    </owl:Class>",
            "",
        ]

    for table_name, table in tables.items():
        cls = class_name(table_name)
        lines.append(f"    <!-- Data Properties: {cls} -->")
        lines.append("")
        key_col = table["key"]
        for col in table["columns"]:
            prop_type, xsd_range = TYPE_MAP[table["types"][col]]
            prop_id = f"{table_name}_{col}"
            lines += [
                f'    <owl:DatatypeProperty rdf:about="{BASE}{prop_id}">',
                f"        <rdfs:label>{escape(col)}</rdfs:label>",
                f'        <rdfs:domain rdf:resource="{BASE}{cls}"/>',
                f'        <rdfs:range rdf:resource="http://www.w3.org/2001/XMLSchema#{xsd_range.split(":")[1]}"/>',
                f"        <ont:propertyType>{prop_type}</ont:propertyType>",
            ]
            if col == key_col:
                lines.append(
                    '        <ont:isIdentifier rdf:datatype="http://www.w3.org/2001/XMLSchema#boolean">true</ont:isIdentifier>'
                )
            lines.append("    </owl:DatatypeProperty>")
            lines.append("")

    lines.append("    <!-- Object Properties (relationships) -->")
    lines.append("")
    for rel in config["relationships"]:
        from_cls = class_name(rel["from"])
        to_cls = class_name(rel["to"])
        lines += [
            f'    <owl:ObjectProperty rdf:about="{BASE}{rel["name"]}">',
            f"        <rdfs:label>{escape(rel['name'])}</rdfs:label>",
            f'        <rdfs:domain rdf:resource="{BASE}{from_cls}"/>',
            f'        <rdfs:range rdf:resource="{BASE}{to_cls}"/>',
            f"        <rdfs:comment>{escape(rel['fromKey'])} -&gt; {escape(rel['toKey'])}</rdfs:comment>",
            "        <ont:cardinality>many-to-one</ont:cardinality>",
            f"        <ont:fromEntityId>{from_cls}</ont:fromEntityId>",
            f"        <ont:toEntityId>{to_cls}</ont:toEntityId>",
            "    </owl:ObjectProperty>",
            "",
        ]

    lines.append("</rdf:RDF>")
    return "\n".join(lines)


def main() -> None:
    here = Path(__file__).parent
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "ontology_config.json"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "chocolate.rdf"

    config = json.loads(config_path.read_text(encoding="utf-8"))
    rdf = build_rdf(config)
    out_path.write_text(rdf, encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
