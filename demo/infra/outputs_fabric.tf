output "FABRIC_CAPACITY_ID" {
  description = "Microsoft.Fabric/capacities ARM resource ID."
  value       = azapi_resource.fabric_capacity.id
}

output "FABRIC_WORKSPACE_ID" {
  description = "Fabric workspace ID -- <WORKSPACE_ID> in demo/eventhouse/eventstream.json"
  value       = local.workspace_id
}

output "FABRIC_EVENTHOUSE_ID" {
  description = "Eventhouse item ID."
  value       = fabric_eventhouse.this.id
}

output "FABRIC_KQL_DATABASE_NAME" {
  description = "KQL database name -- <KQL_DATABASE_NAME> in demo/eventhouse/eventstream.json. Run demo/eventhouse/01-03 against this database."
  value       = fabric_kql_database.this.display_name
}

output "FABRIC_KQL_DATABASE_ITEM_ID" {
  description = "KQL database item ID -- <KQL_DATABASE_ITEM_ID> in demo/eventhouse/eventstream.json (destinations' itemId must be the database's own item, not the parent Eventhouse's)."
  value       = fabric_kql_database.this.id
}

output "FABRIC_CONNECTION_ID" {
  description = "Fabric Connection ID for the Event Hub cloud connection."
  value       = fabric_connection.event_hub.id
}

output "FABRIC_EVENTSTREAM_ID" {
  description = "Eventstream item ID."
  value       = fabric_eventstream.this.id
}

output "FABRIC_WORKSPACE_IDENTITY_ENABLED" {
  description = "Whether the workspace has an identity Terraform could read (either just-created with enable_workspace_identity, or already present on an existing workspace)"
  value       = local.workspace_identity_service_principal_id != ""
}
