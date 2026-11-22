"""Start the local app on demand, or open its already-running instance."""
import json
import os
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
            return (isinstance(data.get('articles'), list)
                    and isinstance(data.get('state'), dict) and 'sourceInventory' in data)
    except (OSError, ValueError):
        return False


def main():
    data_dir = os.environ.get('SO_DATA_DIR')
    if not data_dir or not Path(data_dir).is_absolute() or not Path(data_dir).is_dir():
        raise RuntimeError('SO_DATA_DIR must name an existing absolute data directory')
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
