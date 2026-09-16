"""Sends batches of JSON events to Azure Event Hub.

Auth: uses an Event Hub connection string if AZURE_EVENT_HUB_CONNECTION_STRING
is set (simplest for local/demo use); otherwise falls back to AzureCliCredential
against AZURE_EVENT_HUB_NAMESPACE_HOSTNAME, matching the auth pattern used by
sources/fabric-data-generation's EventHubService.
"""

import json
from typing import Any

try:
    from azure.eventhub import EventData, EventHubProducerClient
except ImportError as e:
    raise ImportError(
        "azure-eventhub is required. Install with: "
        "pip install -r simulator/requirements.txt"
    ) from e


class EventHubService:
    def __init__(
        self,
        connection_string: str | None = None,
        fully_qualified_namespace: str | None = None,
        event_hub_name: str | None = None,
    ) -> None:
        if connection_string:
            self._producer = EventHubProducerClient.from_connection_string(
                conn_str=connection_string,
                eventhub_name=event_hub_name,
            )
        elif fully_qualified_namespace and event_hub_name:
            from azure.identity import AzureCliCredential

            self._producer = EventHubProducerClient(
                fully_qualified_namespace=fully_qualified_namespace,
                eventhub_name=event_hub_name,
                credential=AzureCliCredential(),
            )
        else:
            raise ValueError(
                "Provide either connection_string or "
                "(fully_qualified_namespace and event_hub_name)."
            )

    def send_events(self, events: list[Any]) -> None:
        """Send a list of JSON-serializable dicts as one batch.

        Reuses the producer connection across calls -- do not wrap this in
        a `with` block, or the client closes after the first send.
        """
        if not events:
            return

        batch = self._producer.create_batch()
        for payload in events:
            data = EventData(json.dumps(payload))
            data.properties = {
                "content-type": "application/json",
                "source": "chocolate-factory-data-generator",
            }
            try:
                batch.add(data)
            except ValueError:
                # Batch is full -- flush and start a new one.
                self._producer.send_batch(batch)
                batch = self._producer.create_batch()
                batch.add(data)

        if len(batch) > 0:
            self._producer.send_batch(batch)

    def close(self) -> None:
        self._producer.close()
