

import requests
import json

response = requests.post(
  url="https://openrouter.ai/api/v1/chat/completions",
  headers={
    "Authorization": "Bearer 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",
  },
  data=json.dumps({
    "model": "google/gemma-4-26b-a4b-it:free",
    "messages": [
      {
        "role": "user",
        "content": "What is the meaning of life?"
      }
    ]
  })
)

print(response.status_code)
print(response.json())