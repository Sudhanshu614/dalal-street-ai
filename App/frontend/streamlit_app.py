"""
Streamlit Frontend for Stock Market AI
Senior Dev Pattern: Conversation-first interface with generic display

Zero Hardcoding: No hardcoded queries, display adapts to response structure
Frontend detects data structure and chooses appropriate visualization

Reference: FROM_SCRATCH_DOCS/FRONTEND_ARCHITECTURE.md Part 6
"""

import streamlit as st
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime
import sys
import os
import time
try:
    from htbuilder.units import rem
    from htbuilder import div, styles
    HTBUILDER_AVAILABLE = True
except Exception:
    HTBUILDER_AVAILABLE = False

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from display_components import display_response, show_suggestion_pills

# ============================================================================
# CONFIGURATION
# ============================================================================

# Backend API endpoint
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
CHAT_ENDPOINT = f"{API_BASE_URL}/api/chat"

# Page configuration
st.set_page_config(page_title="Dalal Street AI", page_icon="🧠")

# Minimal CSS: hide deploy/menu controls and keep dividers subtle
st.markdown(
    """
    <style>
      [data-testid='stToolbar'], [data-testid='stHeaderActionMenu'], [data-testid='stAppDeployButton'], [data-testid='stMainMenu']{display:none !important;}
      [data-testid='stDivider'] hr{border:0 !important; border-top:1px solid #e5e7eb !important;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================================
# SESSION STATE INITIALIZATION
# ============================================================================

def initialize_session_state():
    """
    Initialize Streamlit session state

    Senior Dev: Maintain conversation history across reruns
    """
    if 'conversation_history' not in st.session_state:
        st.session_state.conversation_history = []

    if 'messages' not in st.session_state:
        st.session_state.messages = []

    if 'backend_healthy' not in st.session_state:
        st.session_state.backend_healthy = None
    if 'initial_question' not in st.session_state:
        st.session_state.initial_question = None
    if 'selected_suggestion' not in st.session_state:
        st.session_state.selected_suggestion = None
    if 'prev_response_timestamp' not in st.session_state:
        st.session_state.prev_response_timestamp = datetime.fromtimestamp(0)
    if 'prev_question_timestamp' not in st.session_state:
        st.session_state.prev_question_timestamp = datetime.fromtimestamp(0)
    if 'busy' not in st.session_state:
        st.session_state.busy = False


# ============================================================================
# BACKEND HEALTH CHECK
# ============================================================================

def check_backend_health() -> bool:
    """
    Check if backend is healthy

    Senior Dev: Verify backend availability before showing UI
    """
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            health_data = response.json()
            return health_data.get('status') == 'healthy'
        return False
    except Exception as e:
        print(f"Backend health check failed: {e}")
        return False


# ============================================================================
# API CALL FUNCTIONS
# ============================================================================

def send_chat_message(user_query: str, conversation_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Send chat message to backend API

    Senior Dev: Generic API call, works for ANY query
    Zero Hardcoding: No query type detection, backend handles everything

    Args:
        user_query: User's natural language question
        conversation_history: Previous conversation (for context)

    Returns:
        Response dict with 'response', 'raw_results', 'metadata'
    """
    try:
        payload = {
            "query": user_query,
            "conversation_history": conversation_history or []
        }

        response = requests.post(
            CHAT_ENDPOINT,
            json=payload,
            timeout=150  # 150 second timeout for LLM + data fetching
        )

        if response.status_code == 200:
            return response.json()
        else:
            # Return error in standard format
            error_detail = response.json().get('detail', 'Unknown error')
            return {
                "response": f"❌ Backend error: {error_detail}",
                "raw_results": None,
                "metadata": {
                    "error": True,
                    "status_code": response.status_code
                }
            }

    except requests.exceptions.Timeout:
        return {
            "response": "⏱️ Request timed out. Please try again with a simpler query.",
            "raw_results": None,
            "metadata": {"error": True, "type": "timeout"}
        }
    except requests.exceptions.ConnectionError:
        return {
            "response": "🔌 Cannot connect to backend. Please ensure the API server is running.",
            "raw_results": None,
            "metadata": {"error": True, "type": "connection"}
        }
    except Exception as e:
        return {
            "response": f"❌ Unexpected error: {str(e)}",
            "raw_results": None,
            "metadata": {"error": True, "type": type(e).__name__}
        }


# ============================================================================
# UI COMPONENTS
# ============================================================================

def show_header():
    if HTBUILDER_AVAILABLE:
        st.html(div(style=styles(font_size=rem(5), line_height=1))["❉"])
    else:
        st.markdown("<div style='font-size:2.5rem;line-height:1'>❉</div>", unsafe_allow_html=True)
    header_row = st.container(horizontal=True, vertical_alignment="bottom")
    with header_row:
        st.title("Dalal Street AI")
        if st.session_state.get('messages'):
            if st.button(":blue[:material/cached:] Restart", type="secondary"):
                st.session_state.messages = []
                st.session_state.conversation_history = []
                st.session_state.initial_question = None
                st.session_state.selected_suggestion = None
                st.session_state.prev_response_timestamp = datetime.fromtimestamp(0)
                st.session_state.prev_question_timestamp = datetime.fromtimestamp(0)
                st.rerun()


def show_setup_help():
    """
    First-run guidance shown when the backend cannot be reached.

    This is the screen a brand-new user hits, so it must name the actual causes
    rather than just reporting "disconnected". A backend that refuses to start
    is almost always one of two things: no Gemini API key, or no database.
    """
    st.error(f"Cannot reach the backend at `{API_BASE_URL}`")

    st.markdown(
        "**This is expected on a fresh clone.** The backend needs two things "
        "before it will start, and neither ships with the repository."
    )

    st.markdown("#### 1. A Gemini API key")
    st.markdown(
        "The app sends your question to Google Gemini to work out what you are asking. "
        "Get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), then:"
    )
    st.code("cp .env.example .env      # Windows: Copy-Item .env.example .env\n"
            "# open .env and set GEMINI_API_KEY=your-key-here", language="bash")

    st.markdown("#### 2. A database")
    st.markdown(
        "The repository ships no market data — see `docs/DATA.md` for why. Build one:"
    )
    st.code("python scripts/bootstrap_db.py --sample     # small, a few minutes\n"
            "python scripts/verify_db.py                 # confirm it worked", language="bash")
    st.caption("For the full 30-year warehouse, follow `docs/REBUILD.md`.")

    st.markdown("#### 3. Start the backend")
    st.code("python -m uvicorn App.api.server:app --host 127.0.0.1 --port 8000", language="bash")
    st.markdown(
        "Read the backend's terminal output — if something is missing it says exactly what. "
        "Then reload this page."
    )

    st.divider()
    st.caption(
        "Still stuck? Open an issue: "
        "https://github.com/Sudhanshu614/dalal-street-ai/issues"
    )

    if st.button("Retry connection"):
        st.session_state.backend_healthy = None
        st.rerun()


def show_backend_status():
    """
    Show backend connection status

    Senior Dev: Transparent system health for user trust
    """
    if st.session_state.backend_healthy:
        st.sidebar.success("✅ Backend Connected")
    else:
        st.sidebar.error("❌ Backend Disconnected")
        st.sidebar.warning(
            f"Cannot connect to backend at {API_BASE_URL}. "
            "Start the API server from the repository root:\n\n"
            "```bash\n"
            "python -m uvicorn App.api.server:app --port 8000\n"
            "```\n\n"
            "If it exits immediately, read its output — a missing "
            "`GEMINI_API_KEY` or database is the usual cause."
        )


SUGGESTIONS = {
    ":blue[:material/local_library:] What analysis can you provide?": (
        "Hey, what information can Dalal Street AI provide about any specific stock? Give me the answer in a detailed manner."
    ),
    ":green[:material/balance:] Compare TCS and Infosys": (
        "Compare TCS and Infosys in terms of their stock prices, fundamentals, and performance."
    ),
    ":orange[:material/currency_rupee:] Show me the current price of Zomato": (
        "Tell me what the live current price of Zomato is"
    ),
    ":violet[:material/candlestick_chart:] What indicators can you provide?": (
        "What indicators can you provide? Give me the answer in a detailed manner."
    ),
    ":red[:material/campaign:] Recent corporate actions for MRF?": (
        "What are the recent corporate actions for MRF?"
    ),
}

import re
def _clean_label(label: str) -> str:
    s = re.sub(r"^:\w+\[\s*:\s*material\/[^\]]+\s*:\s*\]", "", label)
    s = s.strip()
    return s

CLEAN_SUGGESTIONS = { _clean_label(k): v for k, v in SUGGESTIONS.items() }

def show_initial_ui():
    st.markdown("<div class='home-wrap'>", unsafe_allow_html=True)
    st.chat_input("Ask a question...", key="initial_question")
    selected = st.pills(
        label="Examples",
        label_visibility="collapsed",
        options=SUGGESTIONS.keys(),
        key="selected_suggestion",
    )
    st.caption("Dalal Street AI is for analysis only, not investment advice.")    
    st.markdown("</div>", unsafe_allow_html=True)

def show_disclaimer_dialog():
    st.caption(
        "Powered by Google Gemini and 30 years of NSE market data. Answers may be inaccurate. Do not enter private or regulated data."
    )


def display_chat_message(role: str, content: Dict[str, Any]):
    """
    Display a chat message

    Senior Dev: Generic message display, adapts to content type

    Args:
        role: 'user' or 'assistant'
        content: For user: str, For assistant: response dict
    """
    with st.chat_message(role):
        if role == "user":
            st.text(content)
        else:
            display_response(content)


def show_chat_interface():
    """
    Show conversation interface

    Senior Dev: Generic chat pattern, works for ANY query type
    Zero Hardcoding: All display logic delegated to display_response()
    """
    # Display conversation history
    for message in st.session_state.messages:
        display_chat_message(message['role'], message['content'])

    # Chat input (hidden while busy)
    user_query = None
    if not st.session_state.busy:
        st.markdown('<div data-testid="stChatInput">', unsafe_allow_html=True)
        user_query = st.chat_input("Ask a follow-up...")
        st.markdown('</div>', unsafe_allow_html=True)

    # Handle suggestion selection like demo
    if not user_query and st.session_state.initial_question:
        user_query = st.session_state.initial_question
        st.session_state.initial_question = None
    if not user_query and st.session_state.selected_suggestion:
        cleaned = _clean_label(st.session_state.selected_suggestion)
        user_query = CLEAN_SUGGESTIONS.get(cleaned, cleaned)

    # Process user query
    if user_query and not st.session_state.busy:
        # Add user message to conversation
        user_sanitized = user_query.replace("$", r"\$")
        st.session_state.messages.append({
            'role': 'user',
            'content': user_sanitized
        })

        # Display user message immediately
        display_chat_message('user', user_sanitized)

        # Show loading indicator
        st.session_state.busy = True
        with st.spinner("Waiting..."):
            now = datetime.now()
            delta = (now - st.session_state.prev_question_timestamp).total_seconds()
            st.session_state.prev_question_timestamp = now
            if delta < 3:
                time.sleep(3 - delta)
        with st.chat_message('assistant'):
            st.caption("Thinking...")
        with st.spinner("Researching..."):
            response = send_chat_message(user_sanitized, st.session_state.conversation_history)
        st.session_state.prev_response_timestamp = datetime.now()

        # Add assistant response to conversation
        st.session_state.messages.append({
            'role': 'assistant',
            'content': response
        })

        display_chat_message('assistant', response)
        show_feedback_controls(len(st.session_state.messages))

        # Update conversation history for backend (Gemini format)
        # Senior Dev: Maintain context for multi-turn conversations
        st.session_state.conversation_history.append({
            'role': 'user',
            'parts': [user_query]
        })

        # Only add model response if not an error
        if not response.get('metadata', {}).get('error'):
            st.session_state.conversation_history.append({
                'role': 'model',
                'parts': [response.get('response','')]
            })

        st.session_state.busy = False
        st.rerun()

def show_feedback_controls(message_index: int):
    st.write("")
    with st.popover("How did I do?"):
        with st.form(key=f"feedback-{message_index}", border=False):
            with st.container(gap=None):
                st.markdown(":small[Rating]")
                _ = st.feedback(options="stars")
            _ = st.text_area("More information (optional)")
            _ = st.checkbox("Include chat history with my feedback", True)
            if st.form_submit_button("Send feedback"):
                send_telemetry()

def send_telemetry(**kwargs):
    pass


def show_sidebar():
    """
    Show sidebar with controls and info

    Senior Dev: Keep sidebar minimal, focus on conversation
    """
    with st.sidebar:
        st.markdown("### ⚙️ Settings")

        # Backend status
        show_backend_status()

        st.divider()

        st.markdown("### 💬 Conversation")

        if st.button("🗑️ Clear Conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.conversation_history = []
            st.rerun()
        st.button("Legal disclaimer", type="tertiary", on_click=show_disclaimer_dialog)

        # Show conversation stats
        if st.session_state.messages:
            user_messages = sum(1 for m in st.session_state.messages if m['role'] == 'user')
            st.caption(f"Messages: {len(st.session_state.messages)} ({user_messages} queries)")

        st.divider()

        # App info
        st.markdown("### ℹ️ About")
        st.caption(
            "**Stock Market AI** v1.0\n\n"
            "Powered by Gemini 2.5 Flash\n\n"
            "Built with zero hardcoding - works for ANY query!"
        )

        # Show API endpoint (for debugging)
        with st.expander("🔧 Debug Info"):
            st.code(f"API: {API_BASE_URL}")
            st.caption(f"Status: {'✅ Connected' if st.session_state.backend_healthy else '❌ Disconnected'}")


# ============================================================================
# MAIN APP LOGIC
# ============================================================================

def main():
    """
    Main application entry point

    Senior Dev: Clean separation of concerns
    - Health check
    - Session initialization
    - UI routing (initial vs chat interface)
    """
    # Initialize session state
    initialize_session_state()

    # Check backend health (only once per session)
    if st.session_state.backend_healthy is None:
        with st.spinner("Connecting to backend..."):
            st.session_state.backend_healthy = check_backend_health()

    show_header()

    # If the backend is unreachable, say why in the page itself rather than
    # letting the user type a question into a dead app. The two causes that
    # account for nearly every first-run failure are a missing API key and a
    # missing database, so name both explicitly.
    if st.session_state.backend_healthy is False:
        show_setup_help()
        st.stop()

    # Main UI: Initial view vs Chat view
    user_first_interaction = bool(st.session_state.initial_question or st.session_state.selected_suggestion)
    has_message_history = bool(st.session_state.messages)
    if not user_first_interaction and not has_message_history:
        st.session_state.messages = []
        show_initial_ui()
        st.stop()
    show_chat_interface()


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main()

# Feedback and telemetry omitted per requirements
