"""
FastAPI Backend Server for Stock Market AI
Senior Dev Pattern: Generic API endpoints that work with ANY query

Zero Hardcoding: No hardcoded query types, endpoints, or response formats
All responses follow standardized format for frontend consumption

Reference: FROM_SCRATCH_DOCS/FRONTEND_ARCHITECTURE.md Part 7
"""

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from typing import Dict, List, Any, Optional
from collections.abc import Iterable
import ast
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from datetime import datetime
import secrets
import sys
import os
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data_fetcher.universal_data_fetcher import UniversalDataFetcher
from src.data_fetcher.bhavcopy_downloader import BhavcopyDownloader
try:
    from src.data_fetcher.corporate_actions_ingester import CorporateActionsIngester
except ImportError:
    # Optional component. /admin/update/corporate_actions returns 501 without it.
    CorporateActionsIngester = None
from src.llm.function_declarations import FUNCTION_DECLARATIONS, SYSTEM_PROMPT
from config import config
import json
import math

# ============================================================================
# HELPER FUNCTIONS FOR GEMINI
# ============================================================================

def convert_proto_to_python(proto_value):
    """
    Convert protobuf values to Python types (recursive)

    Senior Dev: Handles nested MapComposite and other protobuf objects
    """
    # Check if it's a protobuf MapComposite or similar object
    if hasattr(proto_value, '__iter__') and hasattr(proto_value, 'items'):
        # It's a dict-like object (MapComposite)
        return {k: convert_proto_to_python(v) for k, v in proto_value.items()}
    elif isinstance(proto_value, (list, tuple)):
        # It's a list/tuple
        return [convert_proto_to_python(v) for v in proto_value]
    elif isinstance(proto_value, dict):
        # Standard dict
        return {k: convert_proto_to_python(v) for k, v in proto_value.items()}
    # Handle non-dict iterables (e.g., RepeatedComposite) generically
    elif isinstance(proto_value, Iterable) and not isinstance(proto_value, (str, bytes)):
        try:
            return [convert_proto_to_python(v) for v in list(proto_value)]
        except Exception:
            return proto_value
    else:
        # Primitive value (str, int, float, bool, None)
        return proto_value


