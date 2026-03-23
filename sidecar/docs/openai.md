LiteLLM supports OpenAI Chat + Embedding calls.

### Required API Keys

    import os 
    os.environ["OPENAI_API_KEY"] = "your-api-key"

### Usage

    import os 
    from litellm import completion
    
    os.environ["OPENAI_API_KEY"] = "your-api-key"
    
    # openai call
    response = completion(
        model = "gpt-4o", 
        messages=[{ "content": "Hello, how are you?","role": "user"}]
    )

### Usage - LiteLLM Proxy Server

Here's how to call OpenAI models with the LiteLLM Proxy Server

### 1\. Save key in your environment

    export OPENAI_API_KEY=""

### 2\. Start the proxy

### 3\. Test it

### Optional Keys - OpenAI Organization, OpenAI API Base

    import os 
    os.environ["OPENAI_ORGANIZATION"] = "your-org-id"       # OPTIONAL
    os.environ["OPENAI_BASE_URL"] = "https://your_host/v1"     # OPTIONAL

### OpenAI Chat Completion Models

Model Name

Function Call

gpt-5

`response = completion(model="gpt-5", messages=messages)`

gpt-5-mini

`response = completion(model="gpt-5-mini", messages=messages)`

gpt-5-nano

`response = completion(model="gpt-5-nano", messages=messages)`

gpt-5-chat

`response = completion(model="gpt-5-chat", messages=messages)`

gpt-5-chat-latest

`response = completion(model="gpt-5-chat-latest", messages=messages)`

gpt-5-2025-08-07

`response = completion(model="gpt-5-2025-08-07", messages=messages)`

gpt-5-mini-2025-08-07

`response = completion(model="gpt-5-mini-2025-08-07", messages=messages)`

gpt-5-nano-2025-08-07

`response = completion(model="gpt-5-nano-2025-08-07", messages=messages)`

gpt-5-pro

`response = completion(model="gpt-5-pro", messages=messages)`

gpt-5.2

`response = completion(model="gpt-5.2", messages=messages)`

gpt-5.2-2025-12-11

`response = completion(model="gpt-5.2-2025-12-11", messages=messages)`

gpt-5.2-chat-latest

`response = completion(model="gpt-5.2-chat-latest", messages=messages)`

gpt-5.3-chat-latest

`response = completion(model="gpt-5.3-chat-latest", messages=messages)`

gpt-5.4

`response = completion(model="gpt-5.4", messages=messages)`

gpt-5.4-2026-03-05

`response = completion(model="gpt-5.4-2026-03-05", messages=messages)`

gpt-5.2-pro

`response = completion(model="gpt-5.2-pro", messages=messages)`

gpt-5.2-pro-2025-12-11

`response = completion(model="gpt-5.2-pro-2025-12-11", messages=messages)`

gpt-5.4-pro

`response = completion(model="gpt-5.4-pro", messages=messages)`

gpt-5.4-pro-2026-03-05

`response = completion(model="gpt-5.4-pro-2026-03-05", messages=messages)`

gpt-5.1

`response = completion(model="gpt-5.1", messages=messages)`

gpt-5.1-codex

`response = completion(model="gpt-5.1-codex", messages=messages)`

gpt-5.1-codex-mini

`response = completion(model="gpt-5.1-codex-mini", messages=messages)`

gpt-5.1-codex-max

`response = completion(model="gpt-5.1-codex-max", messages=messages)`

gpt-4.1

`response = completion(model="gpt-4.1", messages=messages)`

gpt-4.1-mini

`response = completion(model="gpt-4.1-mini", messages=messages)`

gpt-4.1-nano

`response = completion(model="gpt-4.1-nano", messages=messages)`

o4-mini

`response = completion(model="o4-mini", messages=messages)`

o3-mini

`response = completion(model="o3-mini", messages=messages)`

o3

`response = completion(model="o3", messages=messages)`

o1-mini

`response = completion(model="o1-mini", messages=messages)`

o1-preview

`response = completion(model="o1-preview", messages=messages)`

gpt-4o-mini

`response = completion(model="gpt-4o-mini", messages=messages)`

gpt-4o-mini-2024-07-18

`response = completion(model="gpt-4o-mini-2024-07-18", messages=messages)`

gpt-4o

