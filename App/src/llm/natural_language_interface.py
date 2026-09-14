"""
Natural Language Interface - Provider-Based Architecture

Convert plain English to stock queries using LLM function calling

    ⚠️  NOT WIRED INTO THE REQUEST PATH - THIS MODULE IS CURRENTLY DEAD CODE.

    Nothing in the running application imports ``NaturalLanguageInterface`` or
    ``ask()``. ``App/api/server.py`` - the only thing that actually serves chat
    requests - calls the ``google.generativeai`` SDK directly, builds its own
    chat session, and dispatches tool calls itself.

    Practical consequence: setting ``LLM_PROVIDER`` (in ``.env`` or the
    environment) HAS NO EFFECT TODAY. The ``provider=`` argument below and
    ``config.LLM_PROVIDER`` are only consulted if you call this class yourself;
    the HTTP API never does, and always uses Gemini.

    A second drift to be aware of: ``query()`` dispatches only four functions
    (query_stocks, calculate_indicators, query_corporate_actions,
    fetch_stock_data), while ``FUNCTION_DECLARATIONS`` now declares seven -
    resolve_ticker, fetch_any and get_option_chain would fall through to the
    "Unknown function" branch here. ``server.py`` handles all seven.

    The code is kept rather than deleted because finishing this abstraction is a
    tracked item in ``docs/ROADMAP.md`` ("Multi-provider LLM support that
    actually works"). Until that lands, treat this as a reference
    implementation, not as live behaviour.

ARCHITECTURE:
- Provider-based design (Gemini, Groq, or Hybrid)
- Easy switching between providers via config
- Fallback support for reliability
- Maintains backward compatibility

Reference: FROM_SCRATCH_DOCS/LLM_INTEGRATION_COMPLETE_GUIDE.md
"""

import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import config
from .function_declarations import FUNCTION_DECLARATIONS, SYSTEM_PROMPT
from .providers import create_provider