def execute_function_call(function_call):
    """
    Execute function call from Gemini

    Senior Dev: Generic execution, delegates to UniversalDataFetcher
    """
    function_name = function_call.name
    params = convert_proto_to_python(dict(function_call.args))

    # Convert float parameters to int where needed
    if 'limit' in params and isinstance(params['limit'], float):
        params['limit'] = int(params['limit'])

    # Map sector abbreviations to full names
    sector_mapping = {
        'IT': 'Information Technology',
        'FMCG': 'Fast Moving Consumer Goods',
        'Auto': 'Automobile',
        'Pharma': 'Pharmaceuticals'
    }

    if 'filters' in params and isinstance(params['filters'], dict):
        if 'sector' in params['filters']:
            sector = params['filters']['sector']
            params['filters']['sector'] = sector_mapping.get(sector, sector)

    # Pre-execution ticker resolution and validation (runs BEFORE any tool calls)
    try:
        if function_name == 'resolve_ticker':
            inp = params.get('input')
            if not isinstance(inp, str) or not inp.strip():
                return {"error": "invalid_input"}
            res = fetcher.ticker_resolver.resolve_any(inp.strip())
            return convert_proto_to_python(res)
        def _is_wildcard(sym: str) -> bool:
            return isinstance(sym, str) and ('%' in sym)

        def _resolve_ticker_or_error(input_symbol: str) -> Dict[str, Any]:
            resolution = fetcher.ticker_resolver.resolve_any(input_symbol)
            if not (resolution.get('resolved_ticker') or resolution.get('resolved_index_name')):
                return {
                    'error': 'ticker_unresolved',
                    'message': f"Ticker/Index '{input_symbol}' could not be resolved.",
                    'suggestions': resolution.get('metadata', {}).get('suggestions', []),
                    'last_seen': resolution.get('metadata', {}).get('last_seen'),
                    'resolution': resolution
                }
            return resolution

        resolution_meta = None

        # Functions with explicit single ticker
        if function_name in ('fetch_stock_data', 'calculate_indicators', 'query_corporate_actions', 'stock_quote', 'stock_quote_fno'):
            symbol = params.get('ticker') or params.get('symbol')
            if isinstance(symbol, str) and symbol.strip():
                res = _resolve_ticker_or_error(symbol.strip())
                if res.get('error') == 'ticker_unresolved':
                    return res
                if isinstance(res.get('confidence'), int) and res['confidence'] < 50:
                    return {
                        'error': 'low_confidence_resolution',
                        'message': f"Resolution confidence {res['confidence']}% is below threshold for '{symbol}'. Please confirm the intended symbol.",
                        'suggestions': res.get('metadata', {}).get('suggestions', []),
                        'last_seen': res.get('metadata', {}).get('last_seen'),
                        'resolution': res
                    }
                # Get resolved symbol from either resolved_ticker (stocks/ETFs) or resolved_index_name (indices)
                resolved = res.get('resolved_ticker') or res.get('resolved_index_name')
                resolution_meta = {
                    'original': symbol,
                    'resolved': resolved,
                    'method': res['resolution_method'],
                    'confidence': res['confidence'],
                    'note': res.get('metadata', {}).get('note', '')
                }
                params['ticker'] = resolved
                params.pop('symbol', None)

        # Query with filters for a single or list of symbols
        elif function_name == 'query_stocks':
            # Senior Dev: Create working copy early so ticker resolution can use it
            pn = dict(params)
            filters = params.get('filters') or {}
            filter_key = None
            if 'symbol' in filters:
                filter_key = 'symbol'
            elif 'ticker' in filters:
                filter_key = 'ticker'
            elif 'company_name' in filters:
                filter_key = 'company_name'

            if filter_key:
                sym_val = filters[filter_key]

                # Single string symbol
                if isinstance(sym_val, str):
                    if not _is_wildcard(sym_val):
                        res = _resolve_ticker_or_error(sym_val.strip())
                        if res.get('error') == 'ticker_unresolved':
                            return res
                        if res.get('resolved_index_name'):
                            idx = res['resolved_index_name']
                            resolution_meta = {
                                'original': sym_val,
                                'resolved': idx,
                                'method': res['resolution_method'],
                                'confidence': res['confidence'],
                                'note': res.get('metadata', {}).get('note', '')
                            }
                            filters.pop('symbol', None)
                            filters.pop('ticker', None)
                            filters['index_name'] = idx
                            pn['table'] = 'market_indices'
                        else:
                            resolved = res['resolved_ticker']
                            resolution_meta = {
                                'original': sym_val,
                                'resolved': resolved,
                                'method': res['resolution_method'],
                                'confidence': res['confidence'],
                                'note': res.get('metadata', {}).get('note', '')
                            }
                            filters['symbol'] = resolved
                            filters.pop('ticker', None)
                        params['filters'] = filters

                # List of symbols
                elif isinstance(sym_val, list):
                    verified: List[str] = []
                    verified_indices: List[str] = []
                    verified_equities: List[str] = []
                    items_meta: List[Dict[str, Any]] = []
                    unresolved_items: List[Dict[str, Any]] = []

                    for item in sym_val:
                        if not isinstance(item, str):
                            continue
                        raw = item.strip()
                        if not raw or _is_wildcard(raw):
                            # Keep wildcards unchanged (though rare in list form)
                            verified.append(raw)
                            items_meta.append({
                                'original': raw,
                                'resolved': raw,
                                'method': 'wildcard_or_passthrough',
                                'confidence': 100,
                                'note': 'Wildcard or passthrough item'
                            })
                            continue

                        res = _resolve_ticker_or_error(raw)
                        if res.get('error') == 'ticker_unresolved':
                            unresolved_items.append({
                                'original': raw,
                                'suggestions': res.get('suggestions', []),
                                'last_seen': res.get('last_seen')
                            })
                            items_meta.append({
                                'original': raw,
                                'resolved': None,
                                'method': 'not_found',
                                'confidence': 0,
                                'note': 'Could not resolve to active ticker'
                            })
                        else:
                            if isinstance(res.get('confidence'), int) and res['confidence'] < 50:
                                unresolved_items.append({
                                    'original': raw,
                                    'suggestions': res.get('metadata', {}).get('suggestions', []),
                                    'last_seen': res.get('metadata', {}).get('last_seen')
                                })
                                items_meta.append({
                                    'original': raw,
                                    'resolved': None,
                                    'method': res['resolution_method'],
                                    'confidence': res['confidence'],
                                    'note': 'Low confidence resolution omitted'
                                })
                            else:
                                if res.get('resolved_index_name'):
                                    idx = res['resolved_index_name']
                                    verified.append(idx)
                                    verified_indices.append(idx)
                                    items_meta.append({
                                        'original': raw,
                                        'resolved': idx,
                                        'method': res['resolution_method'],
                                        'confidence': res['confidence'],
                                        'note': res.get('metadata', {}).get('note', '')
                                    })
                                else:
                                    sym = res['resolved_ticker']
                                    verified.append(sym)
                                    verified_equities.append(sym)
                                    items_meta.append({
                                        'original': raw,
                                        'resolved': sym,
                                        'method': res['resolution_method'],
                                        'confidence': res['confidence'],
                                        'note': res.get('metadata', {}).get('note', '')
                                    })

                    if len([m for m in items_meta if m['resolved'] is not None]) == 0:
                        return {
                            'error': 'ticker_unresolved_list',
                            'message': 'None of the provided tickers could be resolved to active symbols.',
                            'unresolved': unresolved_items,
                            'suggestions': list({s for u in unresolved_items for s in (u.get('suggestions') or [])})[:10]
                        }

                    # Mixed list handling: run separate queries for equities and indices, then merge
                    verified_equities = list(dict.fromkeys(verified_equities))
                    verified_indices = list(dict.fromkeys(verified_indices))
                    combined_result = None
                    if verified_indices:
                        pn_idx = dict(pn)
                        f_idx = dict(filters)
                        f_idx.pop('symbol', None)
                        f_idx.pop('ticker', None)
                        f_idx['index_name'] = verified_indices
                        pn_idx['filters'] = f_idx
                        pn_idx['table'] = 'market_indices'
                        idx_result = fetcher.query_stocks(**pn_idx)
                    else:
                        idx_result = None
                    if verified_equities:
                        pn_eq = dict(pn)
                        f_eq = dict(filters)
                        f_eq['symbol'] = verified_equities
                        f_eq.pop('ticker', None)
                        f_eq.pop('index_name', None)
                        pn_eq['filters'] = f_eq
                        eq_result = fetcher.query_stocks(**pn_eq)
                    else:
                        eq_result = None
                    if idx_result is not None or eq_result is not None:
                        r_eq = (eq_result or {}).get('results', []) if isinstance(eq_result, dict) else []
                        r_idx = (idx_result or {}).get('results', []) if isinstance(idx_result, dict) else []
                        combined = r_eq + r_idx
                        combined_result = {
                            'results': combined,
                            'count': len(combined),
                            'source': (eq_result or idx_result or {}).get('source', 'sqlite'),
                            'table': 'mixed',
                            'tags': ['mixed_equity_index'],
                            'timestamp': datetime.now().isoformat()
                        }
                        params['__combined_query_result'] = combined_result
                    else:
                        filters['symbol'] = verified_equities
                        if 'ticker' in filters:
                            filters.pop('ticker', None)
                        if 'company_name' in filters:
                            filters.pop('company_name', None)
                        params['filters'] = filters

                    resolution_meta = {
                        'items': items_meta,
                        'note': 'List ticker resolution applied (unresolved items omitted from query)'
                    }
                else:
                    # Coerce iterable or list-like strings to list
                    coerced = None
                    try:
                        if isinstance(sym_val, str) and sym_val.strip().startswith('[') and sym_val.strip().endswith(']'):
                            parsed = ast.literal_eval(sym_val)
                            if isinstance(parsed, list):
                                coerced = parsed
                        elif isinstance(sym_val, Iterable) and not isinstance(sym_val, (str, bytes)):
                            coerced = list(sym_val)
                        elif isinstance(sym_val, str) and (',' in sym_val):
                            coerced = [s.strip() for s in sym_val.split(',') if s.strip()]
                    except Exception:
                        coerced = None

                    if coerced is not None:
                        # Re-enter list branch by setting and continuing
                        filters[filter_key] = coerced
                        params['filters'] = filters
                        # Perform minimal resolution by recursion (reuse logic via setting type)
                        # Execution will pass through the 'list of symbols' branch on next call cycle
                        pass

        # Pragmatic remap: handle 'stock_quote' variants with supported tool
        if function_name in ('stock_quote', 'stock_quote_fno'):
            result = fetcher.fetch_stock_data(ticker=params['ticker'], components=['fundamentals'])
            # Normalize to raw_results-style envelope for frontend renderer (structure-driven)
            out = {}
            if isinstance(result, dict):
                data = result.get('data')
                if isinstance(data, list):
                    recs = data
                elif isinstance(data, dict):
                    recs = [data]
                else:
                    recs = []
                out = {
                    'results': recs,
                    'count': len(recs),
                    'source': result.get('metadata', {}).get('source') or 'live',
                    'table': 'live_quote',
                    'tags': ['live_quote', 'single_stock'],
                    'timestamp': datetime.now().isoformat()
                }
                if resolution_meta:
                    out['ticker_resolution'] = resolution_meta
            else:
                out = {
                    'results': [],
                    'count': 0,
                    'source': 'live',
                    'table': 'live_quote',
                    'tags': ['live_quote', 'single_stock'],
                    'timestamp': datetime.now().isoformat()
                }
            return convert_proto_to_python(out)

        # Execute supported functions with possibly-resolved ticker(s)
        if function_name == 'query_stocks':
            # pn already created at start of query_stocks block (line 151)
            filters = pn.get('filters', {}) if isinstance(pn.get('filters'), dict) else {}
            if 'symbol' in pn:
                sym = pn.pop('symbol')
                filters['symbol'] = sym
            if 'ticker' in pn:
                sym = pn.pop('ticker')
                filters['symbol'] = sym
            # Ensure any non-list iterable becomes list for IN clause
            if isinstance(filters.get('symbol'), Iterable) and not isinstance(filters.get('symbol'), (list, tuple, str, bytes)):
                filters['symbol'] = list(filters['symbol'])
            pn['filters'] = filters
            cq = pn.pop('__combined_query_result', None)
            if cq is not None:
                result = cq
            else:
                result = fetcher.query_stocks(**pn)
        elif function_name == 'calculate_indicators':
            result = fetcher.calculate_indicators(**params)
        elif function_name == 'get_option_chain':
            tk = params.get('ticker') or params.get('symbol')
            if not isinstance(tk, str) or not tk.strip():
                return {"error": "invalid_input"}
            res = _resolve_ticker_or_error(tk.strip())
            if res.get('error') == 'ticker_unresolved':
                return res
            key = res.get('resolved_index_name') or res.get('resolved_ticker')
            limit = params.get('limit') if isinstance(params.get('limit'), int) else None
            atm_window = params.get('atm_window') if isinstance(params.get('atm_window'), int) else None
            oc = fetcher.fetch('option_chain', {'symbol': key})
            data = oc.get('data') if isinstance(oc, dict) else None
            results = []
            expiry_dates = []
            underlying_value = None
            if isinstance(data, dict):
                rec = data.get('records') or data
                expiry_dates = rec.get('expiryDates') or []
                items = rec.get('data') or []
                for it in items:
                    sp = it.get('strikePrice')
                    ed = it.get('expiryDate')
                    ce = it.get('CE') or {}
                    pe = it.get('PE') or {}
                    uv = ce.get('underlyingValue') or pe.get('underlyingValue')
                    if uv and not underlying_value:
                        underlying_value = uv
                    results.append({
                        'strikePrice': sp,
                        'expiryDate': ed,
                        'CE': {
                            'lastPrice': ce.get('lastPrice'),
                            'openInterest': ce.get('openInterest'),
                            'changeinOpenInterest': ce.get('changeinOpenInterest'),
                            'impliedVolatility': ce.get('impliedVolatility')
                        },
                        'PE': {
                            'lastPrice': pe.get('lastPrice'),
                            'openInterest': pe.get('openInterest'),
                            'changeinOpenInterest': pe.get('changeinOpenInterest'),
                            'impliedVolatility': pe.get('impliedVolatility')
                        }
                    })
            if isinstance(underlying_value, (int, float)) and results:
                try:
                    results = sorted(results, key=lambda r: abs(float(r.get('strikePrice') or 0) - float(underlying_value)))
                    if isinstance(atm_window, int) and atm_window > 0:
                        center = 0
                        if results:
                            center_item = results[0]
                            center_strike = center_item.get('strikePrice') or 0
                            around = [r for r in results if abs(float(r.get('strikePrice') or 0) - float(center_strike)) <= atm_window]
                            results = around
                except Exception:
                    pass
            if isinstance(limit, int) and limit > 0 and results:
                results = results[:limit]
            out = {
                'results': results,
                'count': len(results),
                'source': (oc.get('metadata', {}).get('source') if isinstance(oc, dict) else 'unknown'),
                'table': 'option_chain',
                'tags': ['option_chain'],
                'timestamp': datetime.now().isoformat(),
                'expiryDates': expiry_dates,
                'underlyingValue': underlying_value
            }
            result = out
        elif function_name == 'fetch_any':
            qt = params.get('query_type')
            sub = params.get('params') if isinstance(params.get('params'), dict) else {}
            if not isinstance(qt, str) or not qt.strip():
                return {"error": "invalid_input"}
            result = fetcher.fetch(qt.strip(), sub)
        elif function_name == 'query_corporate_actions':
            result = fetcher.query_corporate_actions(**params)
        elif function_name == 'fetch_stock_data':
            result = fetcher.fetch_stock_data(**params)
        else:
            raise ValueError(f"Unknown function: {function_name}")

        # Attach resolution metadata if we resolved before calling fetcher
        if resolution_meta and isinstance(result, dict):
            result['ticker_resolution'] = resolution_meta

        return convert_proto_to_python(result)
    except Exception as e:
        print(f"[ERROR] Function execution failed: {e}")
        return {'error': str(e)}


# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global fetcher, gemini_model
    logger.info("=" * 80)
    logger.info("STOCK MARKET AI BACKEND - STARTING UP")
    logger.info("=" * 80)
    # Database location comes from config (DB_PATH env var, repo-relative default).
    db_path = str(config.DB_PATH)
    csv_dir = str(config.CSV_DIRECTORY)
    logger.info(f"-> Database: {db_path}")
    logger.info(f"-> CSV directory: {csv_dir}")
    if not config.DB_PATH.exists():
        message = (
            f"Database not found at {db_path}.\n"
            "Build a starter database with:\n"
            "    python scripts/bootstrap_db.py --sample\n"
            "or point DB_PATH at an existing file in your .env.\n"
            "See docs/REBUILD.md for the full rebuild procedure."
        )
        logger.error(message)
        raise FileNotFoundError(message)
    fetcher = UniversalDataFetcher(db_path, csv_dir)
    logger.info(f"-> Data fetcher ready (database: {fetcher.db_path})")
    try:
        api_key = config.require_gemini_key()
    except RuntimeError as exc:
        logger.error(str(exc))
        raise
    genai.configure(api_key=api_key)
    logger.info("-> Gemini API configured")


    gemini_model = genai.GenerativeModel(
        model_name=config.GEMINI_MODEL,
        tools=[{'function_declarations': FUNCTION_DECLARATIONS}],
        system_instruction=SYSTEM_PROMPT,
        generation_config=GenerationConfig(
            temperature=0.1,  # Lower temperature = less hallucination, more deterministic
            top_p=0.8,
            top_k=40,
            max_output_tokens=5000,  # Prevent runaway generation
        )
    )
    logger.info(f"-> Gemini model ready ({config.GEMINI_MODEL} with {len(FUNCTION_DECLARATIONS)} functions)")
    logger.info("BACKEND READY - Listening for requests...")
    print("=" * 80 + "\n")
    try:
        yield
    finally:
        print("\n" + "=" * 80)
        print("SHUTTING DOWN BACKEND...")
        print("=" * 80)
        if fetcher:
            fn = getattr(fetcher, "close", None)
            if callable(fn):
                fn()
                print("Data fetcher closed successfully")