`response = completion(model="gpt-4o", messages=messages)`

gpt-4o-2024-08-06

`response = completion(model="gpt-4o-2024-08-06", messages=messages)`

gpt-4o-2024-05-13

`response = completion(model="gpt-4o-2024-05-13", messages=messages)`

gpt-4-turbo

`response = completion(model="gpt-4-turbo", messages=messages)`

gpt-4-turbo-preview

`response = completion(model="gpt-4-0125-preview", messages=messages)`

gpt-4-0125-preview

`response = completion(model="gpt-4-0125-preview", messages=messages)`

gpt-4-1106-preview

`response = completion(model="gpt-4-1106-preview", messages=messages)`

gpt-3.5-turbo-1106

`response = completion(model="gpt-3.5-turbo-1106", messages=messages)`

gpt-3.5-turbo

`response = completion(model="gpt-3.5-turbo", messages=messages)`

gpt-3.5-turbo-0301

`response = completion(model="gpt-3.5-turbo-0301", messages=messages)`

gpt-3.5-turbo-0613

`response = completion(model="gpt-3.5-turbo-0613", messages=messages)`

gpt-3.5-turbo-16k

`response = completion(model="gpt-3.5-turbo-16k", messages=messages)`

gpt-3.5-turbo-16k-0613

`response = completion(model="gpt-3.5-turbo-16k-0613", messages=messages)`

gpt-4

`response = completion(model="gpt-4", messages=messages)`

gpt-4-0314

`response = completion(model="gpt-4-0314", messages=messages)`

gpt-4-0613

`response = completion(model="gpt-4-0613", messages=messages)`

gpt-4-32k

`response = completion(model="gpt-4-32k", messages=messages)`

gpt-4-32k-0314

`response = completion(model="gpt-4-32k-0314", messages=messages)`

gpt-4-32k-0613

`response = completion(model="gpt-4-32k-0613", messages=messages)`

These also support the `OPENAI_BASE_URL` environment variable, which can be used to specify a custom API endpoint.

OpenAI has two ways to use web search, depending on the endpoint:

Approach

Endpoint

Models

How to enable

**Search Models**

`/chat/completions`

`gpt-5-search-api`, `gpt-4o-search-preview`, `gpt-4o-mini-search-preview`

Pass `web_search_options` parameter

**Web Search Tool**

`/responses`

`gpt-5`, `gpt-4.1`, `gpt-4o`, and other regular models

Pass `web_search_preview` tool

For full details, see the [Web Search guide](/docs/completion/web_search).

OpenAI Vision Models
--------------------

Model Name

Function Call

gpt-4o

`response = completion(model="gpt-4o", messages=messages)`

gpt-4-turbo

`response = completion(model="gpt-4-turbo", messages=messages)`

gpt-4-vision-preview

`response = completion(model="gpt-4-vision-preview", messages=messages)`

#### Usage

    import os 
    from litellm import completion
    
    os.environ["OPENAI_API_KEY"] = "your-api-key"
    
    # openai call
    response = completion(
        model = "gpt-4-vision-preview", 
        messages=[
            {
                "role": "user",
                "content": [
                                {
                                    "type": "text",
                                    "text": "What’s in this image?"
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                    "url": "https://awsmp-logos.s3.amazonaws.com/seller-xw5kijmvmzasy/c233c9ade2ccb5491072ae232c814942.png"
                                    }
                                }
                            ]
            }
        ],
    )

PDF File Parsing
----------------

