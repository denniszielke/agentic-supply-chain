// =================================================================================================
// Autopilot (A365) infrastructure for the campaign digital worker.
//
// This layer is deployed AFTER the main `azd up` provisioning (which already
// creates the AI Foundry account/project, ACR, Container Apps env, etc.) and
// AFTER the Python steps that (1) create the Managed Agent Identity Blueprint
// and (2) grant the project identity its roles.
//
// It deliberately keeps ONLY the bot service here:
//
//   * botservice — an Azure Bot (+ Teams channel) that relays M365 interactions
//                  to the agent's activityProtocol endpoint, using the blueprint
//                  client id as appId.
//
// The MAIB creation and project role grants were moved OUT of bicep and into
// Python (scripts/autopilot/create_maib.py + grant_project_roles.py) because:
//   * Creating a MAIB is a data-plane operation that the sample ran from an
//     `AzurePowerShell` deploymentScript, which requires a key-based storage
//     account — blocked by policy in some tenants
//     ("KeyBasedAuthenticationNotPermitted").
//   * `azd up` already grants AcrPull to the project identity, so re-granting
//     it from bicep fails with `RoleAssignmentExists`. The Python grant tolerates
//     pre-existing assignments.
//
// Deploy the whole pipeline with `python -m scripts.deploy_campaign_autopilot`.
// =================================================================================================

targetScope = 'resourceGroup'

@description('Name of the existing Cognitive Services (AI Foundry) account.')
param accountName string

@description('Name of the existing Cognitive Services (AI Foundry) project.')
param projectName string

@description('Logical agent name (used to build the bot activity endpoint).')
param agentName string = 'campaign-a365-agent'

@description('Agent identity blueprint (app) client id, created by create_maib.py.')
param blueprintClientId string

@description('Name of the Bot Service.')
param botName string = '${agentName}-bot'

@description('Display name of the bot.')
param botDisplayName string = 'Campaign Planner'

@description('SKU of the Bot Service.')
param botServiceSku string = 'F0'

@description('Bot activity protocol API version.')
param activityProtocolApiVersion string = '2025-05-15-preview'

// Deploy the bot service and wire it to the blueprint identity + agent endpoint.
module botService 'modules/botservice.bicep' = {
  name: 'botservice-deployment'
  params: {
    botName: botName
    displayName: botDisplayName
    msaAppId: blueprintClientId
    endpoint: 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}/agents/${agentName}/endpoint/protocols/activityProtocol?api-version=${activityProtocolApiVersion}'
    botServiceSku: botServiceSku
  }
}

// =================================================================================================
// Outputs — consumed by the Python wrapper (scripts/deploy_campaign_autopilot.py)
// =================================================================================================

@description('Agent identity blueprint (app) client id.')
output AGENT_IDENTITY_BLUEPRINT_ID string = blueprintClientId

output AGENT_NAME string = agentName

output BOT_NAME string = botService.outputs.botName
