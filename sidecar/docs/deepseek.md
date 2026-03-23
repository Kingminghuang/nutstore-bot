[https://deepseek.com/](https://deepseek.com/)

**We support ALL Deepseek models, just set `deepseek/` as a prefix when sending completion requests**

API Key
-------

    # env variable
    os.environ['DEEPSEEK_API_KEY']

Sample Usage
------------

    from litellm import completion
    import os
    
    os.environ['DEEPSEEK_API_KEY'] = ""
    response = completion(
        model="deepseek/deepseek-chat", 
        messages=[
           {"role": "user", "content": "hello from litellm"}
       ],
    )
    print(response)

Sample Usage - Streaming
------------------------

    from litellm import completion
    import os
    
    os.environ['DEEPSEEK_API_KEY'] = ""
    response = completion(
        model="deepseek/deepseek-chat", 
        messages=[
           {"role": "user", "content": "hello from litellm"}
       ],
        stream=True
    )
    
    for chunk in response:
        print(chunk)

Supported Models - ALL Deepseek Models Supported!
-------------------------------------------------

We support ALL Deepseek models, just set `deepseek/` as a prefix when sending completion requests

Model Name

Function Call

deepseek-chat

`completion(model="deepseek/deepseek-chat", messages)`

deepseek-coder

`completion(model="deepseek/deepseek-coder", messages)`

Reasoning Models
----------------

Model Name

Function Call

deepseek-reasoner

`completion(model="deepseek/deepseek-reasoner", messages)`

### Thinking / Reasoning Mode

Enable thinking mode for DeepSeek reasoner models using `thinking` or `reasoning_effort` parameters:

*   thinking param
*   reasoning\_effort param

    from litellm import completion
    import os
    
    os.environ['DEEPSEEK_API_KEY'] = ""
    
    resp = completion(
        model="deepseek/deepseek-reasoner",
        messages=[{"role": "user", "content": "What is 2+2?"}],
        thinking={"type": "enabled"},
    )
    print(resp.choices[0].message.reasoning_content)  # Model's reasoning
    print(resp.choices[0].message.content)  # Final answer

### Basic Usage

*   SDK
*   PROXY

    from litellm import completion
    import os
    
    os.environ['DEEPSEEK_API_KEY'] = ""
    resp = completion(
        model="deepseek/deepseek-reasoner",
        messages=[{"role": "user", "content": "Tell me a joke."}],
    )
    
    print(
        resp.choices[0].message.reasoning_content
    )