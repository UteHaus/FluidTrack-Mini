import usb.core
import usb.util

# Suche nach dem DS9490R anhand seiner USB-IDs
dev = usb.core.find(idVendor=0x04FA, idProduct=0x2490)

if dev is None:
    print("❌ DS9490R Adapter wurde nicht gefunden. Steckt er richtig im USB-Port?")
else:
    print("✅ DS9490R erfolgreich über USB angesprochen!")
    # Hier wird das USB-Protokoll initialisiert, um den iButton auszulesen
