# So.
Lokale Web-App zum Durchgehen gespeicherter Links aus Inoreader und X: behalten, in Karakeep sichern oder gezielt entfernen.

## Lokaler Betrieb

Die Adressen mit `.example.invalid` in `n8n_connection.py` und `karakeep_connection.py` sind absichtlich nicht erreichbar. Für echte Integrationen die HTTPS-Ursprünge und die n8n-Hostliste ausschließlich im privaten Deployment konfigurieren. Keine Schlüssel, persönlichen Workflow-Exporte oder Lesedaten in Git speichern. Die historischen `hermes/`-Branches sind bereinigte Quellstände, keine lauffertigen Backups.

### Getrennte Datenablage

Der Code-Ordner enthält ausschließlich Anwendungscode und statische Assets. Für den Produktionsstart muss `SO_DATA_DIR` auf einen bestehenden, absoluten, vom Code-Ordner getrennten Datenordner zeigen. Ohne gültige Einstellung startet `server.py` nicht; es gibt keinen stillen Rückfall auf den Checkout. Die Anwendung legt dort Artikel-Snapshots, Status, X-Lesezeichen und -Inhalte sowie OAuth-Clientdateien ab. Logs des gestarteten Prozesses bleiben beim Supervisor/der Konsole; keine Logdateien werden im Checkout erzeugt.

Beispiel (Platzhalter ersetzen; keine privaten Pfade ins Repository schreiben):

```sh
export SO_DATA_DIR=/absolute/path/to/private/prod_data  # vorhandener Ordner, Modus 0700
python3 server.py
```

Die historische `App(root, origin, state_path=None)`-Konstruktorform bleibt für Tests und bestehende lokale Aufrufer erhalten und verwendet weiterhin `root` als Datenordner. Der Produktions-CLI-Einstieg verlangt dagegen explizit `SO_DATA_DIR`. HTTP-Statische Dateien werden ausschließlich über eine feste Allowlist aus dem Code-Ordner ausgeliefert; Datenordner und Archive sind keine statischen Quellen.
