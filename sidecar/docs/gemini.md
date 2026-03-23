Property

Details

Description

Google AI Studio is a fully-managed AI development platform for building and using generative AI.

Provider Route on LiteLLM

`gemini/`

Provider Doc

[Google AI Studio ↗](https://aistudio.google.com/)

API Endpoint for Provider

[https://generativelanguage.googleapis.com](https://generativelanguage.googleapis.com)

Supported OpenAI Endpoints

`/chat/completions`, [`/embeddings`](/docs/embedding/supported_embedding#gemini-ai-embedding-models), `/completions`, [`/videos`](/docs/providers/gemini/videos), [`/images/edits`](/docs/image_edits)

Pass-through Endpoint

[Supported](/docs/pass_through/google_ai_studio)

  

API Keys
--------

    import os
    os.environ["GEMINI_API_KEY"] = "your-api-key"

Sample Usage
------------

    from litellm import completion
    import os
    
    os.environ['GEMINI_API_KEY'] = ""
    response = completion(
        model="gemini/gemini-pro", 
        messages=[{"role": "user", "content": "write code for saying hi from LiteLLM"}]
    )

Supported OpenAI Params
-----------------------

*   temperature
*   top\_p
*   max\_tokens
*   max\_completion\_tokens
*   stream
*   tools
*   tool\_choice
*   include\_server\_side\_tool\_invocations
*   functions
*   response\_format
*   n
*   stop
*   logprobs
*   frequency\_penalty
*   modalities
*   reasoning\_content
*   audio (for TTS models only)

**Anthropic Params**

*   thinking (used to set max budget tokens across anthropic/gemini models)

[**See Updated List**](https://github.com/BerriAI/litellm/blob/main/litellm/llms/gemini/chat/transformation.py#L70)

Usage - Thinking / reasoning\_content
-------------------------------------

LiteLLM translates OpenAI's `reasoning_effort` to Gemini's `thinking` parameter. [Code](https://github.com/BerriAI/litellm/blob/620664921902d7a9bfb29897a7b27c1a7ef4ddfb/litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py#L362)

**Cost Optimization:** Use `reasoning_effort="none"` (OpenAI standard) for significant cost savings - up to 96% cheaper. [Google's docs](https://ai.google.dev/gemini-api/docs/openai)

**Mapping for Gemini 2.5 and earlier models**

reasoning\_effort

thinking

Notes

"none"

"budget\_tokens": 0, "includeThoughts": false

💰 **Recommended for cost optimization** - OpenAI-compatible, always 0

"disable"

"budget\_tokens": DEFAULT (0), "includeThoughts": false

LiteLLM-specific, configurable via env var

"low"

"budget\_tokens": 1024

"medium"

"budget\_tokens": 2048

"high"

"budget\_tokens": 4096

**Mapping for Gemini 3+ models**

reasoning\_effort

thinking\_level

Notes

"minimal"

"low"

Minimizes latency and cost

"low"

"low"

Best for simple instruction following or chat

"medium"

"high"

Maps to high (medium not yet available)

"high"

"high"

Maximizes reasoning depth

"disable"

"low"

Cannot fully disable thinking in Gemini 3

"none"

"low"

Cannot fully disable thinking in Gemini 3

### Gemini 3+ Models - thinking\_level Parameter

For Gemini 3+ models (e.g., `gemini-3-pro-preview`), you can use the new `thinking_level` parameter directly:

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
                    reasoning_content='The capital of France is Paris. This is a very straightforward factual question.'
                ),
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

### Pass thinking to Gemini models

You can also pass the `thinking` parameter to Gemini models.

This is translated to Gemini's [`thinkingConfig` parameter](https://ai.google.dev/gemini-api/docs/thinking#set-budget).

Text-to-Speech (TTS) Audio Output
---------------------------------

### Supported Models

LiteLLM supports Gemini TTS models with audio capabilities (e.g. `gemini-2.5-flash-preview-tts` and `gemini-2.5-pro-preview-tts`). For the complete list of available TTS models and voices, see the [official Gemini TTS documentation](https://ai.google.dev/gemini-api/docs/speech-generation).

### Limitations

### Quick Start

### Advanced Usage

You can combine TTS with other Gemini features:

For more information about Gemini's TTS capabilities and available voices, see the [official Gemini TTS documentation](https://ai.google.dev/gemini-api/docs/speech-generation).

Passing Gemini Specific Params
------------------------------

### Response schema

LiteLLM supports sending `response_schema` as a param for Gemini-1.5-Pro on Google AI Studio.

**Response Schema**

**Validate Schema**

To validate the response\_schema, set `enforce_validation: true`.

LiteLLM will validate the response against the schema, and raise a `JSONSchemaValidationError` if the response does not match the schema.

JSONSchemaValidationError inherits from `openai.APIError`

Access the raw response with `e.raw_response`

### GenerationConfig Params

To pass additional GenerationConfig params - e.g. `topK`, just pass it in the request body of the call, and LiteLLM will pass it straight through as a key-value pair in the request body.

[**See Gemini GenerationConfigParams**](https://ai.google.dev/api/generate-content#v1beta.GenerationConfig)

**Validate Schema**

To validate the response\_schema, set `enforce_validation: true`.

Specifying Safety Settings
--------------------------

In certain use-cases you may need to make calls to the models and pass [safety settings](https://ai.google.dev/docs/safety_setting_gemini) different from the defaults. To do so, simple pass the `safety_settings` argument to `completion` or `acompletion`. For example:

    response = completion(
        model="gemini/gemini-pro", 
        messages=[{"role": "user", "content": "write code for saying hi from LiteLLM"}],
        safety_settings=[
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_NONE",
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_NONE",
            },
        ]
    )

Tool Calling
------------

    from litellm import completion
    import os
    # set env
    os.environ["GEMINI_API_KEY"] = ".."
    
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
        model="gemini/gemini-1.5-flash",
        messages=messages,
        tools=tools,
    )
    # Add any assertions, here to check response args
    print(response)
    assert isinstance(response.choices[0].message.tool_calls[0].function.name, str)
    assert isinstance(
        response.choices[0].message.tool_calls[0].function.arguments, str
    )

### Context Circulation (Server-Side Tool Combination)

Context circulation allows Gemini 3+ models to combine **built-in tools** (like Google Search) with **your custom functions** in the same request. Without it, Gemini returns an error if you try to use both.

When enabled, Gemini can execute Google Search server-side, use those results to decide whether to call your custom functions, and return the full chain of reasoning.

**How it works:**

1.  You pass `include_server_side_tool_invocations=True` along with both Google Search and your function tools
2.  Gemini executes server-side tools internally and returns `toolCall`/`toolResponse` parts alongside any `functionCall` parts
3.  LiteLLM extracts the server-side invocations into `provider_specific_fields["server_side_tool_invocations"]`
4.  On subsequent turns, include the full assistant message in your conversation history — LiteLLM re-injects the server-side parts automatically

### URL Context

### Code Execution Tool

### Computer Use Tool

### Environment Mapping

LiteLLM Input

Gemini API Value

`"browser"`

`ENVIRONMENT_BROWSER`

`"unspecified"`

`ENVIRONMENT_UNSPECIFIED`

`ENVIRONMENT_BROWSER`

`ENVIRONMENT_BROWSER` (passed through)

`ENVIRONMENT_UNSPECIFIED`

`ENVIRONMENT_UNSPECIFIED` (passed through)

Thought Signatures
------------------

Thought signatures are encrypted representations of the model's internal reasoning process for a given turn in a conversation. By passing thought signatures back to the model in subsequent requests, you provide it with the context of its previous thoughts, allowing it to build upon its reasoning and maintain a coherent line of inquiry.

Thought signatures are particularly important for multi-turn function calling scenarios where the model needs to maintain context across multiple tool invocations.

### How Thought Signatures Work

*   **Function calls with signatures**: When Gemini returns a function call, it includes a `thought_signature` in the response
*   **Preservation**: LiteLLM automatically extracts and stores thought signatures in `provider_specific_fields` of tool calls
*   **Return in conversation history**: When you include the assistant's message with tool calls in subsequent requests, LiteLLM automatically preserves and returns the thought signatures to Gemini
*   **Parallel function calls**: Only the first function call in a parallel set has a thought signature
*   **Sequential function calls**: Each function call in a multi-step sequence has its own signature

### Enabling Thought Signatures

To enable thought signatures, you need to enable thinking/reasoning:

### Multi-Turn Function Calling with Thought Signatures

When building conversation history for multi-turn function calling, you must include the thought signatures from previous responses. LiteLLM handles this automatically when you append the full assistant message to your conversation history.

### Important Notes

1.  **Automatic Handling**: LiteLLM automatically extracts thought signatures from Gemini responses and preserves them when you include assistant messages in conversation history. You don't need to manually extract or manage them.
    
2.  **Parallel Function Calls**: When the model makes parallel function calls, only the first function call will have a thought signature. Subsequent parallel calls won't have signatures.
    
3.  **Sequential Function Calls**: In multi-step function calling scenarios, each step's first function call will have its own thought signature that must be preserved.
    
4.  **Required for Context**: Thought signatures are essential for maintaining reasoning context across multi-turn conversations with function calling. Without them, the model may lose context of its previous reasoning.
    
5.  **Format**: Thought signatures are stored in `provider_specific_fields.thought_signature` of tool calls in the response, and are automatically included when you append the assistant message to your conversation history.
    
6.  **Chat Completions Clients**: With chat completions clients where you cannot control whether or not the previous assistant message is included as-is (ex langchain's ChatOpenAI), LiteLLM also preserves the thought signature by appending it to the tool call id (`call_123__thought__<thought-signature>`) and extracting it back out before sending the outbound request to Gemini.
    

JSON Mode
---------

Gemini-Pro-Vision
-----------------

LiteLLM Supports the following image types passed in `url`

*   Images with direct links - [https://storage.googleapis.com/github-repo/img/gemini/intro/landmark3.jpg](https://storage.googleapis.com/github-repo/img/gemini/intro/landmark3.jpg)
*   Image in local storage - ./localimage.jpeg

Media Resolution Control (Images & Videos)
------------------------------------------

LiteLLM supports OpenAI's `detail` parameter for specifying the image resolution when using Gemini models. The behavior differs between Gemini versions:

Gemini Version

Resolution Control

Behavior

Gemini 3+

Per-part

Each image/video can have its own `detail` setting

Gemini 2.x (2.0, 2.5)

Global

The highest `detail` from all images is applied globally via `mediaResolution` in `generationConfig`

**Supported `detail` values:**

*   `"low"` - Maps to `MEDIA_RESOLUTION_LOW` (280 tokens for images, 70 tokens per frame for videos)
*   `"medium"` - Maps to `MEDIA_RESOLUTION_MEDIUM`
*   `"high"` - Maps to `MEDIA_RESOLUTION_HIGH` (1120 tokens for images)
*   `"ultra_high"` - Maps to `MEDIA_RESOLUTION_ULTRA_HIGH`
*   `"auto"` or `None` - Model decides optimal resolution (no `media_resolution` set)

**Usage Examples:**

For Gemini 3+ models, LiteLLM supports fine-grained video processing control through the `video_metadata` field. This allows you to specify frame extraction rates and time ranges for video analysis.

**Supported `video_metadata` parameters:**

Parameter

Type

Description

Example

`fps`

Number

Frame extraction rate (frames per second)

`5`

`start_offset`

String

Start time for video clip processing

`"10s"`

`end_offset`

String

End time for video clip processing

`"60s"`

**Usage Examples:**

Sample Usage
------------

    import os
    import litellm
    from dotenv import load_dotenv
    
    # Load the environment variables from .env file
    load_dotenv()
    os.environ["GEMINI_API_KEY"] = os.getenv('GEMINI_API_KEY')
    
    prompt = 'Describe the image in a few sentences.'
    # Note: You can pass here the URL or Path of image directly.
    image_url = 'https://storage.googleapis.com/github-repo/img/gemini/intro/landmark3.jpg'
    
    # Create the messages payload according to the documentation
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }
            ]
        }
    ]
    
    # Make the API call to Gemini model
    response = litellm.completion(
        model="gemini/gemini-pro-vision",
        messages=messages,
    )
    
    # Extract the response content
    content = response.get('choices', [{}])[0].get('message', {}).get('content')
    
    # Print the result
    print(content)

gemini-robotics-er-1.5-preview Usage
------------------------------------

    from litellm import api_base
    from openai import OpenAI
    import os
    import base64
    
    client = OpenAI(base_url="http://0.0.0.0:4000", api_key="sk-12345")
    base64_image = base64.b64encode(open("closeup-object-on-table-many-260nw-1216144471.webp", "rb").read()).decode()
    
    import json
    import re
    tools = [{"codeExecution": {}}] 
    response = client.chat.completions.create(
        model="gemini/gemini-robotics-er-1.5-preview",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Point to no more than 10 items in the image. The label returned should be an identifying name for the object detected. The answer should follow the json format: [{\"point\": [y, x], \"label\": <label1>}, ...]. The points are in [y, x] format normalized to 0-1000."
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                    }
                ]
            }
        ],
        tools=tools
    )
    
    # Extract JSON from markdown code block if present
    content = response.choices[0].message.content
    # Look for triple-backtick JSON block
    match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = content
    
    try:
        data = json.loads(json_str)
        print(json.dumps(data, indent=2))
    except Exception as e:
        print("Error parsing response as JSON:", e)
        print("Response content:", content)

Usage - PDF / Videos / etc. Files
---------------------------------

### Inline Data (e.g. audio stream)

LiteLLM follows the OpenAI format and accepts sending inline data as an encoded base64 string.

The format to follow is

    data:<mime_type>;base64,<encoded_data>

\*\* LITELLM CALL \*\*

    import litellm
    from pathlib import Path
    import base64
    import os
    
    os.environ["GEMINI_API_KEY"] = "" 
    
    litellm.set_verbose = True # 👈 See Raw call 
    
    audio_bytes = Path("speech_vertex.mp3").read_bytes()
    encoded_data = base64.b64encode(audio_bytes).decode("utf-8")
    print("Audio Bytes = {}".format(audio_bytes))
    model = "gemini/gemini-1.5-flash"
    response = litellm.completion(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please summarize the audio."},
                    {
                        "type": "file",
                        "file": {
                            "file_data": "data:audio/mp3;base64,{}".format(encoded_data), # 👈 SET MIME_TYPE + DATA
                        }
                    },
                ],
            }
        ],
    )

