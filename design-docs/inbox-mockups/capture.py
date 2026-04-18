from playwright.sync_api import sync_playwright
import subprocess
import time
import os

# Start server
server = subprocess.Popen(
    ["python3", "-m", "http.server", "9999"],
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
            ("style-color-header.html", "mockup-color-header.png"),
            ("style-soft-pastel.html", "mockup-soft-pastel.png"),
            ("style-ref-image.html", "mockup-ref-image.png"),
        ]
        
        for html, out in files:
            page.goto(f"http://localhost:9999/{html}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            # Screenshot the phone element
            phone = page.locator(".phone")
            phone.screenshot(path=f"/Users/lijixiang/note-app/design-docs/inbox-mockups/{out}")
            print(f"Captured {out}")
        
        browser.close()
finally:
    server.terminate()
    server.wait()
