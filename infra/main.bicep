param location string = resourceGroup().location
param environmentName string = 'agentic-supply-chain'
param searchSku string = 'basic'

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

output searchServiceName string = searchService.name
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
