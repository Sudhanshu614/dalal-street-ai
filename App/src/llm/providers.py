"""
LLM Provider Abstraction Layer

    ⚠️  NOT WIRED INTO THE REQUEST PATH - THIS MODULE IS CURRENTLY DEAD CODE.

    Nothing in the running application imports this module. ``App/api/server.py``
    - the only thing that actually serves chat requests - calls the
    ``google.generativeai`` SDK directly and never constructs a provider.

    Practical consequence: setting ``LLM_PROVIDER`` (in ``.env`` or the
    environment) HAS NO EFFECT TODAY. ``LLM_PROVIDER=hybrid`` or
    ``LLM_PROVIDER=groq`` is read into ``config`` and then silently ignored;
    every request still goes to Gemini. Likewise ``GROQ_API_KEY`` is unused.

    The code is kept rather than deleted because finishing this abstraction is a
    tracked item in ``docs/ROADMAP.md`` ("Multi-provider LLM support that
    actually works"). Until that lands, treat everything below as a reference
    implementation, not as live behaviour.

    Also note the model names here have drifted from the live configuration:
      - ``GeminiProvider`` pins ``gemini-2.0-flash-exp``, while the server uses
        ``config.GEMINI_MODEL`` (default ``gemini-2.5-flash``).
      - ``GroqProvider`` pins ``llama-3.3-70b-versatile``, which nothing
        validates against the Groq API's current model list.
    Wiring this up must switch both to configuration values.

Architecture:
- Provider interface for swapping between LLMs
- Gemini provider (battle-tested, reliable)
- Groq provider (fast, 30x speed improvement)
- Easy to add more providers (OpenAI, Anthropic, etc.)

Intended usage (once wired up):
    provider = create_provider('groq')  # or 'gemini'
    result = provider.generate_function_call("What is the PE ratio of TCS?")
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import config


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers

    All providers must implement generate_function_call()
    """

    @abstractmethod
    def generate_function_call(self, query: str, functions: List[Dict], system_prompt: str) -> Dict[str, Any]:
        """
        Generate function call from natural language query

        Args:
            query: User's natural language question
            functions: List of available functions
            system_prompt: System instruction

        Returns:
            {
                "function_name": str,
                "params": dict
            }
            OR
            {
                "error": str,
                "original_query": str
            }
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return provider name"""
        pass


class GeminiProvider(LLMProvider):
    """
    Gemini function calling provider

    Reliable, battle-tested, but slower (~2.6s average)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or config.GEMINI_API_KEY

        try:
            import google.generativeai as genai
            self.genai = genai
            self.available = True
        except ImportError:
            self.available = False
            raise ImportError("google-generativeai not installed. Run: pip install google-generativeai")

        # Configure Gemini
        self.genai.configure(api_key=self.api_key)

        # Initialize model (lazy initialization)
        self.model = None
        self.chat = None

    def _ensure_model_initialized(self, functions: List[Dict], system_prompt: str):
        """Lazy model initialization"""
        if self.model is None:
            self.model = self.genai.GenerativeModel(
                model_name='gemini-2.0-flash-exp',
                tools=[{'function_declarations': functions}],
                system_instruction=system_prompt
            )
            self.chat = self.model.start_chat()

    def generate_function_call(self, query: str, functions: List[Dict], system_prompt: str) -> Dict[str, Any]:
        """Generate function call using Gemini"""

        # Initialize model if needed
        self._ensure_model_initialized(functions, system_prompt)

        try:
            # Send message to Gemini
            response = self.chat.send_message(query)

            # Check response
            if not response.candidates:
                return {
                    "error": "No response from Gemini",
                    "original_query": query
                }

            candidate = response.candidates[0]

            if not candidate.content.parts:
                return {
                    "error": "No content in Gemini response",
                    "original_query": query
                }

            part = candidate.content.parts[0]

            # Extract function call
            if hasattr(part, 'function_call'):
                function_call = part.function_call

                # Convert proto to dict
                return {
                    "function_name": function_call.name,
                    "params": self._proto_to_dict(function_call.args)
                }
            else:
                # LLM returned text instead of function call
                return {
                    "error": "Gemini returned text instead of function call",
                    "text": part.text if hasattr(part, 'text') else str(part),
                    "original_query": query
                }

        except Exception as e:
            return {
                "error": f"Gemini function calling failed: {str(e)}",
                "original_query": query,
                "exception_type": type(e).__name__
            }

    def _proto_to_dict(self, obj):
        """Recursively convert Gemini proto objects to Python dicts/lists"""
        if hasattr(obj, 'items'):
            return {key: self._proto_to_dict(value) for key, value in obj.items()}
        elif hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
            try:
                return [self._proto_to_dict(item) for item in obj]
            except TypeError:
                return obj
        else:
            return obj

    def get_provider_name(self) -> str:
        return "gemini"


class GroqProvider(LLMProvider):
    """
    Groq function calling provider

    Fast (30x faster than Gemini), but less reliable without workarounds

    Workaround: Use tool_choice='required' for 95%+ reliability
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or getattr(config, 'GROQ_API_KEY', None)

        if not self.api_key:
            raise ValueError("GROQ_API_KEY not found in config")

        try:
            from groq import Groq
            self.client = Groq(api_key=self.api_key)
            self.available = True
        except ImportError:
            self.available = False
            raise ImportError("groq SDK not installed. Run: pip install groq")

    def generate_function_call(self, query: str, functions: List[Dict], system_prompt: str) -> Dict[str, Any]:
        """Generate function call using Groq"""

        # Convert function declarations to OpenAI format (Groq uses OpenAI-compatible format)
        tools = self._convert_to_openai_format(functions)

        try:
            # Call Groq with tool_choice='required' for reliability
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                tools=tools,
                tool_choice="required"  # Force function calling (95%+ reliability)
            )

            message = response.choices[0].message

            # Check if function call exists
            if hasattr(message, 'tool_calls') and message.tool_calls:
                tool_call = message.tool_calls[0]

                # Parse arguments
                import json
                try:
                    args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    return {
                        "error": "Failed to parse Groq function arguments",
                        "raw_arguments": tool_call.function.arguments,
                        "original_query": query
                    }

                return {
                    "function_name": tool_call.function.name,
                    "params": args
                }
            else:
                # No function call generated
                return {
                    "error": "Groq did not generate function call",
                    "text": message.content if hasattr(message, 'content') else str(message),
                    "original_query": query
                }

        except Exception as e:
            return {
                "error": f"Groq function calling failed: {str(e)}",
                "original_query": query,
                "exception_type": type(e).__name__
            }

    def _convert_to_openai_format(self, functions: List[Dict]) -> List[Dict]:
        """
        Convert Gemini function declarations to OpenAI tool format

        Gemini format:
        [{
            "name": "query_stocks",
            "description": "...",
            "parameters": {"type": "object", ...}
        }]

        OpenAI/Groq format:
        [{
            "type": "function",
            "function": {
                "name": "query_stocks",
                "description": "...",
                "parameters": {"type": "object", ...}
            }
        }]
        """
        return [
            {
                "type": "function",
                "function": func
            }
            for func in functions
        ]

    def get_provider_name(self) -> str:
        return "groq"


