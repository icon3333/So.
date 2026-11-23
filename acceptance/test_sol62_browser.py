#!/usr/bin/env python3
"""Local-only Sol 6.2 browser acceptance against synthetic data.

Run from the checkout with:
    python3 acceptance/test_sol62_browser.py

Needs an installed Chromium/Chrome and Python websocket-client. It makes no
external requests: loopback is the sole allowed destination and the browser is
launched with proxying disabled. Runs against an isolated local dataset.
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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from sol62_fixture import isolated_environment, make_fixture

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def find_chrome():
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    for path in (Path.home() / ".cache/chrome-for-testing/chrome-linux64/chrome", Path("/usr/bin/chromium"), Path("/opt/google/chrome/chrome")):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise RuntimeError("Chromium/Chrome unavailable; install it to run browser acceptance")


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
    with tempfile.TemporaryDirectory(prefix="so-sol62-acceptance-") as td:
        base = Path(td)
        home, prod = make_fixture(base)
        env = isolated_environment(home, prod)
        port = free_port()
        origin = f"http://127.0.0.1:{port}"
        # subprocess uses the real application entrypoint and the isolated fixture.
        server = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome_proc = None
        ws = None
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("local app exited before readiness")
                try:
                    with urllib.request.urlopen(origin + "/cockpit", timeout=1) as response:
                        require(response.status == 200, "cockpit root not served")
                        break
                except Exception:
                    time.sleep(.1)
            else:
                raise RuntimeError("local app did not become ready")

            with urllib.request.urlopen(origin + "/api/cockpit", timeout=3) as response:
                api = json.load(response)
            got = [item for source in api.values() for item in source.get("items", [])]
            require(len(got) > 40, f"fixture must expose more than one page of mixed rows, got {len(got)}")
            require(len({item.get("source") for item in got}) >= 3, "fixture did not cover 3+ sources")

            profile = base / "chrome-profile"
            chrome_proc = subprocess.Popen([
                chrome, "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
                "--no-proxy-server", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True)
            ws_url = ""
            ws_deadline = time.monotonic() + 15
            require(chrome_proc is not None and chrome_proc.stderr is not None, "Chrome stderr pipe unavailable")
            while time.monotonic() < ws_deadline:
                line = chrome_proc.stderr.readline()
                if "DevTools listening on" in line:
                    ws_url = line.strip().split("DevTools listening on", 1)[1].strip()
                    break
                if chrome_proc.poll() is not None:
                    break
            require(ws_url, "Chrome DevTools endpoint did not start")
            # Create a page target through the local DevTools HTTP endpoint.
            debugger_authority = ws_url.replace("ws://", "", 1).split("/", 1)[0]
            target_url = "http://" + debugger_authority + "/json/new?" + urllib.parse.quote(origin + "/cockpit")
            request = urllib.request.Request(target_url, method="PUT")
            with urllib.request.urlopen(request, timeout=3) as response:
                target = json.load(response)
            ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=5, suppress_origin=True)
            msg_id = 0

            def cdp(method, params=None):
                nonlocal msg_id
                msg_id += 1
                ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                while True:
                    result = json.loads(ws.recv())
                    if result.get("method") == "Runtime.bindingCalled" and result.get("params", {}).get("name") == "__acceptanceNetworkEvent":
                        requests.append(json.loads(result["params"]["payload"]))
                    if result.get("id") == msg_id:
                        if "error" in result:
                            raise AssertionError(f"CDP {method}: {result['error']}")
                        return result.get("result", {})

            def evaluate(expression):
                result = cdp("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                                    "returnByValue": True, "userGesture": True})
                if result.get("exceptionDetails"):
                    raise AssertionError(result["exceptionDetails"].get("text", "browser script failed"))
                return result.get("result", {}).get("value")

            requests = []
            cdp("Network.enable")
            cdp("Runtime.addBinding", {"name": "__acceptanceNetworkEvent"})
            cdp("Page.addScriptToEvaluateOnNewDocument", {"source": """
              (() => {
                const originalFetch = window.fetch.bind(window);
                window.fetch = (...args) => { window.__acceptanceNetworkEvent(JSON.stringify({url:String(args[0]),method:args[1]?.method||'GET'})); return originalFetch(...args); };
              })();
            """})
            cdp("Page.enable")
            cdp("Runtime.enable")
            cdp("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if evaluate("document.readyState === 'complete' && document.querySelector('#items li') !== null"):
                    break
                time.sleep(.1)
            require(evaluate("document.querySelector('#items li') !== null"), "items not rendered")

            # The current section's browser contract checks, run against identical fixture rows.
            baseline = evaluate("({ rows: document.querySelectorAll('#items li').length, title: document.title })")
            require(baseline["rows"] > 0, "no rows rendered")
            cdp("Page.captureScreenshot", {"format": "png"})

            # Selection must span all matches, not merely the current first-page display.
            evaluate("document.querySelector('#selectVisible').click()")
            selection = evaluate("({ count: document.querySelector('#selectionCount').textContent, selected: document.querySelectorAll('#items input[type=checkbox]:checked').length })")
            require(selection['count'] == f"{len(got)} ausgewählt", 'not all filtered rows selected')
            evaluate("q('#selectVisible').click()")
            def click_item(item):
                evaluate(f"q('#items li[data-id=\"{item['id']}\"] input').click()")
            def key(name, code):
                for event in ('keyDown', 'keyUp'):
                    cdp('Input.dispatchKeyEvent', {'type':event, 'key':name, 'code':name, 'windowsVirtualKeyCode':code,
                        'text':'\r' if name=='Enter' and event=='keyDown' else ''})
            def until(expr):
                end = time.monotonic() + 6
                while time.monotonic() < end:
                    if evaluate(expr): return
                    time.sleep(.05)
                raise AssertionError(expr)
            evaluate("q('#more').click()")
            reads = [i for i in got if i.get('sourceLabel') == 'Inoreader'][:3]
            task = next(i for i in got if not i.get('url'))
            post = next(i for i in got if i.get('sourceLabel') == 'X')
            for item in reads + [task]: click_item(item)
            evaluate("q('#reviewSelected').click()")
            require(evaluate("Array.from(document.querySelectorAll('#reviewLinks a'),a=>a.href)") == [i['url'] for i in reads], 'wrong link batch')
            require('3 von 4' in evaluate("q('#reviewCount').textContent"), 'unlinked row not counted')
            key('Escape',27)
            evaluate("q('#handoffNote').focus()")
            key('Delete',46)
            require(evaluate("selected.size") == 4, 'text editing hid rows')
            evaluate("q('#items input:checked').focus()")
            key('Delete',46)
            require(evaluate("hiddenIds.size") == 4, 'Delete did not mark batch')
            evaluate("q('#undoDelete').focus()")
            key('Enter',13)
            require(evaluate("hiddenIds.size") == 0, 'Undo failed')
            click_item(post)
            evaluate("q('#handoffNote').focus()")
            cdp('Input.insertText', {'text':'Synthetic note'})
            evaluate("q('#copyPrompts').click()")
            preview=evaluate("q('#previewText').value")
            require(all(v and v in preview for v in (post['excerpt'],post['url'],'Synthetic note')), 'incomplete preview')
            cdp('Browser.grantPermissions', {'origin':origin,'permissions':['clipboardReadWrite','clipboardSanitizedWrite']})
            evaluate("q('#copyPreview').click()")
            until("q('#previewStatus').textContent.includes('kopiert')")
            require(evaluate('navigator.clipboard.readText()') == preview,'clipboard differs')
            key('Escape',27)
            click_item(task)
            evaluate("q('#bulkArea').value='Synthetic area';q('#assignArea').click()")
            until("Array.from(q('#topicFilter').options).some(o=>o.value==='Synthetic area')")
            evaluate("q('#topicFilter').value='Synthetic area';q('#topicFilter').dispatchEvent(new Event('change'))")
            require(set(evaluate("Array.from(q('#items').children,row=>row.dataset.id)")) == {post['id'],task['id']}, 'wrong area matches')
            requests=evaluate("performance.getEntriesByType('resource').map(x=>new URL(x.name).pathname)")
            require(not (set(requests)-{'/cockpit.css','/cockpit.js','/api/cockpit','/api/cockpit/areas','/favicon.ico'}), 'unexpected source request')
            server.terminate(); server.wait(timeout=5)
            server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL)
            deadline=time.monotonic()+8
            while True:
                try:
                    with urllib.request.urlopen(origin+'/api/cockpit',timeout=1) as response: reopened=json.load(response)
                    break
                except urllib.error.URLError:
                    if time.monotonic()>=deadline: raise
                    time.sleep(.1)
            require({i['id'] for s in reopened.values() for i in s['items'] if i['lifeArea']=='Synthetic area'} == {post['id'],task['id']}, 'restart lost assignment')
            cdp('Page.navigate',{'url':origin+'/cockpit'})
            until("document.querySelector('#items li') !== null")

            # Desktop state plus mobile viewport and keyboard-usable first actions.
            cdp("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
            mobile = evaluate("({ width: document.documentElement.scrollWidth, viewport: innerWidth, first: !!document.querySelector('#items li'), focusable: [...document.querySelectorAll('button,select,input')].filter(e=>!e.disabled).length })")
            require(mobile["width"] <= 390 and mobile["viewport"] == 390, "390x844 layout overflows horizontally")
            require(evaluate("document.querySelector('#items li strong').getBoundingClientRect().bottom <= innerHeight") and mobile["first"] and mobile["focusable"] > 0, "mobile first row/actions not keyboard reachable")
            evaluate("document.querySelector('a[href=\"/settings\"]').click()")
            until("document.querySelectorAll('#integrations article').length === 7")
            require(evaluate('document.documentElement.scrollWidth <= 390'), 'mobile settings overflow')
            evaluate("Array.from(document.querySelectorAll('#integrations article')).find(el=>el.textContent.includes('Karakeep')).querySelector('button').click()")
            until("document.querySelector('dialog[open] input[type=password]') !== null")
            key('Escape', 27)
            until("document.querySelector('dialog[open]') === null")
            print(json.dumps({"status": "PASS", "rows": len(got), "sources": len(api),
                              "desktop": baseline, "mobile": mobile}, ensure_ascii=False))
        finally:
            if ws:
                try: cdp("Browser.close")
                except Exception: pass
                ws.close()
            if chrome_proc:
                try:
                    chrome_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    chrome_proc.kill()
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
