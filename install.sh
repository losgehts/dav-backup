#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# KONFIGURATION
# ============================================================

APP_USER="davbackup"
APP_GROUP="davbackup"

INSTALL_DIR="/opt/dav-backup"

PYTHON_SCRIPT="backup.py"
ENV_FILE=".env"
REQUIREMENTS_FILE="requirements.txt"

PYTHON_BIN="/usr/bin/python3"

# CRON_SCHEDULE="30 2 * * *"
CRON_SCHEDULE="@reboot sleep 800 &&"

# ============================================================
# ROOT PRÜFUNG
# ============================================================

if [[ "$EUID" -ne 0 ]]; then
    echo "Bitte als root ausführen."
    exit 1
fi

# ============================================================
# USER ANLEGEN
# ============================================================

echo "==> Erstelle Benutzer"

if id "$APP_USER" &>/dev/null; then
    echo "Benutzer existiert bereits"
else
    useradd \
        --system \
        --create-home \
        --shell /usr/sbin/nologin \
        "$APP_USER"

    echo "Benutzer erstellt"
fi

# ============================================================
# VERZEICHNISSTRUKTUR
# ============================================================

echo "==> Erstelle Verzeichnisse"

mkdir -p "$INSTALL_DIR"

mkdir -p "$INSTALL_DIR/backups/calendar"
mkdir -p "$INSTALL_DIR/backups/contacts"

mkdir -p "$INSTALL_DIR/logs"

# ============================================================
# DATEIEN KOPIEREN
# ============================================================

echo "==> Kopiere Dateien"

cp "$PYTHON_SCRIPT" "$INSTALL_DIR/"
cp "$ENV_FILE" "$INSTALL_DIR/"
cp "$REQUIREMENTS_FILE" "$INSTALL_DIR/"

# ============================================================
# .gitignore ERSTELLEN
# ============================================================

echo "==> Erstelle .gitignore"

cat > "$INSTALL_DIR/.gitignore" <<EOF
# credentials
.env

# python
__pycache__/
*.pyc
*.pyo

# virtualenv
venv/

# logs
logs/
*.log

# backups
backups/

# editor
.vscode/
.idea/

# system
.DS_Store
EOF

# ============================================================
# PYTHON VENV
# ============================================================

echo "==> Erstelle Python Virtual Environment"

if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
fi

echo "==> Installiere Python-Abhängigkeiten"

"$INSTALL_DIR/venv/bin/pip" install --upgrade pip

"$INSTALL_DIR/venv/bin/pip" install \
    -r "$INSTALL_DIR/requirements.txt"

# ============================================================
# DATEIRECHTE
# ============================================================

echo "==> Setze Berechtigungen"

chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"

# Projektverzeichnis
chmod 750 "$INSTALL_DIR"

# .env nur Besitzer
chmod 600 "$INSTALL_DIR/.env"

# Python-Datei
chmod 750 "$INSTALL_DIR/backup.py"

# Logs
chmod 700 "$INSTALL_DIR/logs"

# Backups
chmod -R 700 "$INSTALL_DIR/backups"

# ============================================================
# CRONJOB
# ============================================================

echo "==> Richte Cronjob ein"

CRON_LINE="$CRON_SCHEDULE $INSTALL_DIR/venv/bin/python $INSTALL_DIR/backup.py"

CRON_TMP=$(mktemp)

if crontab -u "$APP_USER" -l &>/dev/null; then
    crontab -u "$APP_USER" -l > "$CRON_TMP"
fi

if grep -Fq "$INSTALL_DIR/backup.py" "$CRON_TMP"; then
    echo "Cronjob existiert bereits"
else
    echo "$CRON_LINE" >> "$CRON_TMP"

    crontab -u "$APP_USER" "$CRON_TMP"

    echo "Cronjob erstellt"
fi

rm -f "$CRON_TMP"

# ============================================================
# TESTLAUF
# ============================================================

echo "==> Starte Testlauf"

sudo -u "$APP_USER" \
    "$INSTALL_DIR/venv/bin/python" \
    "$INSTALL_DIR/backup.py"

# ============================================================
# ABSCHLUSS
# ============================================================

echo
echo "========================================"
echo "Installation abgeschlossen"
echo "========================================"
echo
echo "Installationspfad:"
echo "  $INSTALL_DIR"
echo
echo "Logdatei:"
echo "  $INSTALL_DIR/logs/backup.log"
echo
echo "Cronjob:"
echo "  $CRON_LINE"
echo