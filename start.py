"""Start the local app on demand, or open its already-running instance."""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ORIGIN = 'http://127.0.0.1:8769/'


def running():
    try:
        with urllib.request.urlopen(ORIGIN + 'api/state', timeout=1) as response:
            data = json.load(response)
            return data.get('total') == 371 and isinstance(data.get('articles'), list)
    except (OSError, ValueError):
        return False


def main():
    if running():
        webbrowser.open(ORIGIN)
        return
    root = Path(__file__).resolve().parent
    process = subprocess.Popen([sys.executable, '-B', str(root / 'server.py')], cwd=root)
    try:
        for _ in range(30):
            if process.poll() is not None:
                raise RuntimeError('Die Leseliste konnte nicht starten. Ist Port 8769 bereits belegt?')
            if running():
                webbrowser.open(ORIGIN)
                print('Dieses Fenster offen lassen. Zum Beenden: Ctrl+C.', flush=True)
                process.wait()
                return
            time.sleep(0.2)
        raise RuntimeError('Die Leseliste antwortet noch nicht. Bitte erneut starten.')
    except KeyboardInterrupt:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()


if __name__ == '__main__':
    main()