# FASTAPI APP INITIALIZATION
# ============================================================================

app = FastAPI(
    title="Stock Market AI API",
    description="Generic API for Indian stock market data with LLM integration",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware (allow frontend to call backend)
app.add_middleware(
    CORSMiddleware,
    # Explicit origins only: "*" is invalid alongside allow_credentials=True.
    # Configure via the CORS_ORIGINS env var (comma-separated).
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# GLOBAL INSTANCES (Initialized at startup)
# ============================================================================

fetcher = None
gemini_model = None


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class ConversationMessage(BaseModel):
    """Represents a single message in conversation history.

    Uses the Gemini-style format where `parts` is a list of strings
    (the LLM/chat system may split content into multiple parts).
    """
    role: str
    parts: Optional[List[str]] = None


class ChatRequest(BaseModel):
    """
    Generic chat request model

    Senior Dev: No hardcoded query types, just user's natural language
    """
    query: str
    conversation_history: Optional[List[ConversationMessage]] = None

    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "query": "What is the PE ratio of TCS?",
                "conversation_history": [
                    {"role": "user", "parts": ["Show me TCS details"]},
                    {"role": "model", "parts": ["TCS current price is ₹3,245.50..."]}
                ]
            }
        ]
    })


class ChatResponse(BaseModel):
    """
    Standardized response format for frontend

    Senior Dev: Generic structure that works for ANY query type
    Frontend detects data structure and adapts display
    """
    response: str  # LLM's natural language response
    raw_results: Optional[Dict[str, Any]] = None  # Structured data (if applicable)
    metadata: Optional[Dict[str, Any]] = None  # Additional info (latency, source, etc.)

    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {
                "response": "TCS (Tata Consultancy Services) current price is ₹3,245.50 with PE ratio of 28.45x",
                "raw_results": {
                    "results": [{"symbol": "TCS", "current_price": 3245.50, "pe_ratio": 28.45}],
                    "count": 1,
                    "source": "sqlite",
                    "timestamp": "2025-01-10T12:00:00"
                },
                "metadata": {
                    "latency_ms": 245,
                    "function_called": "query_stocks"
                }
            }
        ]
    })


# ============================================================================
# STARTUP/SHUTDOWN EVENTS
# ============================================================================

async def _shutdown_event_legacy():
    """Cleanup on server shutdown"""
    global fetcher

    print("\n" + "=" * 80)
    print("SHUTTING DOWN BACKEND...")
    print("=" * 80)

    if fetcher:
        fn = getattr(fetcher, "close", None)
        if callable(fn):
            fn()
            print("Data fetcher closed successfully")


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Stock Market AI Backend",
        "version": "1.0.0",
        "endpoints": {
            "chat": "/api/chat",
            "health": "/health"
        }
    }


