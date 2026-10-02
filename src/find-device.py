import os
import usb
import glob

BASE_DIR = "/sys/bus/w1/devices/"
base_dir = '/sys/bus/w1/devices/'
device_folder = glob.glob(base_dir + '81-*')[0]
device_file = device_folder + '/w1_slave'


def read_ibutton():
# 1-Wire Gerät im Dateisystem finden

    dev = usb.core.find(idVendor=0x04FA, idProduct=0x2490)

    if dev is None:
        print("❌ DS9490R Adapter not found.")
    else:
        print("✅ DS9490R works!")

if __name__ == "__main__":
    if os.path.exists(BASE_DIR):
        read_ibutton()
    else:
        print(
            f"Fehler: {BASE_DIR} existiert nicht. Ist 1-Wire in der config.txt aktiviert?"
        )
