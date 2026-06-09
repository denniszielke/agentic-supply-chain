import os
from urllib.parse import urlparse
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Build Foundry responses endpoint from the project endpoint base
project_endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
parsed = urlparse(project_endpoint)
endpoint = f"{parsed.scheme}://{parsed.netloc}/openai/v1"

print (f"Using endpoint: {endpoint}")
deployment = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "gpt-4.1-mini")

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://ai.azure.com/.default"
)

client = OpenAI(
    base_url=endpoint,
    api_key=token_provider(),  # call the provider to get the token string
)

response = client.responses.create(
    model=deployment,
    input="What is the capital of France?",
)

print(f"answer: {response.output[0]}")