class NaturalLanguageInterface:
    """
    Natural language interface using pluggable LLM providers

    Supports:
    - Gemini (reliable, battle-tested, slower ~2.6s)
    - Groq (fast ~0.3s, 80% reliable with workarounds)
    - Hybrid (try Groq first, fallback to Gemini)

    Examples:
        # Use Groq (fast)
        interface = NaturalLanguageInterface(provider='groq')

        # Use Gemini (reliable)
        interface = NaturalLanguageInterface(provider='gemini')

        # Use Hybrid (best of both)
        interface = NaturalLanguageInterface(provider='hybrid')
    """

    def __init__(self, provider: str = None, api_key: Optional[str] = None,
                 groq_api_key: Optional[str] = None, gemini_api_key: Optional[str] = None):
        """
        Initialize natural language interface with function calling

        Args:
            provider: 'gemini', 'groq', or 'hybrid' (defaults to config.LLM_PROVIDER)
            api_key: Deprecated - use groq_api_key or gemini_api_key
            groq_api_key: Groq API key (defaults to config.GROQ_API_KEY)
            gemini_api_key: Gemini API key (defaults to config.GEMINI_API_KEY)
        """
        # Get provider from config if not specified
        if provider is None:
            provider = getattr(config, 'LLM_PROVIDER', 'gemini')

        # Backward compatibility
        if api_key and provider == 'gemini':
            gemini_api_key = api_key
        elif api_key and provider == 'groq':
            groq_api_key = api_key

        # Create provider
        self.provider = create_provider(
            provider_type=provider,
            groq_api_key=groq_api_key,
            gemini_api_key=gemini_api_key
        )

        self.provider_name = self.provider.get_provider_name()

    def parse_query(self, natural_language: str) -> Dict[str, Any]:
        """
        Convert natural language to function call using native function calling

        NO JSON PARSING - LLM returns structured function calls directly

        Args:
            natural_language: User's question in plain English

        Returns:
            Dictionary with function_name and parameters

        Example:
            >>> parse_query("Get TCS stock price")
            {"function_name": "query_stocks", "params": {"filters": {"symbol": "TCS"}}}

            >>> parse_query("Top 10 IT stocks by market cap")
            {"function_name": "query_stocks",
             "params": {"filters": {"sector": "IT"}, "limit": 10, "sort_by": "market_cap"}}
        """
        # Delegate to provider
        result = self.provider.generate_function_call(
            query=natural_language,
            functions=FUNCTION_DECLARATIONS,
            system_prompt=SYSTEM_PROMPT
        )

        # Add provider metadata
        if 'error' not in result:
            result['provider'] = self.provider_name

        return result

    def query(self, natural_language: str, fetcher) -> Dict[str, Any]:
        """
        Complete flow: Parse natural language → Execute function → Return result

        Args:
            natural_language: User's question
            fetcher: UniversalDataFetcher instance

        Returns:
            Query result with data and metadata

        Example:
            >>> interface = NaturalLanguageInterface()
            >>> result = interface.query("What is TCS price?", fetcher)
            >>> print(result['data'])
        """
        # Parse natural language to function call (NO JSON PARSING!)
        parsed = self.parse_query(natural_language)

        # Check for errors
        if 'error' in parsed:
            return {
                'error': parsed['error'],
                'original_query': natural_language,
                'details': parsed
            }

        # Extract function name and parameters
        function_name = parsed['function_name']
        params = parsed['params']

        # Convert float to int for integer parameters (some LLMs return floats)
        if 'limit' in params and isinstance(params['limit'], float):
            params['limit'] = int(params['limit'])
        if 'days' in params and isinstance(params['days'], float):
            params['days'] = int(params['days'])

        # Execute appropriate function
        try:
            if function_name == 'query_stocks':
                result = fetcher.query_stocks(**params)
            elif function_name == 'calculate_indicators':
                result = fetcher.calculate_indicators(**params)
            elif function_name == 'query_corporate_actions':
                result = fetcher.query_corporate_actions(**params)
            elif function_name == 'fetch_stock_data':
                result = fetcher.fetch_stock_data(**params)
            else:
                return {
                    'error': f'Unknown function: {function_name}',
                    'original_query': natural_language,
                    'available_functions': ['query_stocks', 'calculate_indicators',
                                           'query_corporate_actions', 'fetch_stock_data']
                }

            # Add metadata about the query
            if result and isinstance(result, dict):
                if 'metadata' not in result:
                    result['metadata'] = {}
                result['metadata']['natural_language_query'] = natural_language
                result['metadata']['function_called'] = function_name
                result['metadata']['params_used'] = params
                result['metadata']['llm_provider'] = self.provider_name

                # Build neutral resolution notice (no internal method names)
                try:
                    tr = result.get('ticker_resolution') or {}
                    orig = tr.get('original')
                    resolved = tr.get('resolved')
                    md = (result.get('data') if isinstance(result.get('data'), dict) else {})
                    # Prefer resolver metadata if available in separate field
                    res_meta = tr if tr else {}
                    # If a ticker changed, construct a neutral notice
                    if orig and resolved and str(orig).upper() != str(resolved).upper():
                        eff = None
                        reason = None
                        # Try to harvest effective_date and reason from resolver metadata embedded in fetcher flow
                        # UniversalDataFetcher attaches resolver metadata to 'ticker_resolution' note/confidence only
                        # The backend resolver embeds effective_date and reason inside its own metadata; add when present separately
                        # We look for these fields attached elsewhere in the response metadata too
                        res_extra = result.get('metadata') or {}
                        eff = res_extra.get('effective_date') or res_meta.get('effective_date')
                        reason = res_extra.get('reason') or res_meta.get('reason')
                        parts = [f"Note: {str(orig).upper()} → {str(resolved).upper()}"]
                        if eff and isinstance(eff, str) and eff.strip():
                            parts[0] = parts[0] + f" (changed on {eff.strip()})"
                        if reason and isinstance(reason, str) and reason.strip():
                            parts[0] = parts[0] + f"; reason: {reason.strip()}"
                        parts.append("Showing current data.")
                        resolution_notice = " ".join(parts)
                        # Disallowed internal jargon terms removal (fail-safe)
                        for term in ["alias lineage", "symbol fuzzy", "demerger correlation", "CF-CA", "resolver cache", "internal method"]:
                            resolution_notice = resolution_notice.replace(term, "")
                        result['metadata']['resolution_notice'] = resolution_notice
                except Exception:
                    pass

            return result

        except TypeError as e:
            # Parameter mismatch - fetcher method signature doesn't match
            return {
                'error': f'Parameter mismatch calling {function_name}: {str(e)}',
                'original_query': natural_language,
                'function_name': function_name,
                'params': params
            }
        except Exception as e:
            # Other execution errors
            return {
                'error': f'Execution failed: {str(e)}',
                'original_query': natural_language,
                'function_name': function_name,
                'params': params,
                'exception_type': type(e).__name__
            }


# Simple function interface (for backward compatibility)
def ask(question: str, fetcher, provider: str = None) -> Dict[str, Any]:
    """
    Simple function to ask questions in natural language

    Args:
        question: Natural language question
        fetcher: UniversalDataFetcher instance
        provider: 'gemini', 'groq', or 'hybrid' (optional)

    Returns:
        Query result

    Example:
        >>> result = ask("Get TCS stock price", fetcher)
        >>> print(result['data'])

        >>> result = ask("Get TCS stock price", fetcher, provider='groq')  # Force Groq
        >>> print(result['data'])
    """
    interface = NaturalLanguageInterface(provider=provider)
    return interface.query(question, fetcher)