OpenAI has a new `file` message type that allows you to pass in a PDF file and have it parsed into a structured output. [Read more](https://platform.openai.com/docs/guides/pdf-files?api-mode=chat&lang=python)

OpenAI Fine Tuned Models
------------------------

Model Name

Function Call

fine tuned `gpt-4-0613`

`response = completion(model="ft:gpt-4-0613", messages=messages)`

fine tuned `gpt-4o-2024-05-13`

`response = completion(model="ft:gpt-4o-2024-05-13", messages=messages)`

fine tuned `gpt-3.5-turbo-0125`

`response = completion(model="ft:gpt-3.5-turbo-0125", messages=messages)`

fine tuned `gpt-3.5-turbo-1106`

`response = completion(model="ft:gpt-3.5-turbo-1106", messages=messages)`

fine tuned `gpt-3.5-turbo-0613`

`response = completion(model="ft:gpt-3.5-turbo-0613", messages=messages)`

Getting Reasoning Content in /chat/completions
----------------------------------------------

GPT-5 models return reasoning content when called via the Responses API. You can call these models via the `/chat/completions` endpoint by using the `openai/responses/` prefix.

Expected Response:

    {
      "id": "chatcmpl-6382a222-43c9-40c4-856b-22e105d88075",
      "created": 1760146746,
      "model": "gpt-5-mini",
      "object": "chat.completion",
      "system_fingerprint": null,
      "choices": [
        {
          "finish_reason": "stop",
          "index": 0,
          "message": {
            "content": "Paris",
            "role": "assistant",
            "tool_calls": null,
            "function_call": null,
            "reasoning_content": "**Identifying the capital**\n\nThe user wants me to think of the capital of France and write it down. That's pretty straightforward: it's Paris. There aren't any safety issues to consider here. I think it would be best to keep it concise, so maybe just \"Paris\" would suffice. I feel confident that I should just stick to that without adding anything else. So, let's write it down!",
            "provider_specific_fields": null
          }
        }
      ],
      "usage": {
        "completion_tokens": 7,
        "prompt_tokens": 18,
        "total_tokens": 25,
        "completion_tokens_details": null,
        "prompt_tokens_details": {
          "audio_tokens": null,
          "cached_tokens": 0,
          "text_tokens": null,
          "image_tokens": null
        }
      }
    }

### Advanced: Using reasoning\_effort with summary field

By default, `reasoning_effort` accepts a string value (`"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`—`"xhigh"` is only supported on `gpt-5.1-codex-max` and `gpt-5.2` models) and only sets the effort level without including a reasoning summary.

To opt-in to the `summary` feature, you can pass `reasoning_effort` as a dictionary. **Note:** The `summary` field requires your OpenAI organization to have verification status. Using `summary` without verification will result in a 400 error from OpenAI.

**Summary field options:**

*   `"auto"`: System automatically determines the appropriate summary level based on the model
*   `"concise"`: Provides a shorter summary (not supported by GPT-5 series models)
*   `"detailed"`: Offers a comprehensive reasoning summary

**Note:** GPT-5 series models support `"auto"` and `"detailed"`, but do not support `"concise"`. O-series models (o3-pro, o4-mini, o3) support all three options. Some models like o3-mini and o1 do not support reasoning summaries at all.

**Supported `reasoning_effort` values by model:**

Model

Default (when not set)

Supported Values

`gpt-5.1`

`none`

`none`, `low`, `medium`, `high`

`gpt-5`

`medium`

`minimal`, `low`, `medium`, `high`

`gpt-5-mini`

`medium`

`minimal`, `low`, `medium`, `high`

`gpt-5-nano`

`none`

`none`, `low`, `medium`, `high`

`gpt-5-codex`

`adaptive`

`low`, `medium`, `high` (no `minimal`)

`gpt-5.1-codex`

`adaptive`

`low`, `medium`, `high` (no `minimal`)

`gpt-5.1-codex-mini`

`adaptive`

`low`, `medium`, `high` (no `minimal`)

`gpt-5.1-codex-max`

`adaptive`

`low`, `medium`, `high`, `xhigh` (no `minimal`)

`gpt-5.2`

`medium`

`none`, `low`, `medium`, `high`, `xhigh`

`gpt-5.2-pro`

`high`

`low`, `medium`, `high`, `xhigh`

`gpt-5-pro`

`high`

`high` only

**Note:**

*   GPT-5.1 introduced a new `reasoning_effort="none"` setting for faster, lower-latency responses. This replaces the `"minimal"` setting from GPT-5.
*   `gpt-5.1-codex-max` and `gpt-5.2` models support `reasoning_effort="xhigh"`. All other models will reject this value.
*   `gpt-5-pro` only accepts `reasoning_effort="high"`. Other values will return an error.
*   When `reasoning_effort` is not set (None), OpenAI defaults to the value shown in the "Default" column.

See [OpenAI Reasoning documentation](https://platform.openai.com/docs/guides/reasoning) for more details on organization verification requirements.

### Verbosity Control for GPT-5 Models

The `verbosity` parameter controls the length and detail of responses from GPT-5 family models. It accepts three values: `"low"`, `"medium"`, or `"high"`.

**Supported models:** `gpt-5`, `gpt-5.1`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5-pro`

**Note:** GPT-5-Codex models (`gpt-5-codex`, `gpt-5.1-codex`, `gpt-5.1-codex-mini`, `gpt-5.1-codex-max`) do **not** support the `verbosity` parameter.

**Use cases:**

*   **`"low"`**: Best for concise answers or simple code generation (e.g., SQL queries)
*   **`"medium"`**: Default - balanced output length
*   **`"high"`**: Use when you need thorough explanations or extensive code refactoring

OpenAI Chat Completion to Responses API Bridge
----------------------------------------------

LiteLLM offers a chat completion to Responses API bridge. This lets you use the completion interface while calling the Responses API under the hood.

This is useful when you want to use [Responses API](https://platform.openai.com/docs/api-reference/responses) specific features (like built-in tools, web search preview, or code interpreter).

### When to use the openai/responses/ prefix

Each model has a `mode` property defined in [`model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) that determines which API endpoint it uses by default:

*   **`mode: responses`** - Model automatically uses the Responses API
*   **`mode: chat`** - Model defaults to the Chat Completions API

**Models with `mode: responses`** (automatic Responses API):

*   `o3-deep-research`, `o4-mini-deep-research`
*   `o1-pro`, `o3-pro`
*   `gpt-5.1-codex`, `gpt-5.1-codex-mini`, `gpt-5.1-codex-max`
*   `codex-mini-latest`

**Models with `mode: chat`** (require `openai/responses/` prefix for built-in tools):

*   `gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `gpt-4.1-mini`
*   `gpt-5`, `gpt-5-mini`
*   `o3`, `o4-mini`

To use built-in tools like `web_search_preview` with `mode: chat` models, add the `openai/responses/` prefix:

### Examples

OpenAI Audio Transcription
--------------------------

LiteLLM supports OpenAI Audio Transcription endpoint.

Supported models:

Model Name

Function Call

`whisper-1`

`response = completion(model="whisper-1", file=audio_file)`

`gpt-4o-transcribe`

`response = completion(model="gpt-4o-transcribe", file=audio_file)`

`gpt-4o-mini-transcribe`

`response = completion(model="gpt-4o-mini-transcribe", file=audio_file)`

Advanced
--------

### Getting OpenAI API Response Headers

Set `litellm.return_response_headers = True` to get raw response headers from OpenAI

You can expect to always get the `_response_headers` field from `litellm.completion()`, `litellm.embedding()` functions

Expected Response Headers from OpenAI

### Parallel Function calling

See a detailed walthrough of parallel function calling with litellm [here](https://docs.litellm.ai/docs/completion/function_call)

    import litellm
    import json
    # set openai api key
    import os
    os.environ['OPENAI_API_KEY'] = "" # litellm reads OPENAI_API_KEY from .env and sends the request
    # Example dummy function hard coded to return the same weather
    # In production, this could be your backend API or an external API
    def get_current_weather(location, unit="fahrenheit"):
        """Get the current weather in a given location"""
        if "tokyo" in location.lower():
            return json.dumps({"location": "Tokyo", "temperature": "10", "unit": "celsius"})
        elif "san francisco" in location.lower():
            return json.dumps({"location": "San Francisco", "temperature": "72", "unit": "fahrenheit"})
        elif "paris" in location.lower():
            return json.dumps({"location": "Paris", "temperature": "22", "unit": "celsius"})
        else:
            return json.dumps({"location": location, "temperature": "unknown"})
    
    messages = [{"role": "user", "content": "What's the weather like in San Francisco, Tokyo, and Paris?"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_current_weather",
                "description": "Get the current weather in a given location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city and state, e.g. San Francisco, CA",
                        },
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["location"],
                },
            },
        }
    ]
    
    response = litellm.completion(
        model="gpt-3.5-turbo-1106",
        messages=messages,
        tools=tools,
        tool_choice="auto",  # auto is default, but we'll be explicit
    )
    print("\nLLM Response1:\n", response)
    response_message = response.choices[0].message
    tool_calls = response.choices[0].message.tool_calls

### Setting extra\_headers for completion calls

### Setting Organization-ID for completion calls

This can be set in one of the following ways:

*   Environment Variable `OPENAI_ORGANIZATION`
*   Params to `litellm.completion(model=model, organization="your-organization-id")`
*   Set as `litellm.organization="your-organization-id"`

    import os 
    from litellm import completion
    
    os.environ["OPENAI_API_KEY"] = "your-api-key"
    os.environ["OPENAI_ORGANIZATION"] = "your-org-id" # OPTIONAL
    
    response = completion(
        model = "gpt-3.5-turbo", 
        messages=[{ "content": "Hello, how are you?","role": "user"}]
    )

### Set ssl\_verify=False

This is done by setting your own `httpx.Client`

*   For `litellm.completion` set `litellm.client_session=httpx.Client(verify=False)`
*   For `litellm.acompletion` set `litellm.aclient_session=AsyncClient.Client(verify=False)`

    import litellm, httpx
    
    # for completion
    litellm.client_session = httpx.Client(verify=False)
    response = litellm.completion(
        model="gpt-3.5-turbo",
        messages=messages,
    )
    
    # for acompletion
    litellm.aclient_session = httpx.AsyncClient(verify=False)
    response = litellm.acompletion(
        model="gpt-3.5-turbo",
        messages=messages,
    )

### Using OpenAI Proxy with LiteLLM

    import os 
    import litellm
    from litellm import completion
    
    os.environ["OPENAI_API_KEY"] = ""
    
    # set custom api base to your proxy
    # either set .env or litellm.api_base
    # os.environ["OPENAI_BASE_URL"] = "https://your_host/v1"
    litellm.api_base = "https://your_host/v1"
    
    messages = [{ "content": "Hello, how are you?","role": "user"}]
    
    # openai call
    response = completion("openai/your-model-name", messages)

If you need to set api\_base dynamically, just pass it in completions instead - `completions(...,api_base="your-proxy-api-base")`

For more check out [setting API Base/Keys](/docs/set_keys)

### Forwarding Org ID for Proxy requests

Forward openai Org ID's from the client to OpenAI with `forward_openai_org_id` param.

1.  Setup config.yaml

    model_list:
      - model_name: "gpt-3.5-turbo"
        litellm_params:
          model: gpt-3.5-turbo
          api_key: os.environ/OPENAI_API_KEY
    
    general_settings:
        forward_openai_org_id: true # 👈 KEY CHANGE

1.  Start Proxy

    litellm --config config.yaml --detailed_debug
    
    # RUNNING on http://0.0.0.0:4000

1.  Make OpenAI call

    from openai import OpenAI
    client = OpenAI(
        api_key="sk-1234",
        organization="my-special-org",
        base_url="http://0.0.0.0:4000"
    )
    
    client.chat.completions.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": "Hello world"}])

In your logs you should see the forwarded org id

    LiteLLM:DEBUG: utils.py:255 - Request to litellm:
    LiteLLM:DEBUG: utils.py:255 - litellm.acompletion(... organization='my-special-org',)

GPT-5 Pro Special Notes
-----------------------

GPT-5 Pro is OpenAI's most advanced reasoning model with unique characteristics:

*   **Responses API Only**: GPT-5 Pro is only available through the `/v1/responses` endpoint
*   **No Streaming**: Does not support streaming responses
*   **High Reasoning**: Designed for complex reasoning tasks with highest effort reasoning
*   **Context Window**: 400,000 tokens input, 272,000 tokens output
*   **Pricing**: $15.00 input / $120.00 output per 1M tokens (Standard), $7.50 input / $60.00 output (Batch)
*   **Tools**: Supports Web Search, File Search, Image Generation, MCP (but not Code Interpreter or Computer Use)
*   **Modalities**: Text and Image input, Text output only

    # GPT-5 Pro usage example
    response = completion(
        model="gpt-5-pro", 
        messages=[{"role": "user", "content": "Solve this complex reasoning problem..."}]
    )

Video Generation
----------------

LiteLLM supports OpenAI's video generation models including Sora.

For detailed documentation on video generation, see [OpenAI Video Generation →](/docs/providers/openai/videos)

🚅

LiteLLM Enterprise

SSO/SAML, audit logs, spend tracking, multi-team management, and guardrails — built for production.

[Learn more →](/docs/enterprise)