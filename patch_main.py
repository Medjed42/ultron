import re

with open('main.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update imports
old_imports = r'from memory\.config_manager import \('
new_imports = r'from memory.config_manager import (\n    get_ai_provider, get_openai_key,'
text = re.sub(old_imports, new_imports, text, count=1)

# 2. Add openai_adapter import
text = text.replace('import sounddevice as sd', 'import sounddevice as sd\nfrom openai_adapter import OpenAILiveSession')

# 3. Patch the connection logic
old_connect = r'''                client = genai\.Client\(
                    api_key=_get_api_key\(\),
                    http_options=\{"api_version": "v1alpha" if self\._enhanced_live else "v1beta"\}
                \)

                async with \(
                    client\.aio\.live\.connect\(model=live_model, config=config\) as session,
                    asyncio\.TaskGroup\(\) as tg,
                \):
                    self\.session          = session'''

new_connect = r'''                
                # Check provider
                provider = get_ai_provider()
                
                if provider == "chatgpt":
                    openai_key = get_openai_key()
                    if not openai_key:
                        raise ValueError("ChatGPT API key is missing!")
                    system_text = ""
                    if config.system_instruction and config.system_instruction.parts:
                        system_text = config.system_instruction.parts[0].text
                    
                    # Ensure tools is list
                    raw_tools = config.tools if config.tools else []
                    
                    session_ctx = OpenAILiveSession(
                        api_key=openai_key,
                        system_instruction=system_text,
                        voice_name=voice_name,
                        tools=raw_tools
                    )
                else:
                    client = genai.Client(
                        api_key=_get_api_key(),
                        http_options={"api_version": "v1alpha" if self._enhanced_live else "v1beta"}
                    )
                    session_ctx = client.aio.live.connect(model=live_model, config=config)

                async with (
                    session_ctx as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session'''

text = re.sub(old_connect, new_connect, text, flags=re.DOTALL)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(text)
