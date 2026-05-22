#!/usr/bin/env bash

set -euo pipefail

INSTALL_DIR="/opt/dav-backup"
APP_USER="davbackup"

sudo -u "$APP_USER" \
    "$INSTALL_DIR/venv/bin/python" \
    "$INSTALL_DIR/backup.py"
