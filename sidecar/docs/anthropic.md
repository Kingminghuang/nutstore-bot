LiteLLM supports all anthropic models.

*   `claude-opus-4-6` (`claude-opus-4-6-20260205`)
*   `claude-sonnet-4-6`
*   `claude-sonnet-4-5-20250929`
*   `claude-opus-4-5-20251101`
*   `claude-opus-4-1-20250805`
*   `claude-4` (`claude-opus-4-20250514`, `claude-sonnet-4-20250514`)
*   `claude-3.7` (`claude-3-7-sonnet-20250219`)
*   `claude-3.5` (`claude-3-5-sonnet-20240620`)
*   `claude-3` (`claude-3-haiku-20240307`, `claude-3-opus-20240229`, `claude-3-sonnet-20240229`)
*   `claude-2`
*   `claude-2.1`
*   `claude-instant-1.2`

Property

Details

Description

Claude is a highly performant, trustworthy, and intelligent AI platform built by Anthropic. Claude excels at tasks involving language, reasoning, analysis, coding, and more. Also available via Azure Foundry.

Provider Route on LiteLLM

`anthropic/` (add this prefix to the model name, to route any requests to Anthropic - e.g. `anthropic/claude-3-5-sonnet-20240620`). For Azure Foundry deployments, use `azure/claude-*` (see [Azure Anthropic documentation](/docs/providers/azure/azure_anthropic))

Provider Doc

