"""Preview So. with disposable sample data. No installation required."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from urllib.request import ProxyHandler, build_opener
import webbrowser

from acceptance.sol62_fixture import isolated_environment, make_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18769)
    parser.add_argument('--check', action='store_true', help='Test starten und direkt beenden')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port muss zwischen 1 und 65535 liegen.')
    root = Path(__file__).resolve().parent
    url = f'http://127.0.0.1:{args.port}/'
    with tempfile.TemporaryDirectory(prefix='so-demo-') as tmp:
        home, data = make_fixture(Path(tmp).resolve())
        with subprocess.Popen([sys.executable, '-B', str(root / 'server.py'), '--port', str(args.port)],
                              cwd=root, env=isolated_environment(home, data), stdout=subprocess.PIPE,
                              text=True) as server:
            try:
                # server.py flushes its first line only after successfully binding.
                if server.stdout is None or not server.stdout.readline():
                    raise SystemExit('Start fehlgeschlagen. Bei belegtem Port: --port 18770 verwenden.')
                client = build_opener(ProxyHandler({}))
                with client.open(url + 'api/cockpit', timeout=5) as response:
                    count = sum(len(source['items']) for source in json.load(response).values())
                if count != 49:
                    raise SystemExit(f'Demodaten unvollständig: {count} statt 49 Einträge.')
                print(f'{count} Demo-Einträge — {url} — Beenden: Ctrl+C', flush=True)
                if not args.check:
                    webbrowser.open(url)
                    server.wait()
            except KeyboardInterrupt:
                pass
            finally:
                if server.poll() is None:
                    server.terminate()


if __name__ == '__main__':
    main()
