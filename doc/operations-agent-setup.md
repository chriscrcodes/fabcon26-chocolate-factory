# Operations Agent — manual setup steps

`terraform apply` provisions the Operations Agent item
(`chocolate_factory_predictive_maintenance`) with its instructions,
data source, and alert rule already configured, plus the Activator
item (`chocolate-factory-operations-agent-connector`,
`FABRIC_OPERATIONS_AGENT_CONNECTOR_ID` output) that stores the alert
action's Power Automate connection. Two things can't be done via
Terraform or the REST API and need to be done once, by hand, in the
Fabric portal: confirming the Kusto data source is connected, and
connecting the alert action to a real Power Automate flow.

## 1. Connect the data source

1. Open the **chocolate_factory_predictive_maintenance** Operations
   Agent item in the Fabric workspace.
2. In the agent setup, confirm the **temperingTelemetry** data source
   points at the workspace's Eventhouse KQL database
   (`chocolate-factory-kql`). If it shows as disconnected, reconnect it
   to that KQL database.

## 2. Connect the SendTemperingAlert action to a Power Automate flow

1. Open the Operations Agent item's **Agent setup**, find the
   **SendTemperingAlert** action, and select **Edit**.
2. Select the workspace and the
   **chocolate-factory-operations-agent-connector** Activator item
   (already provisioned by `terraform apply`).
3. Select **Copy** to copy the connection string.
4. Select **Open flow builder** — this opens Power Automate in a new
   tab with the correct trigger already in place.
5. In the flow builder, paste the connection string into the trigger's
   **Connection string** field and select **Save**.
6. Add one action to the flow, for example:
   - **Post message in a chat or channel** (Teams), or
   - **Send an email (V2)**
7. In that action, use **dynamic content** to insert the `batchId` and
   `crystalFormIndex` values passed by the agent into the message
   body/subject.
8. Save the flow.
9. Back in the Fabric portal, the **SendTemperingAlert** action should
   now show as **Connected**.

## 3. Turn the agent on

In the agent setup, confirm **shouldRun** is enabled (the agent should
show as **Active**, not **Inactive**).

## 4. Verify

Run the simulator with anomaly injection targeting the tempering
stage's `CrystalFormIndex` and confirm the configured message (Teams
or email, per step 2.6 above) arrives within the agent's evaluation
cadence (~5 minutes). Repeat for 3 consecutive clean runs before
treating this as demo-ready.
