#!/usr/bin/env python3
"""Configure Azure AI Search to index foundry/kb/*.md straight out of the
dimension Lakehouse's Files/kb/ folder (uploaded by deploy_kb_files.py),
via a native OneLake files indexer -- no custom ETL/chunking pipeline,
per https://learn.microsoft.com/en-us/azure/search/search-how-to-index-onelake-files.

The Foundry IQ *knowledge base* is just another Azure AI Search
data-plane object (`PUT .../knowledgebases/{name}`), same family as
datasources/indexes/indexers/knowledgesources. It needs
`api-version=2026-05-01-preview` specifically -- the GA version used
for everything else here doesn't yet support the fields it requires
(`outputMode`, `retrievalReasoningEffort`).

Five REST calls, `PUT .../{kind}/{name}?api-version=<version>`:
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
4. Knowledge source: a `searchIndex`-kind object wrapping the index
   above, required for the *Foundry portal* to recognize this index as
   eligible when creating a knowledge base -- verified live that a
   populated, queryable index is not sufficient by itself. The Foundry
   portal's "Add knowledge base" wizard populates its picker from
   `GET .../knowledgesources`, not from the index list directly; an
   index with no wrapping knowledge source (or one lacking semantic
   configuration) produces "No supported knowledge sources available.
   Create one first." even though the index itself is fully functional.
   Two things had to be added that the original index definition (above)
   didn't have: a `semantic.configurations[]` block (with a
   `defaultConfiguration` pointing at it) on the index itself, and the
   knowledge source's own `searchIndexParameters.semanticConfigurationName`
   referencing that same configuration by name.
5. Knowledge base: wraps the knowledge source above,
   `outputMode: "extractiveData"` (no LLM-generated answers, just
   ranked passages) and `retrievalReasoningEffort: {"kind": "medium"}`,
   which performs query planning/iterative search over the knowledge
   source before returning passages -- unlike `"minimal"`, this
   requires an attached chat-completion model (`models[]`, referencing
   the same `gpt-5.4-mini` deployment `azure.tf` provisions for the
   agent itself). The Search service authenticates to that model via
   its own system-assigned identity (`azurerm_role_assignment.
   search_foundry_openai_user` in `azure.tf`) rather than an API key.
   Querying this also requires semantic ranking enabled at the
   *service* level (`semantic_search_sku` in `azure.tf`'s
   `azurerm_search_service.kb` -- a separate setting from the index's
   own `semantic.configurations[]` block above).

Auth to the Search service's *management* REST API itself uses its
admin key (Terraform output, not a Fabric/Azure AD token) -- simplest
option for a few idempotent PUT calls, consistent with local auth
being enabled by default on the service (azure.tf).
"""

import os

import requests

API_VERSION = "2026-04-01"
PREVIEW_API_VERSION = "2026-05-01-preview"  # required for knowledgebases -- see module docstring
INDEX_NAME = "chocolate-factory-kb"
DATASOURCE_NAME = "chocolate-factory-kb-onelake"
INDEXER_NAME = "chocolate-factory-kb-indexer"
SEMANTIC_CONFIG_NAME = "chocolate-factory-kb-semantic"
KNOWLEDGE_SOURCE_NAME = "chocolate-factory-kb-source"
KNOWLEDGE_BASE_NAME = "chocolate-factory-kb"


def put(
    session: requests.Session, endpoint: str, kind: str, name: str, body: dict, api_version: str = API_VERSION
) -> None:
    resp = session.put(f"{endpoint}/{kind}/{name}", params={"api-version": api_version}, json=body)
    resp.raise_for_status()


def main() -> None:
    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"].rstrip("/")
    admin_key = os.environ["AZURE_SEARCH_ADMIN_KEY"]
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    lakehouse_id = os.environ["FABRIC_LAKEHOUSE_ID"]
    foundry_endpoint = os.environ["AZURE_FOUNDRY_ACCOUNT_ENDPOINT"]
    foundry_model_deployment = os.environ["AZURE_FOUNDRY_MODEL_DEPLOYMENT_NAME"]

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
            "semantic": {
                "defaultConfiguration": SEMANTIC_CONFIG_NAME,
                "configurations": [
                    {
                        "name": SEMANTIC_CONFIG_NAME,
                        "prioritizedFields": {
                            "titleField": {"fieldName": "metadata_storage_name"},
                            "prioritizedContentFields": [{"fieldName": "content"}],
                        },
                    }
                ],
            },
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

    put(
        session,
        endpoint,
        "knowledgesources",
        KNOWLEDGE_SOURCE_NAME,
        {
            "name": KNOWLEDGE_SOURCE_NAME,
            "kind": "searchIndex",
            "searchIndexParameters": {
                "searchIndexName": INDEX_NAME,
                "semanticConfigurationName": SEMANTIC_CONFIG_NAME,
            },
        },
    )
    print(f"knowledge source {KNOWLEDGE_SOURCE_NAME} configured")

    put(
        session,
        endpoint,
        "knowledgebases",
        KNOWLEDGE_BASE_NAME,
        {
            "name": KNOWLEDGE_BASE_NAME,
            "outputMode": "extractiveData",
            "knowledgeSources": [{"name": KNOWLEDGE_SOURCE_NAME}],
            "retrievalReasoningEffort": {"kind": "medium"},
            "models": [
                {
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": foundry_endpoint,
                        "deploymentId": foundry_model_deployment,
                        "modelName": foundry_model_deployment,
                    },
                }
            ],
        },
        api_version=PREVIEW_API_VERSION,
    )
    print(f"knowledge base {KNOWLEDGE_BASE_NAME} configured")


if __name__ == "__main__":
    main()
