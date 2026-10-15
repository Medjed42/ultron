import re

with open('main.py', 'r', encoding='utf-8') as f:
    text = f.read()

replacement = """async def _do_shutdown():
                    await self._save_session_summary()
                    if self.session:
                        try:
                            await self.session.send_client_content(
                                turns={"role": "user", "parts": [{"text": "Say a brief natural goodbye to the user."}]},
                                turn_complete=True,
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                    try:
                        import plugins.smartthings as st
                        st.on_app_exit()
                    except Exception:
                        pass
                    try:
                        import requests
                        requests.post("http://localhost:31337/shutdown", timeout=2)
                    except Exception:
                        pass
                    import os as _os
                    _os.system('taskkill /FI "WINDOWTITLE eq Car Telegram Bot*" /T /F >nul 2>&1')
                    _os._exit(0)"""

text = re.sub(r'async def _do_shutdown\(\):.*?_os\._exit\(0\)', replacement, text, flags=re.DOTALL)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(text)
