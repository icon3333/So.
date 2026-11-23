<div align="center">

# So.

**Sorta organized.**

Eine lokale Übersicht für gespeicherte Links, To-dos und Projekte. In der Leserunde entscheidest du, was auf der Liste bleibt, nach Karakeep wandert oder aus der Quelle entfernt werden soll.

[Was So. kann](#was-so-kann) · [Lokal starten](#lokal-starten) · [Quellen und Daten](#quellen-und-daten)

![So.-Übersicht mit Filtern für To-dos, Lesestoff und Projekte sowie einem Beispiel-Eintrag aus Inoreader](docs/assets/so-uebersicht.png)

<sub>Die So.-Übersicht mit erfundenen Beispieldaten.</sub>

</div>

## Was So. kann

| Bereich | Was du damit machst |
| --- | --- |
| **Übersicht** | To-dos, Lesestoff und Projekte nach Quelle, Lebensbereich oder Format filtern. Links gesammelt öffnen, Einträge zuordnen oder lokal ausblenden. |
| **Leserunde** | Gespeicherte Links aus Inoreader und X durchgehen, zum späteren Lesen behalten, an Karakeep schicken oder zum Entfernen vormerken. Das Entfernen aus Inoreader oder X braucht eine separate Bestätigung. |
| **Übergabe** | Ausgewählte Einträge als Textvorschau für Hermes zusammenstellen. So. sendet nichts automatisch. |

## Lokal starten

Du brauchst Python 3.9 oder neuer auf macOS oder Linux und einen Browser. Der Server nutzt die Python-Standardbibliothek.

```sh
git clone https://github.com/icon3333/So..git so
cd so
export SO_DATA_DIR="$HOME/.local/share/so/prod_data"
mkdir -p -m 700 "$SO_DATA_DIR"
python3 server.py
```

Öffne [127.0.0.1:8769](http://127.0.0.1:8769/). Die Startseite und [`/reader`](http://127.0.0.1:8769/reader) zeigen dieselbe integrierte Liste. Die früheren Cockpit-Adressen `/cockpit` und `/cockpit.html` führen ebenfalls dorthin. Einstellungen bleiben unter [`/settings`](http://127.0.0.1:8769/settings). Ohne lokale Quellen oder importierte Daten kann die Liste leer sein.

## Quellen und Daten

In der Liste öffnet **Einstellungen → Integrationen** die Verbindungsdialoge. Ein Kontotest läuft erst auf Klick; bei X ist vorher eine Kostenfreigabe nötig. Inoreader nutzt den vorhandenen n8n-OAuth-Dienst, Tasks.org/Posteo den serverseitigen Leseadapter. Der Test ersetzt nicht die Einrichtung dieser Dienste.

Keep-Takeout, Desktop-To-dos und Projekte bleiben Dateiimporte. Um im Dialog einen Ordner zu wählen, setze `LESELISTE_IMPORT_ROOT` auf einen separaten Importordner (nicht Checkout, Home oder Produktivdatenordner). Wähle darunter den relativen Pfad zu `Keep`, `To` oder `Projects`, teste ihn und speichere ihn erst danach. Ohne neue Konfiguration gelten die folgenden Standardpfade.

- **Lokale Quellen:** Auf dem Mac liest die Übersicht `To` und `Projects` auf dem Desktop. Mit `LESELISTE_DESKTOP` lässt sich der Desktop-Pfad vorgeben. Für einen Google-Keep-Export nutzt So. standardmäßig `~/Downloads/Takeout/Keep`; überschreiben lässt sich der Pfad mit `LESELISTE_KEEP_EXPORT`. Tasks.org über Posteo benötigt einen separaten Adapter (`LESELISTE_POSTEO_ADAPTER`).
- **Online-Quellen:** Der öffentliche Quellstand enthält keine Zugangsdaten oder privaten Workflows. Die `.example.invalid`-Adressen in [`n8n_connection.py`](n8n_connection.py) und [`karakeep_connection.py`](karakeep_connection.py) sind Platzhalter. Hinterlege echte HTTPS-Ursprünge und die n8n-Hostliste nur im privaten Deployment. Historische `hermes/`-Branches sind bereinigte Quellstände, keine lauffähigen Backups.
- **Datenordner:** `SO_DATA_DIR` muss auf einen bestehenden, absoluten Ordner namens `prod_data` außerhalb des Checkouts zeigen. Er muss dir gehören, privat sein (z. B. Modus `0700`) und darf keine Symlinks enthalten. Ohne gültigen Pfad startet der Server nicht. Artikel-Snapshots, Bearbeitungsstatus, X-Lesezeichen und -Inhalte sowie OAuth-Clientdateien gehören dorthin, nicht ins Repository. Logs bleiben auf der Konsole oder beim Supervisor.

<details>
<summary>Entwicklung und Tests</summary>

```sh
python3 -m unittest discover
node --test test_*.cjs
```

Die historische Konstruktorform `App(root, origin, state_path=None)` verwendet für Tests und bestehende lokale Aufrufer weiterhin `root` als Datenordner. Der CLI-Start verlangt dagegen `SO_DATA_DIR`. Der HTTP-Server liefert nur ausdrücklich freigegebene statische Dateien aus dem Code-Ordner aus, keine Datenordner oder Archive.

</details>

## Lizenz

Im Repository liegt keine Lizenzdatei.
