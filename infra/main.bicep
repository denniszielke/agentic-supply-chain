param location string = resourceGroup().location
param environmentName string = 'agentic-supply-chain'
param searchSku string = 'basic'
param chatModelDeploymentName string = 'gpt-4.1-mini'
param embeddingModelDeploymentName string = 'text-embedding-3-small'
param openAiApiVersion string = '2024-05-01-preview'

var shoppingChatName = 'shopping-chat'
var promotionIngestionName = 'promotion-ingestion'
var shoppingAgentName = 'shopping-agent'
var containerImageBootstrap = 'mcr.microsoft.com/k8se/quickstart:latest'
var acrPullRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: '${environmentName}-search'
  location: location
  sku: {
    name: searchSku
  }
  properties: {
    publicNetworkAccess: 'enabled'
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${environmentName}-law'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${environmentName}-appi'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${replace(environmentName, '-', '')}acr'
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

resource openAi 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: '${environmentName}-aoai'
  location: location
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: '${replace(environmentName, '-', '')}aoai'
    publicNetworkAccess: 'Enabled'
  }
}

resource chatModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAi
  name: chatModelDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1-mini'
      version: '2025-04-14'
    }
    scaleSettings: {
      scaleType: 'Standard'
    }
  }
}

resource embeddingModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: openAi
  name: embeddingModelDeploymentName
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
    scaleSettings: {
      scaleType: 'Standard'
    }
  }
}

resource containerEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: '${environmentName}-cae'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: listKeys(logAnalytics.id, '2023-09-01').primarySharedKey
      }
    }
  }
}

resource shoppingChatApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: shoppingChatName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
      }
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'azure-search-admin-key'
          value: listAdminKeys(searchService.id, '2023-11-01').primaryKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: shoppingChatName
          image: containerImageBootstrap
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: 'https://${searchService.name}.search.windows.net'
            }
            {
              name: 'AZURE_SEARCH_ADMIN_KEY'
              secretRef: 'azure-search-admin-key'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: openAi.properties.endpoint
            }
            {
              name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'
              value: chatModelDeploymentName
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT_NAME'
              value: chatModelDeploymentName
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME'
              value: embeddingModelDeploymentName
            }
            {
              name: 'OPENAI_API_VERSION'
              value: openAiApiVersion
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

resource promotionIngestionApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: promotionIngestionName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnvironment.id
    configuration: {
      ingress: {
        external: false
        targetPort: 8081
        transport: 'auto'
      }
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'azure-search-admin-key'
          value: listAdminKeys(searchService.id, '2023-11-01').primaryKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: promotionIngestionName
          image: containerImageBootstrap
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: 'https://${searchService.name}.search.windows.net'
            }
            {
              name: 'AZURE_SEARCH_ADMIN_KEY'
              secretRef: 'azure-search-admin-key'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: openAi.properties.endpoint
            }
            {
              name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'
              value: chatModelDeploymentName
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT_NAME'
              value: chatModelDeploymentName
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME'
              value: embeddingModelDeploymentName
            }
            {
              name: 'OPENAI_API_VERSION'
              value: openAiApiVersion
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

resource shoppingAgentApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: shoppingAgentName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8090
        transport: 'auto'
      }
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'azure-search-admin-key'
          value: listAdminKeys(searchService.id, '2023-11-01').primaryKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: shoppingAgentName
          image: containerImageBootstrap
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: 'https://${searchService.name}.search.windows.net'
            }
            {
              name: 'AZURE_SEARCH_ADMIN_KEY'
              secretRef: 'azure-search-admin-key'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: openAi.properties.endpoint
            }
            {
              name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'
              value: chatModelDeploymentName
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT_NAME'
              value: chatModelDeploymentName
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME'
              value: embeddingModelDeploymentName
            }
            {
              name: 'OPENAI_API_VERSION'
              value: openAiApiVersion
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

resource shoppingChatAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, shoppingChatApp.name, acrPullRoleDefinitionId)
  scope: containerRegistry
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: shoppingChatApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource promotionIngestionAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, promotionIngestionApp.name, acrPullRoleDefinitionId)
  scope: containerRegistry
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: promotionIngestionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource shoppingAgentAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, shoppingAgentApp.name, acrPullRoleDefinitionId)
  scope: containerRegistry
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: shoppingAgentApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output searchServiceName string = searchService.name
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
output AZURE_SEARCH_ENDPOINT string = 'https://${searchService.name}.search.windows.net'
output AZURE_SEARCH_ADMIN_KEY string = listAdminKeys(searchService.id, '2023-11-01').primaryKey
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.properties.loginServer
output AZURE_REGISTRY string = containerRegistry.properties.loginServer
output AZURE_CONTAINER_APPS_ENVIRONMENT_ID string = containerEnvironment.id
output AZURE_CONTAINER_APPS_ENVIRONMENT_NAME string = containerEnvironment.name
output AZURE_OPENAI_ENDPOINT string = openAi.properties.endpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = chatModelDeploymentName
output AZURE_OPENAI_CHAT_DEPLOYMENT_NAME string = chatModelDeploymentName
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME string = embeddingModelDeploymentName
output OPENAI_API_VERSION string = openAiApiVersion
output SHOPPING_CHAT_CONTAINER_APP_NAME string = shoppingChatApp.name
output SHOPPING_AGENT_CONTAINER_APP_NAME string = shoppingAgentApp.name
output PROMOTION_INGESTION_CONTAINER_APP_NAME string = promotionIngestionApp.name
output SHOPPING_CHAT_URL string = 'https://${shoppingChatApp.properties.configuration.ingress.fqdn}'
output SHOPPING_AGENT_URL string = 'https://${shoppingAgentApp.properties.configuration.ingress.fqdn}'
