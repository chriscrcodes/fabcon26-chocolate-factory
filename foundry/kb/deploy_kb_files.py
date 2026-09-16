#!/usr/bin/env python3
"""Upload foundry/kb/*.md into the dimension Lakehouse's Files area, so a
Foundry IQ knowledge base's OneLake files indexer (see
foundry/kb/deploy_search_indexer.py) has something to index. Unlike
fabric/ontology/deploy_dimension_lakehouse.py, there's no "Load Table"
step here -- these are unstructured documents, not tabular data, so a
plain OneLake file upload is the whole job.

Auth: `az login` (AzureCliCredential), same pattern as the rest of
infra.
"""

import os
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.filedatalake import DataLakeServiceClient

HERE = Path(__file__).parent
KB_DOCS = ["00-company-overview.md", "01-factory-quality.md", "02-supply-chain.md", "03-erp-orders.md"]


def main() -> None:
    workspace_id = os.environ["FABRIC_WORKSPACE_ID"]
    lakehouse_id = os.environ["FABRIC_LAKEHOUSE_ID"]

    credential = AzureCliCredential()
    service_client = DataLakeServiceClient(
        account_url="https://onelake.dfs.fabric.microsoft.com", credential=credential
    )
    file_system_client = service_client.get_file_system_client(file_system=workspace_id)

    # Same GUID-path convention as deploy_dimension_lakehouse.py --
    # friendly-name path resolution is disabled on this tenant.
    directory_client = file_system_client.get_directory_client(f"{lakehouse_id}/Files/kb")
    directory_client.create_directory()

    for doc in KB_DOCS:
        file_client = directory_client.get_file_client(doc)
        data = (HERE / doc).read_bytes()
        file_client.upload_data(data, overwrite=True)
        print(f"uploaded {doc} to Files/kb/")


if __name__ == "__main__":
    main()
