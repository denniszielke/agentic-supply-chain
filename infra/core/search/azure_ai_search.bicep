targetScope = 'resourceGroup'

@description('Tags that will be applied to all resources')
param tags object = {}

@description('Azure Search resource name')
param resourceName string

@description('Azure Search SKU name')
param azureSearchSkuName string = 'basic'

@description('AI Services account name for the project parent')
param aiServicesAccountName string = ''

@description('AI project name for creating the connection')
param aiProjectName string = ''

@description('Id of the user or app to assign application roles')
param principalId string

@description('Principal type of user or app')
param principalType string

@description('Name for the AI Foundry search connection')
param connectionName string = 'azure-ai-search-connection'

@description('Location for all resources')
param location string = resourceGroup().location

@description('Set to true to skip creating the connection if it already exists (idempotent re-runs)')
param skipConnectionCreation bool = false

@description('Set to true to skip creating role assignments that already exist (idempotent re-runs)')
param skipRoleAssignments bool = false

resource aiAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = if (!empty(aiServicesAccountName) && !empty(aiProjectName)) {
  name: aiServicesAccountName

  resource aiProject 'projects' existing = {
    name: aiProjectName
  }
}

resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: resourceName
  location: location
  tags: tags
  sku: {
    name: azureSearchSkuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
    disableLocalAuth: false
    encryptionWithCmk: {
      enforcement: 'Unspecified'
    }
    publicNetworkAccess: 'enabled'
  }
}

resource searchToAIServicesRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(aiServicesAccountName) && !skipRoleAssignments) {
  name: guid(aiServicesAccountName, searchService.id, 'Cognitive Services OpenAI User')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: searchService.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource aiServicesToSearchServiceRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(aiServicesAccountName) && !empty(aiProjectName) && !skipRoleAssignments) {
  name: guid(searchService.id, aiServicesAccountName, aiProjectName, 'Search Service Contributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalId: aiAccount::aiProject!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource aiServicesToSearchDataRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(aiServicesAccountName) && !empty(aiProjectName) && !skipRoleAssignments) {
  name: guid(searchService.id, aiServicesAccountName, aiProjectName, 'Search Index Data Contributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: aiAccount::aiProject!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource userToSearchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRoleAssignments) {
  name: guid(searchService.id, principalId, 'Search Index Data Contributor')
  scope: searchService
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: principalId
    principalType: principalType
  }
}

module aiSearchConnection '../ai/connection.bicep' = if (!empty(aiServicesAccountName) && !empty(aiProjectName)) {
  name: 'ai-search-connection-creation'
  params: {
    aiServicesAccountName: aiServicesAccountName
    aiProjectName: aiProjectName
    connectionConfig: {
      name: connectionName
      category: 'CognitiveSearch'
      target: 'https://${searchService.name}.search.windows.net'
      authType: 'AAD'
      isSharedToAll: true
      metadata: {
        ApiVersion: '2024-07-01'
        ResourceId: searchService.id
        ApiType: 'Azure'
        type: 'azure_ai_search'
      }
    }
    skipCreation: skipConnectionCreation
  }
  dependsOn: [
    aiServicesToSearchDataRoleAssignment
  ]
}

output searchServiceName string = searchService.name
output searchServiceId string = searchService.id
output searchServicePrincipalId string = searchService.identity.principalId
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
output searchAdminKey string = listAdminKeys(searchService.id, '2024-06-01-preview').primaryKey
output searchConnectionName string = (!empty(aiServicesAccountName) && !empty(aiProjectName)) ? aiSearchConnection!.outputs.connectionName : ''
output searchConnectionId string = (!empty(aiServicesAccountName) && !empty(aiProjectName)) ? aiSearchConnection!.outputs.connectionId : ''
