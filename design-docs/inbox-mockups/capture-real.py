from playwright.sync_api import sync_playwright
import subprocess
import time
import os

# Start server
server = subprocess.Popen(
    ["python3", "-m", "http.server", "9998"],
    cwd="/Users/lijixiang/note-app/design-docs/inbox-mockups",
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
)
time.sleep(2)

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 520, "height": 960})
        
        files = [
            ("real-color-header.html", "real-color-header.png"),
            ("real-soft-pastel.html", "real-soft-pastel.png"),
            ("real-ref-image.html", "real-ref-image.png"),
            ("real-new-ref.html", "real-new-ref.png"),
        ]
        
        for html, out in files:
            page.goto(f"http://localhost:9998/{html}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            # Screenshot the iphone element (includes notch, status bar, tab bar)
            phone = page.locator(".iphone")
            phone.screenshot(path=f"/Users/lijixiang/note-app/design-docs/inbox-mockups/{out}")
            print(f"Captured {out}")
        
        browser.close()
finally:
    server.terminate()
    server.wait()
