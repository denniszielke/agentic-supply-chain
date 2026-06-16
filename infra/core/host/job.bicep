// Container Apps *Job* with a cron schedule — used by the Campaign Autopilot.
//
// Mirrors core/host/app.bicep but deploys a scheduled `Microsoft.App/jobs`
// instead of an always-on container app. The job runs the autopilot once per
// cron occurrence (image CMD is `--once`) and then exits, scaling to zero in
// between. It uses the same user-assigned identity and ACR as the other
// services so it inherits the existing managed-identity / ACR-pull setup.

param name string
param location string = resourceGroup().location
param tags object = {}

param containerAppsEnvironmentName string
param containerName string = 'main'

@description('Bare registry name (e.g. "myregistry"), without .azurecr.io')
param containerRegistryName string

param imageName string

@description('User assigned identity name')
param identityName string

@description('JSON array of {name, value | secretRef} env var objects.')
param envJson string = '[]'

@description('JSON array of {name, value} secret objects exposed to the job.')
@secure()
param secretsJson string = ''

@description('Cron expression (UTC) controlling when the job runs.')
param cronExpression string = '0 6 * * 1'

@description('Maximum seconds a single run may execute before it is stopped.')
param replicaTimeout int = 1800

@description('How many times a failed run is retried.')
param replicaRetryLimit int = 1

@description('CPU cores allocated to the job container, e.g. 0.5')
param containerCpuCoreCount string = '1'

@description('Memory allocated to the job container, e.g. 2.0Gi')
param containerMemory string = '2.0Gi'

var envVars = json(envJson)
var secretsArray = empty(secretsJson) ? [] : json(secretsJson)
var acrPullRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource userIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2025-07-01' existing = {
  name: containerAppsEnvironmentName
}

// Grant the user identity ACR pull access before the job is created.
resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, userIdentity.id, acrPullRoleDefinitionId, name)
  scope: containerRegistry
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: userIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: name
  location: location
  tags: tags
  dependsOn: [ acrPullRoleAssignment ]
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${userIdentity.id}': {} }
  }
  properties: {
    environmentId: containerAppsEnvironment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: replicaTimeout
      replicaRetryLimit: replicaRetryLimit
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: userIdentity.id
        }
      ]
      secrets: secretsArray
    }
    template: {
      containers: [
        {
          image: imageName
          name: containerName
          env: envVars
          resources: {
            cpu: json(containerCpuCoreCount)
            memory: containerMemory
          }
        }
      ]
    }
  }
}

output name string = job.name
output id string = job.id