class HybridProvider(LLMProvider):
    """
    Hybrid provider with automatic fallback

    Tries Groq first (fast), falls back to Gemini (reliable) on failure

    Best of both worlds:
    - 80% queries use Groq (8x faster)
    - 20% failures automatically use Gemini (100% reliability)
    """

    def __init__(self, groq_api_key: Optional[str] = None, gemini_api_key: Optional[str] = None):
        try:
            self.groq = GroqProvider(groq_api_key)
            self.groq_available = True
        except:
            self.groq_available = False

        try:
            self.gemini = GeminiProvider(gemini_api_key)
            self.gemini_available = True
        except:
            self.gemini_available = False

        if not self.groq_available and not self.gemini_available:
            raise RuntimeError("Neither Groq nor Gemini available. Install at least one provider.")

    def generate_function_call(self, query: str, functions: List[Dict], system_prompt: str) -> Dict[str, Any]:
        """Try Groq first, fallback to Gemini"""

        # Try Groq first (fast)
        if self.groq_available:
            result = self.groq.generate_function_call(query, functions, system_prompt)

            # If successful, return
            if 'error' not in result:
                result['provider_used'] = 'groq'
                return result

        # Fallback to Gemini (reliable)
        if self.gemini_available:
            result = self.gemini.generate_function_call(query, functions, system_prompt)
            result['provider_used'] = 'gemini'
            if self.groq_available:
                result['fallback_reason'] = 'groq_failed'
            return result

        # Both failed
        return {
            "error": "Both Groq and Gemini failed",
            "original_query": query
        }

    def get_provider_name(self) -> str:
        if self.groq_available and self.gemini_available:
            return "hybrid (groq+gemini)"
        elif self.groq_available:
            return "groq"
        elif self.gemini_available:
            return "gemini"
        else:
            return "none"


def create_provider(provider_type: str = 'groq', groq_api_key: Optional[str] = None, gemini_api_key: Optional[str] = None) -> LLMProvider:
    """
    Factory function to create LLM provider

    Args:
        provider_type: 'gemini', 'groq', or 'hybrid'
        groq_api_key: Optional Groq API key
        gemini_api_key: Optional Gemini API key

    Returns:
        LLMProvider instance

    Example:
        provider = create_provider('groq')
        result = provider.generate_function_call("What is TCS PE ratio?", FUNCTIONS, SYSTEM_PROMPT)
    """
    if provider_type == 'gemini':
        return GeminiProvider(gemini_api_key)
    elif provider_type == 'groq':
        return GroqProvider(groq_api_key)
    elif provider_type == 'hybrid':
        return HybridProvider(groq_api_key, gemini_api_key)
    else:
        raise ValueError(f"Unknown provider type: {provider_type}. Use 'gemini', 'groq', or 'hybrid'")
