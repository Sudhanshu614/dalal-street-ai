import time
import json
from playwright.sync_api import sync_playwright

def find_chat(ctx):
    # Prefer label for E2E hook
    try:
        loc = ctx.get_by_label("E2E Chat")
        if loc and loc.count() > 0:
            return loc.first
    except Exception:
        pass
    # Try container with E2E Chat text
    try:
        panel = ctx.locator("div").filter(has_text="E2E Chat").first
        if panel and panel.count() > 0:
            inner = panel.locator("input, textarea, [contenteditable='true']").first
            if inner and inner.count() > 0:
                return inner
    except Exception:
        pass
    for ph in ["E2E Chat", "Ask anything about Indian stocks...", "Ask a follow-up...", "Ask a question..."]:
        try:
            loc = ctx.get_by_placeholder(ph)
            if loc and loc.count() > 0:
                return loc.first
        except Exception:
            pass
    cont = ctx.locator("div[data-testid='stChatInput']")
    if cont.count() > 0:
        chat = cont.locator("input, textarea").last
        if chat.count() > 0:
            return chat
    # Fallback to any input/textarea
    chat = ctx.locator("input, textarea").last
    if chat.count() > 0:
        return chat
    # Try contenteditable inputs
    ce = ctx.locator("[contenteditable='true']").last
    if ce.count() > 0:
        return ce
    # Final fallback: role textbox
    boxes = ctx.get_by_role("textbox")
    if boxes.count() > 0:
        return boxes.nth(boxes.count() - 1)
    return None

def send(page, q):
    # Use iframe if present
    ctx = page
    try:
        if page.locator("iframe").count() > 0:
            ctx = page.frame_locator("iframe").first
    except Exception:
        ctx = page
    prev = ctx.locator("div[data-testid='stChatMessage']").count()
    chat = find_chat(ctx)
    if chat is None:
        # Try clicking chat container and typing via keyboard
        cont = ctx.locator("div[data-testid='stChatInput']")
        if cont.count() > 0:
            try:
                cont.first.click()
                t0 = time.time()
                page.keyboard.type(q)
                page.keyboard.press("Enter")
                lat = None
                for _ in range(120):
                    time.sleep(0.5)
                    cnt = ctx.locator("div[data-testid='stChatMessage']").count()
                    if cnt > prev:
                        lat = int((time.time() - t0) * 1000)
                        break
                df = ctx.locator("div[data-testid='stDataFrame']").count() > 0
                preview = ""
                try:
                    msgs = ctx.locator("div[data-testid='stChatMessage']")
                    last = msgs.nth(msgs.count() - 1)
                    preview = last.inner_text()[:200]
                except Exception:
                    pass
                return {"query": q, "latency_ms": lat, "has_table": df, "preview": preview}
            except Exception:
                return {"query": q, "error": "chat_input_not_found"}
        else:
            return {"query": q, "error": "chat_input_not_found"}
    try:
        chat.wait_for(state="visible", timeout=10000)
    except Exception:
        pass
    t0 = time.time()
    chat.fill(q)
    try:
        (ctx.get_by_role("button", name="E2E Send") if hasattr(ctx, 'get_by_role') else page.get_by_role("button", name="E2E Send")).click()
    except Exception:
        chat.press("Enter")
    lat = None
    for _ in range(120):
        time.sleep(0.5)
        cnt = ctx.locator("div[data-testid='stChatMessage']").count()
        if cnt > prev:
            lat = int((time.time() - t0) * 1000)
            break
    df = ctx.locator("div[data-testid='stDataFrame']").count() > 0
    preview = ""
    try:
        msgs = ctx.locator("div[data-testid='stChatMessage']")
        last = msgs.nth(msgs.count() - 1)
        preview = last.inner_text()[:200]
    except Exception:
        pass
    return {"query": q, "latency_ms": lat, "has_table": df, "preview": preview}

def main():
    queries = [
        "Hello",
        "What is the current price of TCS?",
        "Show me Wipro RSI and MACD",
        "Compare TCS, INFY, WIPRO",
        "Compare AXISBANK, SBIN, HDFCBANK",
        "Banks with PE < 20 and ROE > 15 among HDFCBANK, ICICIBANK, KOTAKBANK",
        "Top 5 IT stocks by market cap",
        "TCS dividend history",
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8501/?e2e=true&debug=true", timeout=60000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        # Debug: list available data-testid attributes
        try:
            testids = page.evaluate("Array.from(document.querySelectorAll('[data-testid]')).map(e=>e.getAttribute('data-testid'))")
            print(json.dumps({"data_testids": list(dict.fromkeys(testids))}, ensure_ascii=False))
        except Exception:
            pass
        # If chat input not present initially, click a suggestion button to start conversation
        if find_chat(page) is None:
            try:
                buttons = page.get_by_role("button")
                for i in range(min(20, buttons.count())):
                    b = buttons.nth(i)
                    txt = b.inner_text()
                    if txt and ("Restart" not in txt) and ("Legal" not in txt):
                        b.click()
                        time.sleep(0.5)
                        break
            except Exception:
                pass
        out = []
        for q in queries:
            out.append(send(page, q))
        print(json.dumps(out, ensure_ascii=False))
        browser.close()

if __name__ == "__main__":
    main()
