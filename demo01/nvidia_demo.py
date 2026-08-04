

from openai import OpenAI

client = OpenAI(
  base_url = "https://integrate.api.nvidia.com/v1",
  api_key = "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"
)


completion = client.chat.completions.create(
  model="deepseek-ai/deepseek-v4-pro",
  messages=[{"role":"user","content":"介绍一下深度搜索引擎的原理和应用"}],
  temperature=1,
  top_p=0.95,
  max_tokens=16384,
  extra_body={"chat_template_kwargs":{"thinking":False}},
  stream=False
)

print(completion.choices[0].message.content)