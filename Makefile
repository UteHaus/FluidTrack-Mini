# FluidTrack-Mini -- developer shortcuts
# Run `make` or `make help` for an overview.
#
# Windows: use this Makefile from Git Bash / MSYS2 (needs `make` and `uv`).
# PyInstaller cannot cross-compile, so `build-windows` must run ON Windows
# and `build-linux` ON Linux.

APP       := FluidTrack-Mini
SPEC      := $(APP).spec
SRC       := src
DIST_DIR  := dist/$(APP)
KEEP_DIR  := build/keep
BACKUPS   := backups
UV        := uv
URL       ?=

ifeq ($(OS),Windows_NT)
  HOST_OS := windows
  EXE     := $(DIST_DIR)/$(APP).exe
else
  HOST_OS := linux
  EXE     := $(DIST_DIR)/$(APP)
endif

.DEFAULT_GOAL := help
.PHONY: help sync update run run-cli login cloud-sync check find-device inspect-key \
        backup-db build build-linux build-windows run-build package clean udev

help: ## Show this help
	@echo "FluidTrack-Mini -- available targets:"
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- Setup -------------------------------------------------------------------

sync: ## Install/update the virtualenv from uv.lock (incl. dev tools)
	$(UV) sync

update: ## Upgrade all dependencies and refresh uv.lock
	$(UV) lock --upgrade
	$(UV) sync

# --- Run ---------------------------------------------------------------------

run: ## Start the desktop window (tkinter GUI)
	cd $(SRC) && $(UV) run python tray_app.py

run-cli: ## Start the console polling loop (no GUI)
	cd $(SRC) && $(UV) run python main.py

login: ## Nextcloud browser login, e.g. make login URL=https://cloud.example.com
	@test -n "$(URL)" || { echo "Usage: make login URL=https://your-nextcloud"; exit 1; }
	cd $(SRC) && $(UV) run python nextcloud_login.py "$(URL)"

cloud-sync: ## One-off: compare local DB with Nextcloud and upload missing rows
	cd $(SRC) && $(UV) run python -c "import main; db, cloud, enabled = main.initialize(); \
		main.sync_pending_transactions(db, cloud) if enabled else print('[i] Nextcloud is not configured.')"

# --- Hardware / diagnostics --------------------------------------------------

find-device: ## Check whether the DS9490R USB adapter is detected
	cd $(SRC) && $(UV) run python find-device.py

inspect-key: ## Hexdump the raw key memory (saves a .bin copy)
	cd $(SRC) && $(UV) run python inspect_key.py --save

udev: ## Linux: install udev rule so the DS9490R works without sudo
	echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="04fa", ATTR{idProduct}=="2490", MODE="0666"' | \
		sudo tee /etc/udev/rules.d/99-ds9490.rules
	sudo udevadm control --reload-rules
	sudo udevadm trigger
	@echo "Re-plug the adapter now."

# --- Quality / data ----------------------------------------------------------

check: ## Syntax-check all Python sources
	$(UV) run python -m py_compile $(wildcard $(SRC)/*.py)
	@echo "OK"

backup-db: ## Copy the source and build databases into backups/ (timestamped)
	@mkdir -p $(BACKUPS)
	@stamp=$$(date +%Y%m%d-%H%M%S); \
	for db in $(SRC)/FluidTrack.db $(DIST_DIR)/FluidTrack.db; do \
		if [ -f "$$db" ]; then \
			name=$$(echo "$$db" | tr '/' '_'); \
			cp -p "$$db" "$(BACKUPS)/$$stamp-$$name"; \
			echo "Saved $$db -> $(BACKUPS)/$$stamp-$$name"; \
		fi; \
	done

# --- Build -------------------------------------------------------------------
# A rebuild deletes dist/FluidTrack-Mini/, which holds the built app's .env and
# database. They are saved before and restored after every build. On the very
# first build, src/.env is copied next to the executable.

define pyinstaller_build
	@rm -rf $(KEEP_DIR) && mkdir -p $(KEEP_DIR)
	@for f in .env FluidTrack.db; do \
		[ -f "$(DIST_DIR)/$$f" ] && cp -p "$(DIST_DIR)/$$f" $(KEEP_DIR)/ || true; \
	done
	$(UV) run pyinstaller --noconfirm $(SPEC)
	@for f in .env FluidTrack.db; do \
		[ -f "$(KEEP_DIR)/$$f" ] && cp -p "$(KEEP_DIR)/$$f" $(DIST_DIR)/ || true; \
	done
	@if [ ! -f "$(DIST_DIR)/.env" ] && [ -f "$(SRC)/.env" ]; then \
		cp "$(SRC)/.env" $(DIST_DIR)/.env && echo "Copied $(SRC)/.env next to the executable."; \
	fi
	@echo "Built: $(EXE)"
endef

build: build-$(HOST_OS) ## Build for the current OS

build-linux: ## Build the Linux executable (run on Linux)
ifneq ($(HOST_OS),linux)
	$(error build-linux must run on Linux -- PyInstaller cannot cross-compile)
endif
	$(pyinstaller_build)

build-windows: ## Build the Windows .exe (run on Windows via Git Bash/MSYS2)
ifneq ($(HOST_OS),windows)
	$(error build-windows must run on Windows -- PyInstaller cannot cross-compile)
endif
	$(pyinstaller_build)

run-build: ## Start the built executable
	./$(EXE)

package: ## Zip/tar the build for distribution (WITHOUT .env and database)
	@test -f "$(EXE)" || { echo "Nothing built yet -- run 'make build' first."; exit 1; }
	@mkdir -p dist/packages
ifeq ($(HOST_OS),windows)
	cd dist && powershell -NoProfile -Command \
		"Get-ChildItem -Force -Path '$(APP)' -Exclude '.env','FluidTrack.db' | Compress-Archive -Force -DestinationPath 'packages/$(APP)-windows.zip'"
	@echo "Created dist/packages/$(APP)-windows.zip"
else
	tar -czf dist/packages/$(APP)-linux.tar.gz -C dist \
		--exclude='$(APP)/.env' --exclude='$(APP)/FluidTrack.db' $(APP)
	@echo "Created dist/packages/$(APP)-linux.tar.gz"
endif

# --- Cleanup -----------------------------------------------------------------

clean: ## Remove build cache and __pycache__ (keeps dist/ with .env and DB)
	rm -rf build $(SRC)/__pycache__
