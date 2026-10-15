from abc import ABC, abstractmethod
from typing import Dict, Any, List

class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def execute_task(self, task_description: str, tools_available: List[str]) -> Dict[str, Any]:
        """
        Executes a given task using the specific AI model.
        Returns a dictionary with status and result.
        """
        pass
