#!/usr/bin/env python3
"""Configure Azure AI Search to index foundry/kb/*.md straight out of the
dimension Lakehouse's Files/kb/ folder (uploaded by deploy_kb_files.py),
via a native OneLake files indexer -- no custom ETL/chunking pipeline,
per https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files.

Note this is only the Search-side plumbing (data source + index +
indexer). The Foundry IQ *knowledge base* on top of this index has no
documented Terraform/CLI/REST path yet -- see foundry/kb/README.md -- and
is still a one-time manual step in the Foundry portal.

Three REST calls, `PUT .../{kind}/{name}?api-version=2026-04-01`:
1. Data source: type "onelake", container.name = the Lakehouse's item
   ID, container.query = "kb" (the Files/kb/ subfolder -- container
   paths are relative to the Lakehouse's Files root, not prefixed with
   "Files/"). credentials.connectionString = "ResourceId=<workspace
   ID>" -- this is what tells the indexer to authenticate as the search
   service's own system-assigned managed identity (azure.tf), already
   granted Contributor on the workspace (fabric.tf) -- no key/secret
   involved.
2. Index: one key field (populated from OneLake's own
   metadata_storage_path) plus a searchable content field -- auto-mapped
   by name/type from the indexer's default field mappings, no explicit
   fieldMappings needed.
3. Indexer: ties the two together; parsingMode "default" treats each
   .md file as plain text (no markdown-aware chunking -- these are short,
   single-topic docs, so section-level chunking isn't needed here).
   OneLake's metadata_storage_path values are full URLs
   (https://onelake.blob.fabric.microsoft.com/...), which contain
   characters (":", "/") the key field can't hold raw -- verified live,
   first run failed with "Invalid document key" on every item. Fixed
   with the standard blob-indexer fieldMappings base64Encode function on
   the key field (https://learn.microsoft.com/azure/search/search-howto-indexing-azure-blob-storage#DocumentKeys).

Auth to the Search service's *management* REST API itself uses its
admin key (Terraform output, not a Fabric/Azure AD token) -- simplest
option for a few idempotent PUT calls, consistent with local auth
being enabled by default on the service (azure.tf).
"""

import os

import requests

API_VERSION = "2026-04-01"
INDEX_NAME = "chocolate-factory-kb"
DATASOURCE_NAME = "chocolate-factory-kb-onelake"
INDEXER_NAME = "chocolate-factory-kb-indexer"


def put(session: requests.Session, endpoint: str, kind: str, name: str, body: dict) -> None:
    resp = session.put(f"{endpoint}/{kind}/{name}", params={"api-version": API_VERSION}, json=body)
    resp.raise_for_status()


def main() -> None:
    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"].rstrip("/")
    admin_key = os.environ["AZURE_SEARCH_ADMIN_KEY"]
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    lakehouse_id = os.environ["FABRIC_LAKEHOUSE_ID"]

    session = requests.Session()
    session.headers.update({"api-key": admin_key, "Content-Type": "application/json"})

    put(
        session,
        endpoint,
        "datasources",
        DATASOURCE_NAME,
        {
            "name": DATASOURCE_NAME,
            "type": "onelake",
            "credentials": {"connectionString": f"ResourceId={workspace_id}"},
            "container": {"name": lakehouse_id, "query": "kb"},
        },
    )
    print(f"data source {DATASOURCE_NAME} configured")

    put(
        session,
        endpoint,
        "indexes",
        INDEX_NAME,
        {
            "name": INDEX_NAME,
            "fields": [
                {
                    "name": "metadata_storage_path",
                    "type": "Edm.String",
                    "key": True,
                    "searchable": False,
                    "filterable": False,
                    "sortable": False,
                },
                {
                    "name": "metadata_storage_name",
                    "type": "Edm.String",
                    "searchable": False,
                    "filterable": True,
                    "sortable": True,
                },
                {
                    "name": "content",
                    "type": "Edm.String",
                    "searchable": True,
                    "filterable": False,
                    "sortable": False,
                },
            ],
        },
    )
    print(f"index {INDEX_NAME} configured")

    put(
        session,
        endpoint,
        "indexers",
        INDEXER_NAME,
        {
            "name": INDEXER_NAME,
            "dataSourceName": DATASOURCE_NAME,
            "targetIndexName": INDEX_NAME,
            "parameters": {"configuration": {"dataToExtract": "contentAndMetadata", "parsingMode": "default"}},
            "fieldMappings": [
                {
                    "sourceFieldName": "metadata_storage_path",
                    "targetFieldName": "metadata_storage_path",
                    "mappingFunction": {"name": "base64Encode"},
                }
            ],
        },
    )
    print(f"indexer {INDEXER_NAME} configured and running")


if __name__ == "__main__":
    main()
