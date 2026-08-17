output "FABRIC_WORKSPACE_ID" {
  description = "Fabric workspace ID -- <WORKSPACE_ID> in demo/eventhouse/eventstream.json"
  value       = local.workspace_id
}

output "FABRIC_EVENTHOUSE_ID" {
  description = "Eventhouse item ID -- <EVENTHOUSE_ITEM_ID> in demo/eventhouse/eventstream.json"
  value       = fabric_eventhouse.this.id
}

output "FABRIC_KQL_DATABASE_NAME" {
  description = "KQL database name -- <KQL_DATABASE_NAME> in demo/eventhouse/eventstream.json. Run demo/eventhouse/01-03 against this database."
  value       = fabric_kql_database.this.display_name
}

output "FABRIC_EVENTSTREAM_ID" {
  description = "Eventstream item ID, only set when existing_event_hub_connection_id was supplied"
  value       = try(fabric_eventstream.this[0].id, null)
}

output "FABRIC_WORKSPACE_IDENTITY_ENABLED" {
  description = "Whether the workspace has an identity Terraform could read (either just-created with enable_workspace_identity, or already present on an existing workspace)"
  value       = local.workspace_identity_service_principal_id != ""
}
