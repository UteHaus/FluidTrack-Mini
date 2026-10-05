# Fahrzeug-Buttons ein Kennzeichen zuordnen

[English version](vehicle-keys.md)

Diese Anleitung erklärt, wie ein Fahrzeug-Button (grüner oder gelber User Key) an einer PIUSI-Zapfsäule mit MC-Steuerung ein Kennzeichen bekommt. Dieses Kennzeichen steht später in jedem Tankvorgang, den FluidTrack-Mini vom Master-Key liest.

## Das Wichtigste vorab

- **Der Button speichert kein Kennzeichen.** Ein Fahrzeug-Button hat keinen beschreibbaren Speicher. Er enthält nur eine fest eingebrannte Seriennummer (1-Wire-Familie `0x81`). Lesebefehle auf den Speicher liefern ausschließlich `0xFF`.
- **Das Kennzeichen liegt in der Zapfsäule.** Die Zapfsäule führt eine Benutzerliste. Jeder Eintrag hat einen Namen mit 1 bis 10 Zeichen, eine Benutzernummer von 1 bis 50 und optional einen Button. Den Namen trägst du als Kennzeichen ein, zum Beispiel `AB.CD.123`.
- **Der Master-Key überträgt nur Tankdaten zum PC.** Benutzer und Kennzeichen lassen sich über ihn nicht zur Zapfsäule übertragen. Deshalb kann FluidTrack-Mini sie auch nicht schreiben.
- **Die Nummer in den Tankvorgängen** ist sehr wahrscheinlich die Benutzernummer. FluidTrack-Mini speichert sie als „Operator“.

## Button zuordnen

An der Zapfsäule, Menü „USERS / ADD“:

1. Mit dem Master-Key oder der Manager-PIN ins Menü gehen. Ab Werk ist die PIN `1234`.
2. Mit den Pfeiltasten zu **USERS** blättern und **ENTER** drücken. Dann **USERS ADD** wählen und **ENTER** drücken.
3. Bei **USER NAME** das Kennzeichen eingeben, höchstens 10 Zeichen.
4. **USER PIN** auf **NO** stellen.
5. **ELECTRONIC KEY** auf **YES** stellen.
6. Bei „TOUCH USER KEY“ den Fahrzeug-Button an den Leser halten.
   - **Grüner Griff:** Die Zapfsäule fragt zusätzlich den vierstelligen **KEY CODE** ab, der auf dem Griff steht.
   - **Gelber Griff:** Kein Code nötig, der Button wird automatisch erkannt.
7. **USER NUMBER** auf **AUTO** lassen und mit **ENTER** bestätigen.

Die Zapfsäule zeigt danach kurz alle Angaben des neuen Benutzers an.

## Kennzeichen ändern

Ein angelegter Benutzer lässt sich nicht bearbeiten.

1. Unter **USERS / DELETE** den Benutzer über seine Benutzernummer löschen.
2. Ihn mit dem richtigen Kennzeichen neu anlegen, wie oben beschrieben.

Nach dem Löschen ist der Button wieder frei und kann neu vergeben werden. Unter **USERS / VIEW** siehst du alle Benutzer mit Nummer, Name und einem Stern für zugeordnete Buttons.

## Meldungen

| Meldung | Bedeutung |
|---|---|
| `WARNING KEY ALREADY ASSIGNED` | Der Button gehört schon einem anderen Benutzer. Diesen zuerst löschen. |
| `WARNING NAME ALREADY ASSIGNED` | Ein Benutzer mit diesem Namen existiert bereits. |
| `UNKNOWN USER KEY` | Beim Tanken: Der Button ist keinem Benutzer zugeordnet. |

## Alternative: Kennzeichen bei jedem Tanken abfragen

Unter **SYSTEM CONFIGURATION** (im Menü SYSTEM die Tasten `#` und `1` zusammen drücken) lässt sich **REGISTRATION NUMBER** auf **ENABLED** stellen. Dann fragt die Zapfsäule bei jedem Tankvorgang ein Kennzeichen mit bis zu 10 Zeichen ab.

## Einschränkungen

- Die Anleitung folgt dem Handbuch der MC-BOX-Steuerung. Je nach Firmware deiner Zapfsäule können Menünamen leicht abweichen.
- Ob neuere Versionen der PIUSI-Software SelfService Benutzer per RS-485-Kabel übertragen können, ist nicht geprüft.

## Quelle

- PIUSI, *MC-BOX Management System Software – Use Manual*, Bulletin M0187 EN Rev. 0, Kapitel 2.2, 3.4, 4.5.1, 4.6.2 und 4.6.6 ([PDF](https://www.oilybits.com/downloads/Piusi_MC_Software_Instruction_Manual.pdf))
