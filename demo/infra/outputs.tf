# Names match demo/data-generation's .env.sample so these can be copied
# straight in.

output "AZURE_EVENT_HUB_NAMESPACE_HOSTNAME" {
  description = "Event Hub Namespace hostname -- AZURE_EVENT_HUB_NAMESPACE_HOSTNAME"
  value       = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net"
}

output "AZURE_EVENT_HUB_NAME" {
  description = "Event Hub name -- AZURE_EVENT_HUB_NAME"
  value       = azurerm_eventhub.this.name
}

output "AZURE_EVENT_HUB_NAMESPACE_ID" {
  description = "Event Hub Namespace resource ID"
  value       = azurerm_eventhub_namespace.this.id
}

output "LOCAL_AUTH_ENABLED" {
  description = "Whether local (SAS) auth is enabled -- false only when workspace_identity_principal_id was supplied"
  value       = !local.use_workspace_identity
}