[Anthropic ↗](https://docs.anthropic.com/en/docs/build-with-claude/overview), [Azure Foundry Claude ↗](https://learn.microsoft.com/en-us/azure/ai-services/foundry-models/claude)

API Endpoint for Provider

[https://api.anthropic.com](https://api.anthropic.com) (or Azure Foundry endpoint: `https://<resource-name>.services.ai.azure.com/anthropic`)

Supported Endpoints

`/chat/completions`, `/v1/messages` (passthrough)

Supported OpenAI Parameters
---------------------------

Check this in code, [here](/docs/completion/input#translated-openai-params)

    "stream",
    "stop",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "tools",
    "tool_choice",
    "extra_headers",
    "parallel_tool_calls",
    "response_format",
    "user",
    "reasoning_effort",

Structured Outputs
------------------

LiteLLM supports Anthropic's [structured outputs feature](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) for Claude Sonnet 4.5 and Opus 4.1 models. When you use `response_format` with these models, LiteLLM automatically:

*   Adds the required `structured-outputs-2025-11-13` beta header
*   Transforms OpenAI's `response_format` to Anthropic's `output_format` format

### Supported Models

*   `sonnet-4-5` or `sonnet-4.5` (all Sonnet 4.5 variants)
*   `opus-4-1` or `opus-4.1` (all Opus 4.1 variants)
    *   `opus-4-5` or `opus-4.5` (all Opus 4.5 variants)

### Example Usage

API Keys
--------

    import os
    
    os.environ["ANTHROPIC_API_KEY"] = "your-api-key"
    # os.environ["ANTHROPIC_API_BASE"] = "" # [OPTIONAL] or 'ANTHROPIC_BASE_URL'
    # os.environ["LITELLM_ANTHROPIC_DISABLE_URL_SUFFIX"] = "true" # [OPTIONAL] Disable automatic URL suffix appending

### Custom API Base

When using a custom API base for Anthropic (e.g., a proxy or custom endpoint), LiteLLM automatically appends the appropriate suffix (`/v1/messages` or `/v1/complete`) to your base URL.

If your custom endpoint already includes the full path or doesn't follow Anthropic's standard URL structure, you can disable this automatic suffix appending:

    import os
    
    os.environ["ANTHROPIC_API_BASE"] = "https://my-custom-endpoint.com/custom/path"
    os.environ["LITELLM_ANTHROPIC_DISABLE_URL_SUFFIX"] = "true"  # Prevents automatic suffix

Without `LITELLM_ANTHROPIC_DISABLE_URL_SUFFIX`:

*   Base URL `https://my-proxy.com` → `https://my-proxy.com/v1/messages`
*   Base URL `https://my-proxy.com/api` → `https://my-proxy.com/api/v1/messages`

With `LITELLM_ANTHROPIC_DISABLE_URL_SUFFIX=true`:

*   Base URL `https://my-proxy.com/custom/path` → `https://my-proxy.com/custom/path` (unchanged)

### Azure AI Foundry (Alternative Method)

As an alternative, you can use the `anthropic/` provider directly with your Azure endpoint since Azure exposes Claude using Anthropic's native API.

    from litellm import completion
    
    response = completion(
        model="anthropic/claude-sonnet-4-5",
        api_base="https://<your-resource>.services.ai.azure.com/anthropic",
        api_key="<your-azure-api-key>",
        messages=[{"role": "user", "content": "Hello!"}],
    )
    print(response)

Usage
-----

    import os
    from litellm import completion
    
    # set env - [OPTIONAL] replace with your anthropic key
    os.environ["ANTHROPIC_API_KEY"] = "your-api-key"
    
    messages = [{"role": "user", "content": "Hey! how's it going?"}]
    response = completion(model="claude-opus-4-20250514", messages=messages)
    print(response)

Usage - Streaming
-----------------

Just set `stream=True` when calling completion.

    import os
    from litellm import completion
    
    # set env
    os.environ["ANTHROPIC_API_KEY"] = "your-api-key"
    
    messages = [{"role": "user", "content": "Hey! how's it going?"}]
    response = completion(model="claude-opus-4-20250514", messages=messages, stream=True)
    for chunk in response:
        print(chunk["choices"][0]["delta"]["content"])  # same as openai format

Usage with LiteLLM Proxy
------------------------

Here's how to call Anthropic with the LiteLLM Proxy Server

### 1\. Save key in your environment

    export ANTHROPIC_API_KEY="your-api-key"

### 2\. Start the proxy

### 3\. Test it

Supported Models
----------------

`Model Name` 👉 Human-friendly name.  
`Function Call` 👉 How to call the model in LiteLLM.

Model Name

Function Call

claude-opus-4-6

`completion('claude-opus-4-6-20260205', messages)`

claude-sonnet-4-5

`completion('claude-sonnet-4-5-20250929', messages)`

claude-opus-4-5

`completion('claude-opus-4-5-20251101', messages)`

claude-opus-4-1

`completion('claude-opus-4-1-20250805', messages)`

claude-opus-4

`completion('claude-opus-4-20250514', messages)`

claude-sonnet-4

`completion('claude-sonnet-4-20250514', messages)`

claude-3.7

`completion('claude-3-7-sonnet-20250219', messages)`

claude-3-5-sonnet

`completion('claude-3-5-sonnet-20240620', messages)`

claude-3-haiku

`completion('claude-3-haiku-20240307', messages)`

claude-3-opus

`completion('claude-3-opus-20240229', messages)`

claude-3-5-sonnet-20240620

`completion('claude-3-5-sonnet-20240620', messages)`

claude-3-sonnet

`completion('claude-3-sonnet-20240229', messages)`

claude-2.1

`completion('claude-2.1', messages)`

claude-2

`completion('claude-2', messages)`

claude-instant-1.2

`completion('claude-instant-1.2', messages)`

claude-instant-1

`completion('claude-instant-1', messages)`

Prompt Caching
--------------

Use Anthropic Prompt Caching

[Relevant Anthropic API Docs](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)

### Caching - Large Context Caching

This example demonstrates basic Prompt Caching usage, caching the full text of the legal agreement as a prefix while keeping the user instruction uncached.

### Caching - Tools definitions

In this example, we demonstrate caching tool definitions.

The cache\_control parameter is placed on the final tool

### Caching - Continuing Multi-Turn Convo

In this example, we demonstrate how to use Prompt Caching in a multi-turn conversation.

The cache\_control parameter is placed on the system message to designate it as part of the static prefix.

The conversation history (previous messages) is included in the messages array. The final turn is marked with cache-control, for continuing in followups. The second-to-last user message is marked for caching with the cache\_control parameter, so that this checkpoint can read from the previous cache.

Function/Tool Calling
---------------------

    from litellm import completion
    
    # set env
    os.environ["ANTHROPIC_API_KEY"] = "your-api-key"
    
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
    messages = [{"role": "user", "content": "What's the weather like in Boston today?"}]
    
    response = completion(
        model="anthropic/claude-3-opus-20240229",
        messages=messages,
        tools=tools,
        tool_choice="auto",
    )
    # Add any assertions, here to check response args
    print(response)
    assert isinstance(response.choices[0].message.tool_calls[0].function.name, str)
    assert isinstance(
        response.choices[0].message.tool_calls[0].function.arguments, str
    )

### Forcing Anthropic Tool Use

If you want Claude to use a specific tool to answer the user’s question

You can do this by specifying the tool in the `tool_choice` field like so:

    response = completion(
        model="anthropic/claude-3-opus-20240229",
        messages=messages,
        tools=tools,
        tool_choice={"type": "tool", "name": "get_weather"},
    )

### Disable Tool Calling

You can disable tool calling by setting the `tool_choice` to `"none"`.

### MCP Tool Calling

Here's how to use MCP tool calling with Anthropic:

### Parallel Function Calling

Here's how to pass the result of a function call back to an anthropic model:

    from litellm import completion
    import os 
    
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant.."
    
    litellm.set_verbose = True
    
    ### 1ST FUNCTION CALL ###
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
    messages = [
        {
            "role": "user",
            "content": "What's the weather like in Boston today in Fahrenheit?",
        }
    ]
    try:
        # test without max tokens
        response = completion(
            model="anthropic/claude-3-opus-20240229",
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        # Add any assertions, here to check response args
        print(response)
        assert isinstance(response.choices[0].message.tool_calls[0].function.name, str)
        assert isinstance(
            response.choices[0].message.tool_calls[0].function.arguments, str
        )
    
        messages.append(
            response.choices[0].message.model_dump()
        )  # Add assistant tool invokes
        tool_result = (
            '{"location": "Boston", "temperature": "72", "unit": "fahrenheit"}'
        )
        # Add user submitted tool results in the OpenAI format
        messages.append(
            {
                "tool_call_id": response.choices[0].message.tool_calls[0].id,
                "role": "tool",
                "name": response.choices[0].message.tool_calls[0].function.name,
                "content": tool_result,
            }
        )
        ### 2ND FUNCTION CALL ###
        # In the second response, Claude should deduce answer from tool results
        second_response = completion(
            model="anthropic/claude-3-opus-20240229",
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        print(second_response)
    except Exception as e:
        print(f"An error occurred - {str(e)}")

s/o @[Shekhar Patnaik](https://www.linkedin.com/in/patnaikshekhar) for requesting this!

### Context Management (Beta)

Anthropic’s [context editing](https://docs.claude.com/en/docs/build-with-claude/context-editing) API lets you automatically clear older tool results or thinking blocks. LiteLLM now forwards the native `context_management` payload when you call Anthropic models, and automatically attaches the required `context-management-2025-06-27` beta header.

    from litellm import completion
    
    response = completion(
        model="anthropic/claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "Summarize the latest tool results"}],
        context_management={
            "edits": [
                {
                    "type": "clear_tool_uses_20250919",
                    "trigger": {"type": "input_tokens", "value": 30000},
                    "keep": {"type": "tool_uses", "value": 3},
                    "clear_at_least": {"type": "input_tokens", "value": 5000},
                    "exclude_tools": ["web_search"],
                }
            ]
        },
    )

Usage - Vision
--------------

    from litellm import completion
    
    # set env
    os.environ["ANTHROPIC_API_KEY"] = "your-api-key"
    
    def encode_image(image_path):
        import base64
    
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    
    image_path = "../proxy/cached_logo.jpg"
    # Getting the base64 string
    base64_image = encode_image(image_path)
    resp = litellm.completion(
        model="anthropic/claude-3-opus-20240229",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Whats in this image?"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + base64_image
                        },
                    },
                ],
            }
        ],
    )
    print(f"\nResponse: {resp}")

Usage - Thinking / reasoning\_content
-------------------------------------

LiteLLM translates OpenAI's `reasoning_effort` to Anthropic's `thinking` parameter. [Code](https://github.com/BerriAI/litellm/blob/23051d89dd3611a81617d84277059cd88b2df511/litellm/llms/anthropic/chat/transformation.py#L298)

reasoning\_effort

thinking

"low"

"budget\_tokens": 1024

"medium"

"budget\_tokens": 2048

"high"

"budget\_tokens": 4096

**Expected Response**

    ModelResponse(
        id='chatcmpl-c542d76d-f675-4e87-8e5f-05855f5d0f5e',
        created=1740470510,
        model='claude-3-7-sonnet-20250219',
        object='chat.completion',
        system_fingerprint=None,
        choices=[
            Choices(
                finish_reason='stop',
                index=0,
                message=Message(
                    content="The capital of France is Paris.",
                    role='assistant',
                    tool_calls=None,
                    function_call=None,
                    provider_specific_fields={
                        'citations': None,
                        'thinking_blocks': [
                            {
                                'type': 'thinking',
                                'thinking': 'The capital of France is Paris. This is a very straightforward factual question.',
                                'signature': 'EuYBCkQYAiJAy6...'
                            }
                        ]
                    }
                ),
                thinking_blocks=[
                    {
                        'type': 'thinking',
                        'thinking': 'The capital of France is Paris. This is a very straightforward factual question.',
                        'signature': 'EuYBCkQYAiJAy6AGB...'
                    }
                ],
                reasoning_content='The capital of France is Paris. This is a very straightforward factual question.'
            )
        ],
        usage=Usage(
            completion_tokens=68,
            prompt_tokens=42,
            total_tokens=110,
            completion_tokens_details=None,
            prompt_tokens_details=PromptTokensDetailsWrapper(
                audio_tokens=None,
                cached_tokens=0,
                text_tokens=None,
                image_tokens=None
            ),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0
        )
    )

### Pass thinking to Anthropic models

You can also pass the `thinking` parameter to Anthropic models.

You can also pass the `thinking` parameter to Anthropic models.

#### Adaptive Thinking (Claude Opus 4.6)

#### Enabled Thinking with Budget

Passing Extra Headers to Anthropic API
--------------------------------------

Pass `extra_headers: dict` to `litellm.completion`

Usage - "Assistant Pre-fill"
----------------------------

You can "put words in Claude's mouth" by including an `assistant` role message as the last item in the `messages` array.

> \[!IMPORTANT\] The returned completion will _not_ include your "pre-fill" text, since it is part of the prompt itself. Make sure to prefix Claude's completion with your pre-fill.

    import os
    from litellm import completion
    
    # set env - [OPTIONAL] replace with your anthropic key
    os.environ["ANTHROPIC_API_KEY"] = "your-api-key"
    
    messages = [
        {"role": "user", "content": "How do you say 'Hello' in German? Return your answer as a JSON object, like this:\n\n{ \"Hello\": \"Hallo\" }"},
        {"role": "assistant", "content": "{"},
    ]
    response = completion(model="claude-2.1", messages=messages)
    print(response)

#### Example prompt sent to Claude

    Human: How do you say 'Hello' in German? Return your answer as a JSON object, like this:
    
    { "Hello": "Hallo" }
    
    Assistant: {

Usage - "System" messages
-------------------------

If you're using Anthropic's Claude 2.1, `system` role messages are properly formatted for you.

    import os
    from litellm import completion
    
    # set env - [OPTIONAL] replace with your anthropic key
    os.environ["ANTHROPIC_API_KEY"] = "your-api-key"
    
    messages = [
        {"role": "system", "content": "You are a snarky assistant."},
        {"role": "user", "content": "How do I boil water?"},
    ]
    response = completion(model="claude-2.1", messages=messages)

#### Example prompt sent to Claude

    You are a snarky assistant.
    
    Human: How do I boil water?
    
    Assistant:

Usage - PDF
-----------

Pass base64 encoded PDF files to Anthropic models using the `file` content type with a `file_data` field.

\[BETA\] Citations API
----------------------

Pass `citations: {"enabled": true}` to Anthropic, to get citations on your document responses.

Note: This interface is in BETA. If you have feedback on how citations should be returned, please [tell us here](https://github.com/BerriAI/litellm/issues/7970#issuecomment-2644437943)

Files API
---------

Upload files once and reference them by `file_id` in multiple requests—no need to re-upload content each time.

*   **Max file size:** 500 MB | **Total storage:** 100 GB per org
*   **Pricing:** File API operations are free. File content used in Messages requests is priced as input tokens.

**Supported models by file type:**

*   **Images:** All Claude 3+ models
*   **PDFs:** All Claude 3.5+ models
*   **Other file types** (for code execution): Claude 3.5 Haiku + all Claude 3.7+ models

### Quick Start

    import litellm
    import os
    
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."
    
    # 1. Upload a file once
    file = litellm.create_file(
        file=open("document.pdf", "rb"),
        purpose="messages",
        custom_llm_provider="anthropic",
    )
    
    # 2. Use file_id in messages (no re-upload needed)
    response = litellm.completion(
        model="anthropic/claude-sonnet-4-5-20250929",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Summarize this document"},
                {"type": "file", "file": {"file_id": file.id, "format": "application/pdf"}}
            ]
        }]
    )

### File Operations

Operation

Function

Upload

`litellm.create_file(file, purpose="messages", custom_llm_provider="anthropic")`

List

`litellm.file_list(custom_llm_provider="anthropic")`

Retrieve

`litellm.file_retrieve(file_id, custom_llm_provider="anthropic")`

Delete

`litellm.file_delete(file_id, custom_llm_provider="anthropic")`

Download

`litellm.file_content(file_id, custom_llm_provider="anthropic")`

### Supported Formats

File Type

Format Value

PDF

`application/pdf`

Plain text

`text/plain`

JPEG

`image/jpeg`

PNG

`image/png`

GIF

`image/gif`

WebP

`image/webp`

### Using Images

    # Upload image
    image = litellm.create_file(
        file=open("photo.jpg", "rb"),
        purpose="messages",
        custom_llm_provider="anthropic",
    )
    
    # Use in message
    response = litellm.completion(
        model="anthropic/claude-sonnet-4-5-20250929",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {"type": "file", "file": {"file_id": image.id, "format": "image/jpeg"}}
            ]
        }]
    )

Usage - passing 'user\_id' to Anthropic
---------------------------------------

LiteLLM translates the OpenAI `user` param to Anthropic's `metadata[user_id]` param.

Usage - Agent Skills
--------------------

LiteLLM supports using Agent Skills with the API

The container and its "id" will be present in "provider\_specific\_fields" in streaming/non-streaming response

🚅

LiteLLM Enterprise

SSO/SAML, audit logs, spend tracking, multi-team management, and guardrails — built for production.

[Learn more →](/docs/enterprise)