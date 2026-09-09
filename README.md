# FluidTrack-Mini 🔑⛽

Ein minimalistisches Open-Source-Tool zum direkten Auslesen und Parsen von Tankdaten aus dem **PIUSI CUBE MC** Managementsystem via USB-1-Wire-Adapter.

Dieses Tool dient als leichtgewichtige Alternative zur offiziellen Software, um die auf dem roten **Manager Key (Master Key)** gespeicherten Betankungstransaktionen unkompliziert auszulesen und als strukturierte Daten bereitzustellen. Entwickelt mit ⚡ **uv**.

## 🚀 Funktionen

* **Hardware-Erkennung:** Automatische Erkennung des Maxim/Dallas DS9490R USB-zu-1-Wire-Adapters.
* **Schlüsselerkennung:** Erkennt sofort, wenn der rote iButton-Manager-Schlüssel aufgelegt wird.
* **Rohdaten-Export:** Sichert die originalen Hex- oder Binärdaten direkt vom EEPROM des Schlüssels.
* **Transaktions-Parser:** Entschlüsselt die letzten aufgezeichneten Tankvorgänge (Nutzer-ID, Literanzahl, Datum, Uhrzeit und Fahrzeugcode).
* **Daten-Export:** Ausgabe der ausgelesenen Daten als saubere `.csv` oder `.json` zur Weiterverarbeitung.

## 📋 Voraussetzungen & Hardware

Das Programm setzt die Nutzung der originalen PIUSI-Hardware voraus:
1. **Zapfsäule:** PIUSI CUBE 70 MC (oder kompatible MC-Box-Systeme).
2. **Datenträger:** Roter PIUSI Manager Key (iButton-Format).
3. **Hardware-Interface:** DS9490R USB-1-Wire-Adapter.

### Software-Voraussetzungen
Das Projekt setzt den modernen Python-Paket- und Projekt-Manager **[uv](https://astral.sh)** voraus.

> 🐧 **Hinweis für Linux-Nutzer:** Der DS9490R erzeugt **keinen** virtuellen COM-Port (kein `ttyUSB0` / `ttyACM0`). Das Gerät wird stattdessen direkt als 1-Wire-Bus im Kernel registriert oder über `libusb` angesprochen.

## 🛠️ Installation & Einrichtung

### 1. Repository klonen
```bash
git clone https://github.com
cd FluidTrack-Mini
```

### 2. Linux-System vorbereiten (nur unter Linux nötig)
Damit der Kernel den USB-Adapter richtig einhängt und du ohne Root-Rechte darauf zugreifen kannst, lade das Kernel-Modul:
```bash
sudo modprobe ds2490
```

## 💻 Benutzung

1. Stecke den DS9490R-USB-Stick in deinen PC.
2. Lege den roten **Manager Key** auf den Leser.
3. Starte das Skript direkt über `uv run`:

```bash
uv run read_key.py --output tankdaten.csv
```

### Parameter:
* `--output`: Ziel-Dateiname für den Datenexport (Standard: `export.csv`).
* `--mode`: Wechselt zwischen `native` (Nutzt Linux `/sys/bus/w1/`) und `usb` (Direktzugriff via PyUSB).

## 📊 Datenstruktur (Ausgabebeispiel)

Nach dem erfolgreichen Auslesen wird eine Datei mit folgendem Inhalt erzeugt:

| Zeitstempel | Nutzer_ID | Fahrzeug_Code | Menge_Liter | Status |
| :--- | :--- | :--- | :--- | :--- |
| 2026-09-09 14:23:11 | User_04 | LOG-ZN-102 | 64.50 | OK |
| 2026-09-09 15:05:42 | User_12 | AGR-TR-882 | 120.10 | OK |

## 🛡️ Rechtlicher Hinweis / Disclaimer

Dieses Projekt steht in keiner Verbindung zu der Firma **PIUSI SpA**. Es handelt sich um ein unabhängiges Community-Projekt zur Vereinfachung des Datenexports für den Eigenbedarf. Die Nutzung erfolgt auf eigene Gefahr.
