# FluidTrack-Mini 🔑⛽

[English](README.md) | **Deutsch**

Ein minimalistisches, modulares Open-Source-Tool, das Tankvorgänge direkt von einem **PIUSI-iButton-Tankschlüssel (DS1996)** über einen USB-1-Wire-Adapter ausliest, auswertet und in einer lokalen SQLite-Datenbank sowie (optional) in Nextcloud Tables speichert. Entwickelt mit ⚡ **uv**.

> ℹ️ Dieses Projekt kommuniziert **direkt per USB mit dem physischen Tankschlüssel**, unabhängig von der originalen PIUSI-Windows-Software „SelfService“. Das Datenformat auf dem Schlüssel wurde vollständig per Reverse Engineering ermittelt (siehe [Protokoll-Notizen](#-protokoll-notizen--wie-das-format-ermittelt-wurde) unten) und mit echten exportierten Tankdaten abgeglichen.

## 🏭 Kompatible Hardware

Gedacht für **PIUSI-Tankmanagementsysteme**, die Fahrer/Fahrzeuge über einen roten **DS1996-„Master Key“**-iButton identifizieren und die Daten mit der PIUSI-Software „SelfService“ verwalten (z. B. Zapfsäulen der Serie **PIUSI CUBE MC**). Es sollte mit jeder Anlage funktionieren, die denselben DS1996-Schlüssel und ein DS9490R-USB-Lesegerät verwendet – unabhängig vom eingesetzten PIUSI-Zapfsäulenmodell. Das Tool liest den Schlüssel, nicht die Zapfsäule.

## 🚀 Funktionen

* **Direkter USB-Hardwarezugriff:** Spricht den Maxim/Dallas-**DS2490**-Chip im DS9490R-Adapter direkt per USB an (`pyusb`), unabhängig von 1-Wire-Treibern des Betriebssystems. Das ist wichtig, weil gängige Linux-Kernel **keinen eigenen Treiber für die DS1996-Familie (`0x0c`)** haben – beim Lesen über die generischen `sysfs`-Dateien kommen daher stillschweigend nur leere Daten (`0xFF`) zurück.
* **Korrekte Behandlung mehrerer Geräte:** Unterscheidet zuverlässig den DS1996-Tankschlüssel (Familie `0x0c`) von einem zweiten Identifikationschip ohne Speicher (DS1420, Familie `0x81`), der am selben 1-Wire-Bus hängen kann.
* **Vollständige Ringpuffer-Auswertung:** Liest und dekodiert alle 255 gespeicherten Tankvorgänge (Datum/Uhrzeit im BCD-Format, Liter als 3 BCD-Bytes, Fahrzeug-/Gerätename) aus dem Ringpuffer des Schlüssels.
* **Hash-basierte Duplikaterkennung:** Jeder Tankvorgang erhält einen stabilen Inhalts-Hash. Wird derselbe Schlüssel erneut gelesen (z. B. bevor er synchronisiert/gelöscht wurde), entstehen weder lokal noch in Nextcloud doppelte Einträge.
* **Nextcloud ist optional:** Läuft problemlos nur mit lokalem SQLite. Sind Nextcloud-Zugangsdaten hinterlegt, wird die Zieltabelle automatisch angelegt (Spalten: Station, Timestamp, Liters, Operator, KeyID) und neue Zeilen werden synchronisiert.
* **Nextcloud-Anmeldung im Browser:** Optionale einmalige Einrichtung (`nextcloud_login.py`) über den offiziellen Nextcloud „Login Flow v2“ – derselbe Mechanismus wie beim Nextcloud-Desktop-Client. Kein manuelles Erstellen eines App-Passworts nötig.
* **Optionales Löschen des Schlüssels:** Kann den Schlüssel automatisch löschen, sobald *jeder* Eintrag darauf nachweislich gespeichert (und, falls aktiviert, mit Nextcloud synchronisiert) ist. Das Verfahren entspricht der Original-Software von PIUSI. Muss ausdrücklich aktiviert werden, standardmäßig aus.
* **Kennzeichen-Formatierung:** Wandelt Kennzeichen in Punktschreibweise (`AB.CD.1234`) vom Schlüssel in die übliche Schreibweise (`AB-CD 1234`) um. Gerätenamen, die nicht dem Kennzeichenmuster entsprechen (z. B. `FORKLIFT`, `EXCAVATOR-2`), bleiben unverändert.

## 📋 Hardware- und Systemvoraussetzungen

1. **Datenträger:** PIUSI-iButton-Tankschlüssel – **DS1996+F5** (64 kbit NV-RAM, Familiencode `0x0c`). Der Zusatz `+F5` ist nur eine Gehäusevariante von Maxim und ändert nichts am Protokoll.
2. **Hardware-Schnittstelle:** Maxim/Dallas **DS9490R** USB-zu-1-Wire-Adapter (enthält den DS2490-Brückenchip).

> 🐧 **Hinweise für Linux:**
> - Der DS9490R erzeugt **keine** virtuelle serielle Schnittstelle (kein `ttyUSB0`/`ttyACM0`) – er ist ein natives USB-Gerät.
> - Das Kernelmodul `ds2490` ist **optional**. Ist es geladen, wird die ROM-ID des Schlüssels aus `/sys/bus/w1/devices/.../w1_master_slaves` gelesen. Andernfalls durchsucht die App den 1-Wire-Bus selbst per USB. Lesen und Schreiben des Schlüsselspeichers umgeht immer den Kerneltreiber und spricht den DS2490 direkt per USB an – erst dadurch funktioniert das Lesen der DS1996-Familie zuverlässig.
> - Wahrscheinlich brauchst du eine **udev-Regel**, damit der Adapter ohne `sudo` zugänglich ist:
>   ```
>   # /etc/udev/rules.d/99-ds9490.rules
>   SUBSYSTEM=="usb", ATTR{idVendor}=="04fa", ATTR{idProduct}=="2490", MODE="0666"
>   ```
>   Danach: `sudo udevadm control --reload-rules && sudo udevadm trigger` und den Adapter neu einstecken.

> 🪟 **Hinweise für Windows:**
> - Der DS9490R muss den **WinUSB**-Treiber verwenden. Einmalig mit [Zadig](https://zadig.akeo.ie/) installieren: *Options → List All Devices* aktivieren, den Adapter auswählen (USB-ID `04FA 2490`), **WinUSB** wählen und auf *Replace Driver* klicken.
> - Die Maxim/TMEX-„1-Wire Drivers“ sind **nicht** nutzbar. PIUSI SelfService ist auf sie angewiesen und kann den Adapter daher nicht verwenden, solange WinUSB installiert ist. Zurückwechseln geht im Geräte-Manager über *Treiber → Vorheriger Treiber*.
> - Die Bibliothek `libusb-1.0.dll` kommt mit der Abhängigkeit `libusb-package` und wird automatisch in den Build gepackt.
> - Die Schlüsselerkennung läuft über USB, daher wird kein 1-Wire-Kernel- oder Herstellertreiber benötigt. Mit `make find-device` lässt sich die Einrichtung prüfen.

## 🛠️ Dateistruktur des Repositorys

* **`main.py`** — Laufzeitschleife: erkennt die ROM-ID des Schlüssels, liest den gesamten Speicher per USB, wertet die Tankvorgänge aus, entfernt Duplikate, speichert lokal, synchronisiert mit Nextcloud (falls eingerichtet) und löscht optional den Schlüssel.
* **`ds9490_direct.py`** — Low-Level-Implementierung des DS2490-USB-Protokolls (`pyusb`): Reset, Bit-/Byte-I/O, Blocklesen, 1-Wire-ROM-Suche, Match-ROM-Adressierung, Write/Read/Copy Scratchpad sowie der Parser für die Transaktionsdaten (`scan_records`). Läuft unter Linux und Windows.
* **`find-device.py`** — Diagnose: öffnet den Adapter per USB und listet alle 1-Wire-Geräte am Bus auf.
* **`database.py`** — Lokales SQLite-Schema, Migrationen, Hash-basierte Duplikatprüfung und Statusverwaltung der Zeilen.
* **`nextcloud.py`** — Kommunikation mit der OCS-REST-API, automatisches Anlegen der Tabelle und Hochladen von Zeilen. Vollständig optional – siehe unten.
* **`nextcloud_login.py`** — Einmaliges interaktives Einrichtungsskript: Nextcloud-Anmeldung im Browser (Login Flow v2), schreibt die Zugangsdaten automatisch in `.env`.

## ⚙️ Einrichtung

### 1. Projekt initialisieren
```bash
git clone https://github.com/your-org/FluidTrack-Mini
cd FluidTrack-Mini
uv sync
```

### 2. Nextcloud-Anmeldung (optional)
Für die Cloud-Synchronisierung einmalig ausführen, statt manuell ein App-Passwort anzulegen:
```bash
uv run python nextcloud_login.py https://your-nextcloud-instance.com
```
Das öffnet den Browser, du meldest dich ganz normal an (inklusive 2FA), und `NEXTCLOUD_URL`, `NEXTCLOUD_USER` und `NEXTCLOUD_APP_TOKEN` werden automatisch in `.env` geschrieben. Diesen Schritt einfach überspringen, wenn nur lokal in SQLite gespeichert werden soll.

### 3. Umgebungsvariablen (`.env`)
Vorlage kopieren und anpassen. Alle Einstellungen sind in [`env.example`](env.example) dokumentiert.
```bash
cp env.example src/.env
```
Beim Start aus dem Quellcode liest die App `src/.env`. Die gebaute App liest die `.env` neben ihrer ausführbaren Datei. Jede Einstellung ist optional. Ohne Nextcloud-Zugangsdaten speichert die App nur lokal. `NEXTCLOUD_TABLE_ID` wird automatisch ausgefüllt.

### Mehrere Nutzer, eine Tabelle
Jeder Nutzer meldet sich mit seinem eigenen Nextcloud-Konto an, alle Einträge sollen aber in **einer** Tabelle landen:

1. Der erste Nutzer richtet FluidTrack-Mini wie gewohnt ein. Die App legt die Tabelle an.
2. Auf dieser Installation `NEXTCLOUD_SHARE_WITH` in der `.env` auf eine Nextcloud-Gruppe oder einen Nutzer setzen (z. B. `Fahrer`) und neu starten. Die App teilt die Tabelle mit Lese- und Anlegerecht. Alternativ in der Weboberfläche von Nextcloud Tables mit mindestens *Lesen* und *Erstellen* teilen.
3. Alle anderen Installationen nehmen die geteilte Tabelle automatisch, weil sie immer die **älteste** beschreibbare Tabelle mit diesem Namen verwenden. Einträge, die sie vorher in einer eigenen Tabelle gespeichert haben, werden ohne Duplikate in die geteilte hochgeladen. Ihre alten Tabellen können danach gelöscht werden.

Das Fenster zeigt an, wessen Tabelle verwendet wird („geteilt von …“).

## 💻 Benutzung

> 🔑 **Fahrzeug-Buttons:** Kennzeichen werden an der Zapfsäule zugeordnet, nicht auf dem Button. Siehe [Fahrzeug-Buttons ein Kennzeichen zuordnen](docs/vehicle-keys.de.md).

DS9490R einstecken, den iButton-Schlüssel auf die Lesefassung legen und starten:
```bash
uv run python main.py
```

### Desktop-Fenster (optional)
Statt der Konsolenschleife kann ein kleines tkinter-Steuerfenster gestartet werden:
```bash
uv run python tray_app.py
```
Es zeigt den Status des Runners und ein Live-Log, und man kann den Runner pausieren/fortsetzen und sich bei Nextcloud anmelden. tkinter unterstützt keinen System-Tray; beim Schließen wird das Fenster daher in die Taskleiste minimiert. Beenden mit **Quit**. Auf minimalen Linux-Installationen ist eventuell `sudo apt install python3-tk` nötig. Das Fenster folgt der Systemsprache (Deutsch oder Englisch); mit `UI_LANGUAGE=de` oder `en` in der `.env` lässt sich das festlegen.

### Makefile-Kurzbefehle
`make` listet alle Ziele auf. Die wichtigsten sind `make sync`, `make run`, `make lint`, `make build`, `make cloud-sync` und `make backup-db`. `make lint` prüft den Code mit [ruff](https://docs.astral.sh/ruff/), `make format` korrigiert und formatiert ihn; die CI führt dieselbe Prüfung aus. `make build` sichert `.env` und Datenbank der gebauten App und stellt sie danach automatisch wieder her. PyInstaller kann nicht cross-kompilieren, daher `make build-windows` unter Windows in Git Bash oder MSYS2 ausführen.

### Eigenständiger Build (PyInstaller)
```bash
uv run pyinstaller --noconfirm FluidTrack-Mini.spec
cp src/.env dist/FluidTrack-Mini/.env
```
Die gebaute App liest `.env` und speichert `FluidTrack.db` **neben der ausführbaren Datei** (`dist/FluidTrack-Mini/`), nicht in `_internal/`. Ein Neubau mit `--noconfirm` löscht diesen Ordner, daher `.env` und Datenbank vorher sichern.

Das Fenster zeigt den Nextcloud-Verbindungsstatus: grün bedeutet verbunden und Tabelle bereit, rot bedeutet nicht verbunden (mit Angabe des Grunds), grau bedeutet nicht eingerichtet. Die Verbindung wird jede Minute neu geprüft. Eine fehlende oder veraltete `NEXTCLOUD_TABLE_ID` wird automatisch über den Tabellentitel ermittelt, und die Tabelle wird angelegt, falls sie nicht existiert.

### CI-Builds (GitHub Actions)
Der Workflow in `.github/workflows/build.yml` baut die App auf einem Linux- und einem Windows-Runner. Bei jedem Push auf `main`/`master` hängen beide Archive am Workflow-Lauf unter **Actions → Artifacts**. Das Pushen eines Versions-Tags erstellt ein GitHub-Release mit beiden Archiven:
```bash
git tag v0.1.0 && git push origin v0.1.0
```
Die Archive enthalten keine `.env` und keine Datenbank. `env.example` als `.env` neben die ausführbare Datei kopieren.

Für Windows gibt es zusätzlich einen MSI-Installer (`FluidTrack-Mini-windows.msi`). Er installiert pro Benutzer nach `%LOCALAPPDATA%\Programs\FluidTrack-Mini`, ohne Admin-Rechte, und legt einen Startmenü-Eintrag an. Die `.env` gehört in diesen Ordner. Ein neueres MSI ersetzt die alte Version; `.env` und Datenbank bleiben erhalten, auch bei der Deinstallation. Das MSI ist nicht signiert, daher zeigt Windows SmartScreen eine Warnung: *Weitere Informationen → Trotzdem ausführen* anklicken.

### Ablauf
1. `main.py` startet, lädt `.env` und initialisiert/migriert das lokale SQLite-Schema.
2. Ist Nextcloud eingerichtet, prüft `nextcloud.py` die Zieltabelle bzw. legt sie an; andernfalls wird die Cloud-Synchronisierung komplett übersprungen (das ist ein voll unterstützter Betriebsmodus, kein Fehlerzustand).
3. Alle 5 Sekunden prüft die Schleife den 1-Wire-Bus auf eine DS1996-ROM-ID (Familie `0x0c`) und ignoriert andere Gerätefamilien (z. B. einen DS1420-Identifikationschip).
4. Bei Erkennung: Der gesamte 8192 Byte große Schlüsselspeicher wird direkt per USB gelesen (`ds9490_direct.py`), alle 255 Ringpuffer-Plätze werden ausgewertet, und jeder Tankvorgang erhält einen Hash zur Duplikaterkennung.
5. Neue (noch unbekannte) Tankvorgänge werden lokal gespeichert und, falls Nextcloud eingerichtet ist, hochgeladen.
6. Ist `DELETE_KEY_AFTER_SYNC=true` **und** jeder Tankvorgang auf dem Schlüssel nachweislich vollständig verarbeitet, wird der Schlüssel wie mit der PIUSI-Software gelöscht und danach durch erneutes Lesen geprüft. Firmware-Version und Stationsnummer bleiben erhalten.

## 📊 Datenzuordnung

| iButton-Feld | SQLite-Spalte | Nextcloud-Spalte | Beispiel |
| :--- | :--- | :--- | :--- |
| Stationsnummer (16-Byte-Header) | `serial_number` | Station | `"100001"` |
| Datum + Uhrzeit (kombiniert, ISO 8601) | `timestamp` | Timestamp | `"2026-01-15T09:30:00"` |
| Liter (3×BCD-Bytes, ÷100) | `liters` | Liters | `45.80` |
| Fahrzeug-/Gerätename (ggf. als Kennzeichen formatiert) | `registration_number` | Operator | `"AB-CD 1234"` |
| Bedienercode (Rohbyte vom Schlüssel) | `operator` | *(nur lokal)* | `"3"` |
| iButton-ROM-ID | `key_id` | KeyID | `"0c-0000000000ab"` |
| Inhalts-Hash zur Duplikaterkennung | `hash` | *(nur lokal)* | `sha256(...)` |

## 🔬 Protokoll-Notizen — Wie das Format ermittelt wurde

Das Format auf dem Schlüssel ist vom Hersteller nicht dokumentiert und wurde wie folgt ermittelt:
1. Disassemblieren der originalen PIUSI-Windows-Programme „Self.exe“/„SelfComm.exe“, um die 1-Wire-Befehlsfolge auf unterster Ebene zu identifizieren (`TMTouchReset`/`TMTouchByte`, Skip ROM, Read Memory `0xF0`).
2. Eigenständige Neuimplementierung dieser Befehlsfolge in Python auf Basis des dokumentierten, GPL-referenzierten DS2490-USB-Protokolls (`ds9490_direct.py`) – es wird kein Herstellercode verwendet.
3. Abgleich der dekodierten Felder mit einem Export aus der herstellereigenen „SelfService“-Datenbank: Fahrzeugname, Datum, Uhrzeit und Liter stimmen bei mehreren unabhängigen Tankvorgängen exakt überein.

**Wichtigste Erkenntnisse:**
- Der Speicher ist ein **Ringpuffer mit 255 Plätzen** (32 Byte/Platz) nach einem 16-Byte-Header (2 unbekannte Bytes + nullterminierter Firmware-Versionsstring + 6-stellige Stationsnummer).
- Header-Byte 8 ist der **Schreibzeiger**: der Platz, den die Zapfsäule als Nächstes beschreibt (z. B. `0x0E`, wenn der neueste Eintrag in Platz 13 liegt).
- **Löschen** (wie die PIUSI-SelfService-Software, an einem echten Schlüssel geprüft): Die Namenshälfte jedes Eintrags wird auf `0xFF` gesetzt und der Schreibzeiger auf `0`. Die Datenhälften (Liter/Datum/Uhrzeit) bleiben auf dem Schlüssel, ein Platz ohne Namen zählt aber nicht mehr als Eintrag. PIUSI lässt den Namen des letzten Platzes (`0x1FF0`) stehen. FluidTrack-Mini löscht ihn mit, damit ein gelöschter Schlüssel leer gelesen wird.
- Ist der Puffer voll, werden die ältesten Einträge zuerst überschrieben – die physische Adressreihenfolge ist über den gesamten Puffer **nicht** chronologisch (sie beginnt bei jedem Umlauf abschnittsweise neu).
- Innerhalb eines 32-Byte-Platzes gehört die „Namens“-Hälfte tatsächlich zur **Datenhälfte des vorherigen Platzes**, nicht zur eigenen – ein Versatz um eins, der erst beim Abgleich mit echten Exporten auffiel.
- Liter sind als **drei aufeinanderfolgende BCD-Bytes kodiert, zu einer 6-stelligen Zahl zusammengesetzt und durch 100 geteilt** (z. B. `00 45 80` → `"004580"` → `45.80` L) – keine einfache Binärzahl.

## 🛡️ Haftungsausschluss

Diese Anwendung ist ein unabhängiges Community-Projekt und steht in keiner Verbindung zu **PIUSI SpA**, wird von PIUSI weder autorisiert, gepflegt noch unterstützt. Das oben beschriebene Datenformat wurde durch unabhängiges Reverse Engineering zum Zweck der Interoperabilität ermittelt. Nutzung auf eigene Verantwortung – insbesondere `DELETE_KEY_AFTER_SYNC` vor dem Produktiveinsatz gründlich mit einem unkritischen Schlüssel testen, da dabei Tankdaten dauerhaft vom Schlüssel gelöscht werden.
