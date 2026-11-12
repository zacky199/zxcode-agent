import json
from typing import Tuple, Any

class ValidationUtils:
    @staticmethod
    def parse_json_safely(content: str) -> Tuple[bool, Any]:
        if not content:
            return False, None
            
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
            return True, data
        except json.JSONDecodeError:
            return False, None