\*\* Equivalent GOOGLE API CALL \*\*

    # Initialize a Gemini model appropriate for your use case.
    model = genai.GenerativeModel('models/gemini-1.5-flash')
    
    # Create the prompt.
    prompt = "Please summarize the audio."
    
    # Load the samplesmall.mp3 file into a Python Blob object containing the audio
    # file's bytes and then pass the prompt and the audio to Gemini.
    response = model.generate_content([
        prompt,
        {
            "mime_type": "audio/mp3",
            "data": pathlib.Path('samplesmall.mp3').read_bytes()
        }
    ])
    
    # Output Gemini's response to the prompt and the inline audio.
    print(response.text)

### https:// file

    import litellm
    import os
    
    os.environ["GEMINI_API_KEY"] = "" 
    
    litellm.set_verbose = True # 👈 See Raw call 
    
    model = "gemini/gemini-1.5-flash"
    response = litellm.completion(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please summarize the file."},
                    {
                        "type": "file",
                        "file": {
                            "file_id": "https://storage...", # 👈 SET THE IMG URL
                            "format": "application/pdf" # OPTIONAL
                        }
                    },
                ],
            }
        ],
    )

### gs:// file

    import litellm
    import os
    
    os.environ["GEMINI_API_KEY"] = "" 
    
    litellm.set_verbose = True # 👈 See Raw call 
    
    model = "gemini/gemini-1.5-flash"
    response = litellm.completion(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please summarize the file."},
                    {
                        "type": "file",
                        "file": {
                            "file_id": "gs://storage...", # 👈 SET THE IMG URL
                            "format": "application/pdf" # OPTIONAL
                        }
                    },
                ],
            }
        ],
    )

