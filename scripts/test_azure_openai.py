import asyncio
import os
from openai import AsyncAzureOpenAI

# REMOVED_SECRET
async def main():
    endpoint = "https://sumee-mnj0fhty-eastus2.cognitiveservices.azure.com/"
    api_key = ""
    api_version = "2025-04-01-preview"
    deployment = "gpt-5.3-chat"

    client = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )

    response = await client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Respond in valid JSON format."},
            {"role": "user", "content": "Say hello and tell me the current year. Return as JSON."},
        ],
        max_completion_tokens=500,  # Increased from 50 to allow for full response
        response_format={"type": "json_object"},
    )
    print("Full response:", response)
    print("Content:", response.choices[0].message.content)
    if response.choices[0].message.content:
        print("Response:", response.choices[0].message.content)
    else:
        print("Response is empty. Check the response object above.")

if __name__ == "__main__":
    asyncio.run(main())