@app.get("/health")
async def health_check():
    """
    Detailed health check

    Senior Dev: Verify all components are working
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "components": {}
    }

    # Check data fetcher
    try:
        if fetcher:
            test_query = fetcher.query_stocks(filters={'symbol': 'TCS'}, limit=1)
            health_status["components"]["data_fetcher"] = {
                "status": "healthy",
                "database_accessible": test_query.get('count', 0) > 0
            }
        else:
            health_status["components"]["data_fetcher"] = {"status": "not_initialized"}
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["components"]["data_fetcher"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "degraded"

    # Check Gemini model
    try:
        if gemini_model:
            health_status["components"]["gemini_llm"] = {"status": "healthy"}
        else:
            health_status["components"]["gemini_llm"] = {"status": "not_initialized"}
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["components"]["gemini_llm"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "degraded"

    return health_status


# ============================================================================
# HELPER FUNCTIONS FOR LLM CONTEXT
# ============================================================================

def build_system_context(fetcher) -> Dict[str, Any]:
    """
    Build dynamic system context with current date/time
    
    Senior Dev: Zero hardcoding - all values computed at runtime
    Injects metadata into chat history for LLM to use in date filters
    
    Args:
        fetcher: UniversalDataFetcher instance with sqlite_last_updated
        
    Returns:
        Dict with 'role' and 'parts' for Gemini chat history
    """
    now = datetime.now()
    
    return {
        'role': 'user',
        'parts': [f"""[SYSTEM CONTEXT - AUTOMATIC]
Current Date: {now.strftime('%Y-%m-%d')}
Current Time: {now.strftime('%H:%M:%S IST')}
Current Day: {now.strftime('%A')}
Database Last Updated: {fetcher.sqlite_last_updated}
Historical Data Range: 2010-01-01 to {fetcher.sqlite_last_updated}
Market Hours: 09:15-15:30 IST (Monday-Friday)

Use this context for all date-based queries."""]
    }


def validate_response_with_llm(
    query: str,
    response_text: str,
    function_called: Optional[str],
    raw_results: Optional[Dict]
) -> Dict[str, Any]:
    """
    Use LLM to validate its own response
    
    Senior Dev: Zero hardcoding - LLM is best judge of its own output
    
    Args:
        query: Original user query
        response_text: LLM's text response
        function_called: Name of function called (if any)
        raw_results: Function results (if any)
        
    Returns:
        {
            'requires_data': bool,
            'function_was_called': bool,
            'response_is_valid': bool,
            'reason': str
        }
    """
    # Build validation prompt
    validation_prompt = f"""You are a quality control system analyzing your own previous response.

Original user query: "{query}"
Your response: "{response_text}"
Function called: {function_called if function_called else "None"}
Function returned data: {bool(raw_results and raw_results.get('count', 0) > 0)}

Analyze if your response is valid by answering these questions:

1. Does the user query require retrieving data from a database or API?
   (e.g., stock prices, financial data, market indices, company info)
   
2. If yes, did you call a function to retrieve that data?

3. Is your response valid and accurate?
   - If query needs data and you called function: Valid
   - If query needs data but you didn't call function: Invalid
   - If query doesn't need data (greeting, explanation): Valid

