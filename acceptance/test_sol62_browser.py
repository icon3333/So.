#!/usr/bin/env python3
"""User-centric, local-only browser acceptance for the unified So reader.

Run: python3 acceptance/test_sol62_browser.py
Uses synthetic fixtures, a real local server subprocess, and headless Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from sol62_fixture import isolated_environment, make_fixture

ROOT = Path(__file__).resolve().parents[1]


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def find_chrome():
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if shutil.which(name):
            return shutil.which(name)
    for path in (Path.home() / ".cache/chrome-for-testing/chrome-linux64/chrome", Path("/usr/bin/chromium"), Path("/opt/google/chrome/chrome")):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise RuntimeError("Chromium/Chrome unavailable")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("Python websocket-client unavailable") from exc
    chrome = find_chrome()
    with tempfile.TemporaryDirectory(prefix="so-reader-acceptance-", ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        home, prod = make_fixture(base)
        env = isolated_environment(home, prod)
        port = free_port()
        origin = f"http://127.0.0.1:{port}"
        server = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        browser = ws = None
        errors, requests = [], []
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("local server exited before readiness")
                try:
                    with urllib.request.urlopen(origin + "/", timeout=1) as r:
                        require(r.status == 200, "reader not served")
                    break
                except Exception:
                    time.sleep(.1)
            else:
                raise RuntimeError("local server readiness timeout")

            with urllib.request.urlopen(origin + "/api/cockpit", timeout=3) as r:
                api = json.load(r)
            fixture_rows = [row for group in api.values() for row in group.get("items", [])]
            require(len(fixture_rows) >= 40, f"synthetic fixture should contain mixed rows, got {len(fixture_rows)}")

            browser = subprocess.Popen([chrome, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--no-proxy-server", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", f"--user-data-dir={base / 'chrome-profile'}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            ws_url = ""
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                line = browser.stderr.readline()
                if "DevTools listening on" in line:
                    ws_url = line.split("DevTools listening on", 1)[1].strip()
                    break
                if browser.poll() is not None:
                    break
            require(ws_url, "Chrome DevTools startup timeout")
            authority = ws_url.removeprefix("ws://").split("/", 1)[0]
            req = urllib.request.Request("http://" + authority + "/json/new?about:blank", method="PUT")
            with urllib.request.urlopen(req, timeout=3) as r:
                target = json.load(r)
            ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=5, suppress_origin=True)
            msg_id = 0

            def cdp(method, params=None):
                nonlocal msg_id
                msg_id += 1
                ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                while True:
                    response = json.loads(ws.recv())
                    method_name = response.get("method")
                    p = response.get("params", {})
                    if method_name == "Runtime.exceptionThrown":
                        details = p.get("exceptionDetails", {})
                        exception = details.get("exception", {})
                        errors.append(details.get("text") or exception.get("description") or exception.get("value") or "uncaught page exception")
                    elif method_name == "Log.entryAdded":
                        entry = p.get("entry", {})
                        if entry.get("level") == "error": errors.append(entry.get("text", "console error"))
                    elif method_name == "Network.requestWillBeSent":
                        requests.append({"url": p.get("request", {}).get("url", ""), "method": p.get("request", {}).get("method", "GET")})
                    if response.get("id") == msg_id:
                        if "error" in response:
                            raise AssertionError(f"CDP {method} failed: {response['error']}")
                        return response.get("result", {})

            def js(expr):
                result = cdp("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True, "userGesture": True})
                if result.get("exceptionDetails"):
                    raise AssertionError(result["exceptionDetails"].get("text", "page expression failed"))
                return result.get("result", {}).get("value")

            def until(expr, label, timeout=8):
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    try:
                        if js(expr): return
                    except Exception:
                        pass
                    time.sleep(.08)
                raise AssertionError(f"Timed out waiting for {label}; page errors={errors}; page={js('({title:document.title,url:location.href,text:document.body.innerText.slice(0,1200)})')}")

            cdp("Runtime.enable"); cdp("Log.enable"); cdp("Network.enable"); cdp("Page.enable")
            cdp("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
            cdp("Page.navigate", {"url": origin + "/"})
            until("document.querySelector('#article-list .article-row') !== null", "unified reader rows")
            require(js("document.querySelector('#article-list') !== null"), "reader must use #article-list")
            require(js("document.querySelector('#items') === null"), "legacy second list model unexpectedly present")
            require(js("document.querySelectorAll('#article-list > .article-row').length > 0"), "original reader item class not rendered")
            desktop = js("({count:document.querySelectorAll('#article-list > .article-row').length, first:document.querySelector('#article-list > .article-row')?.innerText, bottom:document.querySelector('#article-list > .article-row')?.getBoundingClientRect().bottom})")
            require(desktop["count"] <= 10 and desktop["count"] > 0, f"first page should show a bounded round: {desktop}")

            # No action below is allowed to call a source mutation endpoint.
            source_mutations = []
            def inspect_network():
                for entry in requests:
                    path = urllib.parse.urlparse(entry["url"]).path
                    if entry["method"] not in ("GET", "HEAD") and ("inoreader" in path.lower() or "x/" in path.lower() or "source" in path.lower()):
                        source_mutations.append(entry)

            # Filtered batch selection spans all matching rows, not just page one.
            js("document.querySelector('#search').value='Synthetic reading';document.querySelector('#search').dispatchEvent(new Event('input',{bubbles:true}))")
            until("document.querySelectorAll('#article-list > .article-row').length > 0", "search results")
            js("document.querySelector('#select-filtered').click()")
            selected = js("({count:document.querySelectorAll('#article-list .article-row input[type=checkbox]:checked').length, label:document.querySelector('#selection-label').textContent, total:document.querySelector('#result-count').textContent})")
            require("16 gefiltert ausgewählt" in selected["label"].lower(), f"filtered selection label should report all 16 filtered rows (including page two): {selected}")
            require(selected["count"] <= 10, f"only the current page is rendered; checked DOM rows should not exceed page size: {selected}")
            js("document.querySelector('#search').value='';document.querySelector('#search').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-status=all]').click()")
            until("document.querySelectorAll('#article-list > .article-row').length > 0", "unfiltered list after selection")
            js("document.querySelector('#next-page').click()")
            require(js("document.querySelector('#page-label').textContent.includes('2 von')"), "second page navigation should render")
            page_two = js("({count:document.querySelectorAll('#article-list > .article-row').length, label:document.querySelector('#page-label').textContent})")
            require(page_two["count"] > 0 and page_two["label"].startswith('Runde 2'), f"second page is not populated: {page_two}")
            # Restore to page one; navigation intentionally clears round selection.
            js("document.querySelector('#previous-page').click()")
            js("document.querySelector('#page-size').value='500';document.querySelector('#page-size').dispatchEvent(new Event('change',{bubbles:true}))")

            # A source-neutral decision is local triage; it must not reach an origin write endpoint.
            js("document.querySelector('[data-batch=keep]').click()")
            until("document.querySelectorAll('#article-list > .article-row').length > 0", "list after local triage")
            inspect_network()
            require(not source_mutations, f"source mutation request during local triage: {source_mutations}")

            # Load the all-items view before local triage; source rows do not have local-hide actions.
            js("document.querySelector('[data-status=all]').click()")
            js("document.querySelector('#search').value='';document.querySelector('#search').dispatchEvent(new Event('input',{bubbles:true}))")
            js("document.querySelector('#kind-filter').selectedIndex=0;document.querySelector('#kind-filter').dispatchEvent(new Event('change',{bubbles:true}))")
            js("document.querySelector('#page-size').value='500';document.querySelector('#page-size').dispatchEvent(new Event('change',{bubbles:true}))")
            local_row = js("[...document.querySelectorAll('#article-list > .article-row')].find(r=>r.querySelector('[data-action=local-hide]'))?.dataset.id")
            require(local_row, "synthetic fixture must include a local cockpit row")
            first_id = local_row
            js("[...document.querySelectorAll('#article-list > .article-row')].find(r=>r.dataset.id===" + json.dumps(first_id) + ").querySelector('[data-action=local-hide]').click()")
            until("!document.querySelector('#article-list > .article-row') || document.querySelector('#article-list > .article-row').dataset.id !== " + json.dumps(first_id), "row hidden")
            require(js("!!document.querySelector('#local-undo')"), "local undo action should be available")
            js("document.querySelector('[data-status=all]').click()")
            until("document.querySelectorAll('#article-list > .article-row').length > 0", "rerender after hide")
            require(js("![...document.querySelectorAll('#article-list > .article-row')].some(r=>r.dataset.id===" + json.dumps(first_id) + ")"), "hidden local row should stay hidden across rerender")
            js("document.querySelector('#local-undo').click()")
            until("[...document.querySelectorAll('.article-row')].some(r=>r.dataset.id===" + json.dumps(first_id) + ")", "undo restores row")
            inspect_network()
            require(not source_mutations, f"hide/undo made source mutation request: {source_mutations}")

            # Use control-driven local area assignment and local hide/undo.
            require(js("!!document.querySelector('#area-entry') && !!document.querySelector('#area-assign')"), "local area controls missing")
            area_entry = local_row
            require(area_entry, "expected a local cockpit area entry in the synthetic fixture")
            js("document.querySelector('#article-list > .article-row input[type=checkbox]').click()")
            js("document.querySelector('#area-entry').value='Research';document.querySelector('#area-assign').click()")
            until("Array.from(document.querySelector('#area-filter').options).some(o=>o.value==='Research')", 'area persisted and offered')
            js("document.querySelector('#local-hide').click()")
            require(js("!!document.querySelector('#local-undo')"), "local undo control missing")
            js("document.querySelector('#local-undo').click()")
            # Exercise handoff with selection and a note; clipboard/share action is UI-only here.
            js("document.querySelector('#article-list > .article-row input[type=checkbox]').click()")
            until("!document.querySelector('#hermes-note').disabled", "handoff note enabled")
            js("document.querySelector('#hermes-note').value='Synthetic handoff context';document.querySelector('#hermes-note').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#share-selected').click()")
            inspect_network()
            require(not source_mutations, f"local controls made a source mutation request: {source_mutations}")
            unexpected = [r for r in requests if urllib.parse.urlparse(r['url']).netloc not in ('', urllib.parse.urlparse(origin).netloc)]
            require(not unexpected, f"browser contacted non-local network: {unexpected}")

            # Desktop-to-mobile first-row user-visible geometry and no horizontal scroll.
            cdp("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
            mobile = js("({width:document.documentElement.scrollWidth,viewport:innerWidth,first:!!document.querySelector('#article-list > .article-row'),bottom:document.querySelector('#article-list > .article-row')?.getBoundingClientRect().bottom,focusable:[...document.querySelectorAll('#article-list button,#article-list a,#article-list input')].filter(e=>!e.disabled).length})")
            require(mobile["width"] <= 390 and mobile["viewport"] == 390, f"mobile horizontal overflow: {mobile}")
            require(mobile["first"] and mobile["bottom"] <= 844 and mobile["focusable"] > 0, f"first item/actions not usable on mobile: {mobile}")
            require(not errors, f"page errors: {errors}")
            print(json.dumps({"status":"PASS", "synthetic_rows":len(fixture_rows), "desktop":desktop, "mobile":mobile, "network_requests":len(requests), "source_mutations":source_mutations}, ensure_ascii=False))
        except Exception:
            print(json.dumps({"status":"FAIL", "page_errors":errors, "requests":requests[-30:]}, ensure_ascii=False), file=sys.stderr)
            raise
        finally:
            if ws:
                try: ws.close()
                except Exception: pass
            if browser:
                browser.terminate()
                try: browser.wait(timeout=5)
                except subprocess.TimeoutExpired: browser.kill()
            server.terminate()
            try: server.wait(timeout=5)
            except subprocess.TimeoutExpired: server.kill()


if __name__ == "__main__":
    main()
