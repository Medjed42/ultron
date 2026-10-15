from typing import List, Dict, Any
from agents import GeminiAgent, ChatGPTAgent, ClaudeAgent
from tools import send_to_discord

class UltronOrchestrator:
    def __init__(self):
        self.agents = {
            "gemini": GeminiAgent(),
            "chatgpt": ChatGPTAgent(),
            "claude": ClaudeAgent()
        }
        # A list of tools to inform agents what is possible
        self.available_tools = [
            "send_email(to, subject, content)",
            "upload_video(file_path, title, description)",
            "create_site(project_name, prompt)",
            "send_to_discord(message)"
        ]

    def delegate_task(self, task_description: str, preferred_agent: str = None) -> Dict[str, Any]:
        send_to_discord(f"**Ultron received a new task:** {task_description}")
        
        # Decide which agent to use
        agent_name = preferred_agent if preferred_agent in self.agents else "chatgpt"
        agent = self.agents[agent_name]
        
        send_to_discord(f"**Delegating task to:** {agent_name.capitalize()}")
        
        # Execute task
        result = agent.execute_task(task_description, self.available_tools)
        
        if result["status"] == "success":
            send_to_discord(f"**Task completed successfully by {agent_name.capitalize()}.**\nResult preview: {result['result'][:200]}...")
        else:
            send_to_discord(f"**Task failed.**\nError: {result['message']}")
            
        return result