Respond with ONLY a JSON object (no markdown, no explanation):
{{
    "requires_data": true or false,
    "function_was_called": true or false,
    "response_is_valid": true or false,
    "reason": "brief explanation of why valid or invalid"
}}"""

    try:
        # Use a separate model instance for validation to avoid chat history pollution
        validation_model = genai.GenerativeModel(
            model_name=config.GEMINI_MODEL,
            generation_config=GenerationConfig(
                temperature=0.0,  # Deterministic validation
                response_mime_type="application/json"
            )
        )
        
        # Get validation
        validation_response = validation_model.generate_content(validation_prompt)
        
        # Parse JSON response
        import json
        validation = json.loads(validation_response.text)
        
        return validation
        
    except Exception as e:
        logger.error(f"[VALIDATION ERROR] Failed to validate response: {e}")
        # Fallback: assume response is valid if validation fails
        return {
            'requires_data': False,
            'function_was_called': bool(function_called),
            'response_is_valid': True,
            'reason': f'Validation error: {str(e)}'
        }


# ============================================================================
# API ENDPOINTS
# ============================================================================


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    MAIN CHAT ENDPOINT - Works for ANY natural language query
    """
    if not gemini_model or not fetcher:
        raise HTTPException(
            status_code=503,
            detail="Backend components not initialized. Please try again."
        )

    try:
        start_time = datetime.now()
        logger.info(f"[CHAT] Query: {request.query}")

        # Prepare conversation history
        # If using ConversationMessage pydantic models, convert them to plain dicts
        if request.conversation_history:
            chat_history = [m.dict() if hasattr(m, 'dict') else m for m in request.conversation_history]
        else:
            chat_history = []

        # Inject dynamic system context (date/time) for hallucination prevention
        # Only inject when starting new conversation
        if not request.conversation_history:
            context_msg = build_system_context(fetcher)
            model_acknowledgment = {
                'role': 'model',
                'parts': ['Understood. I will use this context for date-based queries.']
            }
            chat_history = [context_msg, model_acknowledgment]
        
        # Start chat session with Gemini
        chat = gemini_model.start_chat(history=chat_history)

        # Send user query to Gemini
        # Senior Dev: Safe logging without Unicode errors
        try:
            print(f"\n[CHAT] User Query: {request.query}")
        except UnicodeEncodeError:
            print(f"\n[CHAT] User Query: [Contains Unicode characters]")

        response = chat.send_message(request.query)
        llm_response_text = ""
        raw_results = None
        function_called = None
        # Initialize response data
        

        # Senior Dev: Multi-turn function calling support
        # Gemini might return multiple function calls in sequence
        max_function_turns = 5  # Prevent infinite loops
        current_turn = 0

        while current_turn < max_function_turns:
            current_turn += 1
            has_function_call = False

            if response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        has_function_call = True
                        function_call = part.function_call
                        function_called = function_call.name

                        print(f"[CHAT] Turn {current_turn}: LLM chose function: {function_called}")
                        try:
                            print(f"[CHAT] Parameters: {dict(function_call.args)}")
                        except UnicodeEncodeError:
                            print(f"[CHAT] Parameters: [Contains Unicode characters]")

                        function_result = execute_function_call(function_call)

                        # Short-circuit on unresolved ticker(s)
                        if isinstance(function_result, dict) and function_result.get('error') in ('ticker_unresolved', 'ticker_unresolved_list'):
                            raw_results = function_result
                            if function_result['error'] == 'ticker_unresolved':
                                suggestions = function_result.get('suggestions') or []
                                last_seen = function_result.get('last_seen')
                                msg = "Ticker could not be resolved. "
                                if last_seen:
                                    msg += f"Last seen on {last_seen}. "
                                if suggestions:
                                    # Senior Dev: Handle suggestions being list of dicts
                                    sug_strs = [s.get('symbol', str(s)) if isinstance(s, dict) else str(s) for s in suggestions]
                                    msg += f"Did you mean: {', '.join(sug_strs[:5])}?"
                                else:
                                    msg += "Please check the symbol and try again."
                                llm_response_text = msg
                            else:
                                # List unresolved
                                unresolved = function_result.get('unresolved') or []
                                suggestions = function_result.get('suggestions') or []
                                msg = "None of the provided tickers could be resolved. "
                                if unresolved:
                                    bad = ', '.join([u['original'] for u in unresolved][:5])
                                    msg += f"Unresolved: {bad}. "
                                if suggestions:
                                    sug_strs = [s.get('symbol', str(s)) if isinstance(s, dict) else str(s) for s in suggestions]
                                    msg += f"Suggestions: {', '.join(sug_strs[:5])}"
                                llm_response_text = msg

                            has_function_call = False
                            break

                        raw_results = function_result

                        print(f"[CHAT] Sending function result back to LLM...")
                        safe_function_result = make_json_safe(function_result)
                        response = chat.send_message({
                            'function_response': {
                                'name': function_called,
                                'response': safe_function_result
                            }
                        })
                        break

                    elif hasattr(part, 'text') and part.text:
                        llm_response_text = part.text
                        try:
                            print(f"[CHAT] LLM Response: {llm_response_text[:100]}...")
                        except UnicodeEncodeError:
                            print(f"[CHAT] LLM Response: [Contains Unicode characters]")
                        break

            if not has_function_call:
                break

        # If we hit max turns, log warning
        if current_turn >= max_function_turns:
            print(f"[WARNING] Max function call turns ({max_function_turns}) reached")

        # ====================================================================
        # LLM SELF-VALIDATION AND AUTO-RETRY (Zero Hardcoding)
        # ====================================================================
        # Senior Dev: Let LLM validate itself instead of hardcoded rules
        
        validation_result = None
        retry_attempted = False
        retry_succeeded = False
        
        # Only validate if LLM responded with text but didn't call function
        if llm_response_text and not function_called:
            try:
                # Ask LLM to validate its own response
                validation_result = validate_response_with_llm(
                    query=request.query,
                    response_text=llm_response_text,
                    function_called=function_called,
                    raw_results=raw_results
                )
                
                logger.info(f"[VALIDATION] Result: {validation_result}")
                
                # Check if retry is needed
                needs_retry = (
                    validation_result['requires_data'] and 
                    not validation_result['function_was_called'] and 
                    not validation_result['response_is_valid']
                )
                
                if needs_retry:
                    retry_attempted = True
                    logger.info(f"[AUTO-RETRY] Triggered. Reason: {validation_result['reason']}")
                    
                    # Build retry instruction using LLM's own reasoning
                    retry_instruction = f"""[SYSTEM CORRECTION]

Your previous response was invalid.
Validation result: {validation_result['reason']}

Analysis:
- User query requires data: {validation_result['requires_data']}
- You called a function: {validation_result['function_was_called']}
- Response is valid: {validation_result['response_is_valid']}

To answer correctly, you MUST:
1. Call the appropriate function (query_stocks, fetch_stock_data, calculate_indicators, etc.)
2. Wait for the function results
3. Use those results to answer the user's question

User query: {request.query}

Please retry and call the necessary function."""
                    
                    # Execute retry (reuse same multi-turn logic)
                    retry_response = chat.send_message(retry_instruction)
                    retry_llm_text = ""
                    retry_raw_results = None
                    retry_function_called = None
                    
                    # Process retry response (same multi-turn loop)
                    retry_turn = 0
                    max_retry_turns = 3
                    
                    while retry_turn < max_retry_turns:
                        retry_turn += 1
                        has_retry_function_call = False
                        
                        if retry_response.candidates[0].content.parts:
                            for part in retry_response.candidates[0].content.parts:
                                if hasattr(part, 'function_call') and part.function_call:
                                    has_retry_function_call = True
                                    retry_function_called = part.function_call.name
                                    
                                    logger.info(f"[AUTO-RETRY] Function called: {retry_function_called}")
                                    
                                    # Execute function
                                    retry_raw_results = execute_function_call(part.function_call)
                                    
                                    # Send result back to LLM
                                    safe_retry_result = make_json_safe(retry_raw_results)
                                    retry_response = chat.send_message({
                                        'function_response': {
                                            'name': retry_function_called,
                                            'response': safe_retry_result
                                        }
                                    })
                                    break
                                    
                                elif hasattr(part, 'text') and part.text:
                                    retry_llm_text = part.text
                                    break
                        
                        if not has_retry_function_call:
                            break
                    
                    # If retry succeeded (function was called), use retry results
                    if retry_function_called:
                        llm_response_text = retry_llm_text
                        raw_results = retry_raw_results
                        function_called = retry_function_called
                        retry_succeeded = True
                        logger.info(f"[AUTO-RETRY] SUCCESS - Used function: {retry_function_called}")
                    else:
                        logger.warning(f"[AUTO-RETRY] FAILED - Retry also didn't call function")
                        
            except Exception as e:
                logger.error(f"[AUTO-RETRY] Error during validation/retry: {e}")
                # Continue with original response if validation/retry fails

        # Calculate latency
        end_time = datetime.now()
        latency_ms = int((end_time - start_time).total_seconds() * 1000)

        # Senior Dev: Ensure raw_results is fully JSON-serializable
        # Final safety conversion to handle any remaining protobuf objects
        if raw_results is not None:
            raw_results = json.loads(json.dumps(raw_results, default=str))

        # Hallucination detection monitoring (pattern-based, user-first: log only, don't block)
        if raw_results is None and llm_response_text:
            import re
            # Check if response contains financial/data patterns without calling functions
            has_structured_data = bool(re.search(r'₹\s*[\d,]+|RS\s*[\d,]+|\d+\.\d+%|\d+\s*Cr|\d+\s*L', llm_response_text))
            has_table = '\t' in llm_response_text
            
            if has_structured_data or has_table:
                logger.warning(
                    f"[HALLUCINATION DETECTED] Response contains data but no function was called.\n"
                    f"Query: {request.query}\n"
                    f"Response preview: {llm_response_text[:200]}"
                )
                # Don't block user - just log for monitoring and improvement

        interval = None
        date_range = None
        try:
            rr = raw_results or {}
            if isinstance(rr, dict):
                tbl = rr.get('table')
                recs = rr.get('results') or []
                if tbl == 'daily_ohlc':
                    interval = '1 day'
                elif recs and any(any('date' in str(k).lower() for k in r.keys()) for r in recs):
                    interval = '1 day'
                from datetime import datetime as _dt
                dmin = None
                dmax = None
                for r in recs:
                    for k, v in r.items():
                        if isinstance(v, str) and any(x in str(k).lower() for x in ['date','timestamp']):
                            try:
                                d = _dt.fromisoformat(v.replace('Z','').split(' ')[0])
                            except Exception:
                                try:
                                    d = _dt.strptime(v[:10], '%Y-%m-%d')
                                except Exception:
                                    d = None
                            if d:
                                dmin = d if dmin is None or d < dmin else dmin
                                dmax = d if dmax is None or d > dmax else dmax
                            break
                def _fmt(d: _dt):
                    try:
                        return d.strftime('%d %b %Y')
                    except Exception:
                        return 'Date not available'
                if dmin and dmax:
                    s1 = _fmt(dmin)
                    s2 = _fmt(dmax)
                    date_range = s1 if s1 == s2 else f"{s1} – {s2}"
                elif dmax:
                    date_range = _fmt(dmax)
        except Exception:
            pass

        # Generic contract enforcement: if data exists but no tab block was produced, append a minimal tab-separated table
        def _has_tab_block(text: str) -> bool:
            try:
                return ('\t' in (text or '')) and any('\n' in seg for seg in (text or '').split('\t'))
            except Exception:
                return False

        def _synthesize_tab_table(rr: Dict[str, Any], max_rows: int = 10) -> str:
            try:
                results = rr.get('results') or []
                if not results:
                    return ''
                first = results[0] if isinstance(results[0], dict) else {}
                keys = list(first.keys())
                # Pick columns by semantics: date/time first if available
                date_cols = [k for k in keys if any(tok in str(k).lower() for tok in ['date','time','timestamp'])]
                # Prefer common series columns next
                common = ['open','high','low','close','volume','rsi','macd']
                common_present = [c for c in common if c in keys]
                # Fallback: include first textual identifier column
                ident = next((k for k in keys if not isinstance(first.get(k), (int,float))), None)
                cols = []
                cols.extend(date_cols[:1])
                if ident and ident not in cols:
                    cols.append(ident)
                for c in common_present:
                    if c not in cols:
                        cols.append(c)
                # Fill remaining with first few keys to keep generic (limit total 6)
                for k in keys:
                    if len(cols) >= 6:
                        break
                    if k not in cols:
                        cols.append(k)
                header = '\t'.join([str(c).replace('_',' ').title() for c in cols])
                lines = [header]
                for row in results[:max_rows]:
                    vals = []
                    for c in cols:
                        v = row.get(c)
                        vals.append(str(v) if v is not None else '')
                    lines.append('\t'.join(vals))
                return '\n'.join(lines)
            except Exception:
                return ''

        if raw_results and isinstance(raw_results, dict) and (raw_results.get('count') or 0) > 0:
            # Append only if the narrative lacks any tab-separated table
            if not _has_tab_block(llm_response_text):
                tab_text = _synthesize_tab_table(raw_results)
                if tab_text:
                    llm_response_text = (llm_response_text or '')
                    if llm_response_text and not llm_response_text.endswith('\n'):
                        llm_response_text += '\n\n'
                    llm_response_text += tab_text

        # Fallback guard: ensure we never return an empty narrative when no function was called
        if not (llm_response_text or "").strip():
            llm_response_text = (
                "Unable to generate an answer right now. Try refining your query which help the Dalal Street AI to get the answer"
            )

        # Build response
        response_data = ChatResponse(
            response=llm_response_text,
            raw_results=raw_results,
            metadata={
                "latency_ms": latency_ms,
                "function_called": function_called,
                "timestamp": end_time.isoformat(),
                "interval": interval or "N/A",
                "date_range": date_range or "Date not available",
                # Auto-retry metadata (zero hardcoding - tracks LLM self-validation)
                "retry_attempted": retry_attempted if 'retry_attempted' in locals() else False,
                "retry_succeeded": retry_succeeded if 'retry_succeeded' in locals() else False,
                "validation_reason": validation_result['reason'] if validation_result else None
            }
        )

        logger.info(f"[CHAT] Completed in {latency_ms}ms | function={function_called or 'none'} | size={ (raw_results or {}).get('count', 0) }")
        return response_data

    except Exception as e:
        logger.exception(f"[ERROR] Chat endpoint failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal server error",
                "message": str(e),
                "type": type(e).__name__
            }
        )