Chat Models
-----------

Model Name

Function Call

Required OS Variables

gemini-pro

`completion(model='gemini/gemini-pro', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-1.5-pro-latest

`completion(model='gemini/gemini-1.5-pro-latest', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-2.0-flash

`completion(model='gemini/gemini-2.0-flash', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-2.0-flash-exp

`completion(model='gemini/gemini-2.0-flash-exp', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-2.0-flash-lite-preview-02-05

`completion(model='gemini/gemini-2.0-flash-lite-preview-02-05', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-2.5-flash-preview-09-2025

`completion(model='gemini/gemini-2.5-flash-preview-09-2025', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-2.5-flash-lite-preview-09-2025

`completion(model='gemini/gemini-2.5-flash-lite-preview-09-2025', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-3.1-flash-lite-preview

`completion(model='gemini/gemini-3.1-flash-lite-preview', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-flash-latest

`completion(model='gemini/gemini-flash-latest', messages)`

`os.environ['GEMINI_API_KEY']`

gemini-flash-lite-latest

`completion(model='gemini/gemini-flash-lite-latest', messages)`

`os.environ['GEMINI_API_KEY']`

Context Caching
---------------

Use Google AI Studio context caching is supported by

    {
        {
            "role": "system",
            "content": ...,
            "cache_control": {"type": "ephemeral"} # 👈 KEY CHANGE
        },
        ...
    }

in your message content block.

### Custom TTL Support

You can now specify a custom Time-To-Live (TTL) for your cached content using the `ttl` parameter:

    {
        {
            "role": "system",
            "content": ...,
            "cache_control": {
                "type": "ephemeral",
                "ttl": "3600s"  # 👈 Cache for 1 hour
            }
        },
        ...
    }

**TTL Format Requirements:**

*   Must be a string ending with 's' for seconds
*   Must contain a positive number (can be decimal)
*   Examples: `"3600s"` (1 hour), `"7200s"` (2 hours), `"1800s"` (30 minutes), `"1.5s"` (1.5 seconds)

**TTL Behavior:**

*   If multiple cached messages have different TTLs, the first valid TTL encountered will be used
*   Invalid TTL formats are ignored and the cache will use Google's default expiration time
*   If no TTL is specified, Google's default cache expiration (approximately 1 hour) applies

### Architecture Diagram

**Notes:**

*   [Relevant code](https://github.com/BerriAI/litellm/blob/main/litellm/llms/vertex_ai/context_caching/vertex_ai_context_caching.py#L255)
    
*   Gemini Context Caching only allows 1 block of continuous messages to be cached.
    
*   If multiple non-continuous blocks contain `cache_control` - the first continuous block will be used. (sent to `/cachedContent` in the [Gemini format](https://ai.google.dev/api/caching#cache_create-SHELL))
    
*   The raw request to Gemini's `/generateContent` endpoint looks like this:
    

    curl -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-001:generateContent?key=$GOOGLE_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{
          "contents": [
            {
              "parts":[{
                "text": "Please summarize this transcript"
              }],
              "role": "user"
            },
          ],
          "cachedContent": "'$CACHE_NAME'"
        }'

### Example Usage

Image Generation
----------------

### Image Generation Pricing

Gemini image generation models (like `gemini-3-pro-image-preview`) return `image_tokens` in the response usage. These tokens are priced differently from text tokens:

Token Type

Price per 1M tokens

Price per token

Text output

$12

$0.000012

Image output

$120

$0.00012

The number of image tokens depends on the output resolution:

Resolution

Tokens per image

Cost per image

1K-2K (1024x1024 to 2048x2048)

1,120

$0.134

4K (4096x4096)

2,000

$0.24

LiteLLM automatically calculates costs using `output_cost_per_image_token` from the model pricing configuration.

**Example response usage:**

    {
        "completion_tokens_details": {
            "reasoning_tokens": 225,
            "text_tokens": 0,
            "image_tokens": 1120
        }
    }

For more details, see [Google's Gemini pricing documentation](https://ai.google.dev/gemini-api/docs/pricing).

🚅

LiteLLM Enterprise

SSO/SAML, audit logs, spend tracking, multi-team management, and guardrails — built for production.

[Learn more →](/docs/enterprise)