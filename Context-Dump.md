# Project Summary: FluidTrack-Mini 🔑⛽

## 🎯 Project Goal
A modular, open-source pipeline written in Python (managed via **uv**) to extract fleet fueling records from a **PIUSI CUBE 70 MC** station using a physical master key, synchronizing the data seamlessly to a local SQLite database and a Nextcloud Tables instance.

## 🎛️ Hardware & System Interface
* **USB Adapter:** Maxim/Dallas **DS9490R** USB-to-1-Wire Bridge. 
  * *Important:* It is **not** a serial/COM port device. It operates via `pyusb` (Windows) or the native Linux kernel subsystem at `/sys/bus/w1/devices/`.
* **Key Architecture:** **DS1996** (64 kbit memory iButton, hardware prefix `0c-`). On Linux systems, payload data blocks are fetched directly from the virtual `rw` or `memory` file.

## 📂 Directory Tree
```text
FluidTrack-Mini/
├── data/                       # Local database workspace
│   └── FluidTrack.db           # SQLite database using English column names
├── src/                        # Code directory
│   ├── main.py                 # Cross-platform polling, RegEx license plate mapper
│   ├── database.py             # Schema provisioning & auto-directory creation
│   └── nextcloud.py            # OCS-REST-API client with automated configuration writeback
├── .env                        # Local configurations & secrets (Git ignored)
└── pyproject.toml              # Project blueprints (pyusb, requests, python-dotenv)
```

## 🛠️ Key Engine Logic
1. **Nextcloud Sync App (`nextcloud.py`):** Automatically validates or creates the remote cloud grid structure with the correct typed fields (`Station`, `Timestamp`, `Liters`, `Operator`, `KeyID`). It features an **auto-writeback mechanism** that injects a newly generated table ID dynamically back into the local `.env` file to prevent configuration race conditions.
2. **License Plate Formatter (`main.py`):** Utilizes a Regular Expression parser to detect German vehicle identification plates stored within the PIUSI system in dot-notation (e.g., `DD.EE.1250`) and automatically reformats them into standard street notation (`DD-EE 1250`) before local or cloud synchronization.

## 🏁 Current Milestone & Next Steps
* **Status:** System architectural separation into `main.py`, `database.py`, and `nextcloud.py` is completed and running bug-free. The engine safely initializes fallback schemas and sample data rows.
* **Next Goal:** Build the binary decoder logic to parse the actual raw transaction bytes extracted from the physical `rw` memory dump of the PIUSI iButton key.