# ============================================================================
# UTILITY ENDPOINTS (For debugging/testing)
# ============================================================================

@app.get("/api/stocks/search")
async def search_stocks(query: str, limit: int = 10):
    """
    Search stocks by symbol or company name

    Senior Dev: Utility endpoint for autocomplete/search features
    """
    if not fetcher:
        raise HTTPException(status_code=503, detail="Data fetcher not initialized")

    try:
        # Search by symbol (starts with)
        symbol_results = fetcher.query_stocks(
            filters={'symbol': f"{query.upper()}%"},
            fields=['symbol', 'company_name', 'sector'],
            limit=limit
        )

        # Search by company name (contains)
        name_results = fetcher.query_stocks(
            filters={'company_name': f"%{query}%"},
            fields=['symbol', 'company_name', 'sector'],
            limit=limit
        )

        # Combine and deduplicate
        all_results = symbol_results.get('results', []) + name_results.get('results', [])
        unique_results = {r['symbol']: r for r in all_results}.values()

        return {
            "query": query,
            "results": list(unique_results)[:limit],
            "count": len(unique_results)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/functions")
async def list_functions():
    """
    List available function declarations

    Senior Dev: For debugging/documentation purposes
    """
    return {
        "functions": [
            {
                "name": func['name'],
                "description": func['description']
            }
            for func in FUNCTION_DECLARATIONS
        ]
    }


# ============================================================================
# ADMIN ENDPOINTS (bearer-token protected)
# ============================================================================

async def require_admin_token(
    authorization: Optional[str] = Header(default=None),
) -> None:
    """
    Guard the /admin/* endpoints with a shared bearer token.

    Set ADMIN_TOKEN in the environment to enable them. When ADMIN_TOKEN is
    empty (the default) the endpoints answer 404 rather than 403, so an
    unconfigured deployment does not advertise that they exist.
    """
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="Not Found")

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Compare as bytes: secrets.compare_digest rejects non-ASCII str inputs.
    if not secrets.compare_digest(
        token.strip().encode("utf-8"), config.ADMIN_TOKEN.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid admin token")


@app.post("/admin/update/bhavcopy", dependencies=[Depends(require_admin_token)])
async def admin_update_bhavcopy(date: Optional[str] = None):
    try:
        db_path = str(config.DB_PATH)
        downloader = BhavcopyDownloader(db_path)
        from datetime import datetime
        dt = datetime.strptime(date, '%Y-%m-%d') if date else None
        result = downloader.update_daily(dt)
        return {
            "status": "ok",
            "result": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/update/corporate_actions", dependencies=[Depends(require_admin_token)])
async def admin_update_corporate_actions(limit: Optional[int] = None):
    if CorporateActionsIngester is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "Corporate actions ingestion is not available: "
                "src.data_fetcher.corporate_actions_ingester could not be imported."
            ),
        )
    try:
        db_path = str(config.DB_PATH)
        csv_dir = str(config.CSV_DIRECTORY)
        import glob
        files = sorted(glob.glob(os.path.join(csv_dir, 'CF-CA-*.csv')))
        csv_path = files[-1] if files else None
        ing = CorporateActionsIngester(db_path, csv_path)
        result = ing.ingest(limit=limit)
        ing.close()
        return {
            "status": "ok",
            "result": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/update/resolver_cache", dependencies=[Depends(require_admin_token)])
async def admin_update_resolver_cache():
    try:
        if not fetcher:
            raise HTTPException(status_code=503, detail="Data fetcher not initialized")
        fetcher.ticker_resolver.refresh_cache()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Configure structured logging to file and console
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
repo_root = os.path.dirname(project_root)
logs_dir = os.path.join(repo_root, 'logs')
os.makedirs(logs_dir, exist_ok=True)

logger = logging.getLogger("backend")
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

file_handler = RotatingFileHandler(
    os.path.join(logs_dir, "backend.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# Route uvicorn logs to the same file/console
for name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
    uvlog = logging.getLogger(name)
    uvlog.setLevel(logging.DEBUG)
    if not uvlog.handlers:
        uvlog.addHandler(file_handler)
        uvlog.addHandler(console_handler)

# Place after app initialization
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Log method/path and request duration
    start = datetime.now()
    try:
        response = await call_next(request)
    finally:
        duration_ms = (datetime.now() - start).total_seconds() * 1000
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} [{duration_ms:.1f}ms]")
    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Capture full stack trace for unexpected errors
    logger.exception(f"Unhandled error at {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "message": str(exc), "path": str(request.url.path)},
    )
def make_json_safe(obj):
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [make_json_safe(v) for v in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return obj
    return obj


# ============================================================================
# RUN SERVER (For development)
#
# Must stay at the very end of this module: everything above (the logger, the
# middleware, the exception handler) has to be defined before we hand the app
# to uvicorn.
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 80)
    print("STARTING STOCK MARKET AI BACKEND")
    print(f"Binding to http://{config.API_HOST}:{config.API_PORT}")
    print("=" * 80 + "\n")

    uvicorn.run(
        "server:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=config.DEV_RELOAD,
        log_level=config.LOG_LEVEL,
    )
