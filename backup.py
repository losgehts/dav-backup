#!/usr/bin/env python3

import os
import fcntl
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, date
import shutil
import tempfile
import sys
import signal

import requests
from requests.auth import HTTPBasicAuth
from lxml import etree

from caldav import DAVClient
from icalendar import Calendar
from dotenv import dotenv_values


# =========================================================
# KONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

BACKUP_DIR = BASE_DIR / "backups"

CALENDAR_DIR = BACKUP_DIR / "calendar"
CONTACTS_DIR = BACKUP_DIR / "contacts"

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "backup.log"

ENV_FILE = BASE_DIR / ".env"

TODAY = datetime.now().strftime("%Y-%m-%d")

LOCK_FILE = BASE_DIR / f".backup_{TODAY}.lock"

SUCCESS_MARKER = ".backup_complete"

# =========================================================
# LOGGING
# =========================================================

def setup_logging() -> logging.Logger:

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("dav_backup")

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


logger = setup_logging()


# =========================================================
# SIGNAL HANDLING
# =========================================================
def handle_signal(signum, frame):
    logger.warning(f"Signal empfangen: {signum}")
    cleanup_and_exit(1)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# =========================================================
# HILFSFUNKTIONEN
# =========================================================

def sanitize_filename(name: str) -> str:
    return "".join(
        c for c in name
        if c.isalnum() or c in ("-", "_")
    ).strip()


def load_config():

    if not ENV_FILE.exists():
        logger.error(".env Datei nicht gefunden")
        sys.exit(1)

    env_stat = ENV_FILE.stat()

    if env_stat.st_mode & 0o077:
        logger.warning(
            ".env ist für andere Benutzer lesbar"
        )

    config = dotenv_values(ENV_FILE)

    required = [
        "CALDAV_URL",
        "CARDDAV_URL",
        "USERNAME",
        "PASSWORD",
    ]

    missing = [
        key for key in required
        if not config.get(key)
    ]

    if missing:
        logger.error(
            f"Fehlende Konfiguration: {', '.join(missing)}"
        )
        sys.exit(1)

    return config


def create_atomic_directory(target_dir: Path) -> Path:

    return Path(
        tempfile.mkdtemp(
            prefix=f"{target_dir.name}_",
            dir=target_dir.parent
        )
    )


def finalize_backup(temp_dir: Path, target_dir: Path):

    marker = temp_dir / SUCCESS_MARKER
    marker.write_text(
        datetime.now().isoformat(),
        encoding="utf-8"
    )

    if target_dir.exists():
        raise RuntimeError(
            f"Zielverzeichnis existiert bereits: "
            f"{target_dir}"
        )

    temp_dir.rename(target_dir)


def backup_complete(target_dir: Path) -> bool:

    marker = target_dir / SUCCESS_MARKER

    return (
        target_dir.exists()
        and target_dir.is_dir()
        and marker.exists()
    )


def remove_incomplete_backup(target_dir: Path):

    if target_dir.exists():

        marker = target_dir / SUCCESS_MARKER

        if not marker.exists():

            logger.warning(
                f"Entferne unvollständiges Backup: "
                f"{target_dir}"
            )

            shutil.rmtree(
                target_dir,
                ignore_errors=True
            )



# =========================================================
# KALENDER BACKUP
# =========================================================

def backup_calendars(config):

    logger.info("Starte Kalender-Backup")

    CALENDAR_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    target_dir = CALENDAR_DIR / TODAY

    remove_incomplete_backup(target_dir)

    temp_dir = create_atomic_directory(target_dir)

    success_count = 0

    try:

        client = DAVClient(
            url=config["CALDAV_URL"],
            username=config["USERNAME"],
            password=config["PASSWORD"],
        )

        principal = client.principal()

        calendars = principal.calendars()

        logger.debug(
            f"{len(calendars)} Kalender gefunden"
        )

        for calendar in calendars:

            try:

                display_name = (
                    calendar.get_display_name()
                    or "calendar"
                )

                logger.debug(
                    f"Exportiere Kalender: "
                    f"{display_name}"
                )

                cal = Calendar()

                events = calendar.events()

                for event in events:
                    cal.add_component(
                        event.icalendar_instance
                    )

                filename = (
                    f"{sanitize_filename(display_name)}.ics"
                )

                filepath = temp_dir / filename

                with open(filepath, "wb") as f:
                    f.write(cal.to_ical())

                success_count += 1

                logger.debug(
                    f"Kalender gespeichert: "
                    f"{filepath.name}"
                )

            except Exception:
                logger.exception(
                    f"Fehler bei Kalender "
                    f"'{calendar.name}'"
                )

        if success_count == 0:
            raise RuntimeError(
                "Kein Kalender erfolgreich exportiert"
            )

        finalize_backup(temp_dir, target_dir)

        logger.info(
            f"Kalender-Backup abgeschlossen "
            f"({success_count} erfolgreich)"
        )

    except Exception:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        raise


# =========================================================
# KONTAKTE BACKUP
# =========================================================

def backup_contacts(config):

    logger.info("Starte Kontakte-Backup")

    CONTACTS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    target_dir = CONTACTS_DIR / TODAY

    remove_incomplete_backup(target_dir)

    temp_dir = create_atomic_directory(target_dir)

    auth = HTTPBasicAuth(
        config["USERNAME"],
        config["PASSWORD"]
    )

    headers = {
        "Depth": "1",
        "Content-Type": "application/xml",
    }

    propfind_body = """<?xml version="1.0" encoding="utf-8" ?>
    <d:propfind xmlns:d="DAV:">
      <d:prop>
        <d:displayname />
      </d:prop>
    </d:propfind>
    """

    success_count = 0

    try:

        response = requests.request(
            "PROPFIND",
            config["CARDDAV_URL"],
            headers=headers,
            data=propfind_body,
            auth=auth,
            timeout=60,
        )

        response.raise_for_status()

        xml = etree.fromstring(response.content)

        ns = {
            "d": "DAV:"
        }

        addressbooks = []

        for resp in xml.findall("d:response", ns):

            href = resp.find("d:href", ns)

            displayname = resp.find(
                ".//d:displayname",
                ns
            )

            if href is None:
                continue

            addressbooks.append({
                "url": href.text,
                "name": (
                    displayname.text
                    if displayname is not None
                    else "adressbuch"
                )
            })

        logger.debug(
            f"{len(addressbooks)} Addressbooks gefunden"
        )

        for abook in addressbooks:

            try:

                logger.debug(
                    f"Exportiere Addressbook: "
                    f"{abook['name']}"
                )

                abook_url = abook["url"]

                if abook_url.startswith("/"):

                    parsed = requests.utils.urlparse(
                        config["CARDDAV_URL"]
                    )

                    abook_url = (
                        f"{parsed.scheme}"
                        f"://{parsed.netloc}"
                        f"{abook_url}"
                    )

                report_body = """<?xml version="1.0" encoding="utf-8" ?>
                <d:propfind xmlns:d="DAV:">
                  <d:prop>
                    <d:getcontenttype />
                  </d:prop>
                </d:propfind>
                """

                r = requests.request(
                    "PROPFIND",
                    abook_url,
                    headers=headers,
                    data=report_body,
                    auth=auth,
                    timeout=60,
                )

                r.raise_for_status()

                xml_contacts = etree.fromstring(
                    r.content
                )

                contacts = []

                for resp in xml_contacts.findall(
                    "d:response",
                    ns
                ):

                    href = resp.find(
                        "d:href",
                        ns
                    )

                    if href is None:
                        continue

                    href_text = href.text

                    if not href_text.endswith(".vcf"):
                        continue

                    contact_url = href_text

                    if contact_url.startswith("/"):

                        parsed = (
                            requests.utils.urlparse(
                                config["CARDDAV_URL"]
                            )
                        )

                        contact_url = (
                            f"{parsed.scheme}"
                            f"://{parsed.netloc}"
                            f"{contact_url}"
                        )

                    c = requests.get(
                        contact_url,
                        auth=auth,
                        timeout=60,
                    )

                    c.raise_for_status()

                    contacts.append(c.text)

                if not contacts:

                    logger.info(
                        f"Addressbook '{abook['name']}' "
                        f"ist leer"
                    )

                safe_name = sanitize_filename(
                    abook["name"]
                ) or "addressbook"

                backup_file = (
                    temp_dir / f"{safe_name}.vcf"
                )

                with open(
                    backup_file,
                    "w",
                    encoding="utf-8"
                ) as f:

                    for contact in contacts:

                        f.write(contact)

                        if not contact.endswith("\n"):
                            f.write("\n")

                success_count += 1

                logger.debug(
                    f"Addressbook gespeichert: "
                    f"{backup_file.name}"
                )

            except Exception:
                logger.exception(
                    f"Fehler bei Addressbook "
                    f"{abook['name']}"
                )

        if success_count == 0:
            raise RuntimeError(
                "Kein Addressbook erfolgreich exportiert"
            )

        finalize_backup(temp_dir, target_dir)

        logger.info(
            f"Kontakte-Backup abgeschlossen "
            f"({success_count} erfolgreich)"
        )

    except Exception:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        raise


# =========================================================
# RETENTION POLICY
# =========================================================

def should_keep_backup(
    backup_date: date,
    newest_date: date
) -> bool:

    age = newest_date - backup_date

    # jünger als 14 Tage
    if age.days < 14:
        return True

    # jünger als 6 Monate
    if age.days < 183:
        return backup_date.weekday() == 4

    # älter als 6 Monate:
    # nur erstes Backup im Monat
    return backup_date.day <= 7


def cleanup_backups(base_dir: Path):

    logger.info(
        f"Starte Bereinigung: {base_dir.name}"
    )

    if not base_dir.exists():
        return

    backups = []

    for path in base_dir.iterdir():

        if not path.is_dir():
            continue

        marker = path / SUCCESS_MARKER

        if not marker.exists():

            logger.warning(
                f"Unvollständiges Backup entfernt: "
                f"{path.name}"
            )

            shutil.rmtree(
                path,
                ignore_errors=True
            )

            continue

        try:

            backup_date = datetime.strptime(
                path.name,
                "%Y-%m-%d"
            ).date()

            backups.append((backup_date, path))

        except ValueError:

            logger.warning(
                f"Ungültiger Backupordner ignoriert: "
                f"{path.name}"
            )

    if not backups:
        return

    newest_date = max(d for d, _ in backups)

    for backup_date, path in backups:

        if should_keep_backup(
            backup_date,
            newest_date
        ):

            logger.debug(
                f"Behalte Backup: {path.name}"
            )

            continue

        logger.info(
            f"Lösche altes Backup: {path}"
        )

        shutil.rmtree(
            path,
            ignore_errors=True
        )


# =========================================================
# LOCK:  no parallel runs
# =========================================================
fd = None

def acquire_lock():
    global fd
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)


def cleanup_and_exit(code=1):
    global fd
    if fd is not None:
        try:
            os.close(fd)
            os.remove(LOCK_FILE)
        except Exception:
            pass
    sys.exit(code)


# =========================================================
# MAIN
# =========================================================
def main():

    exitcode = 0

    try:
        acquire_lock()
    except FileExistsError:
        logger.warning("Backup läuft bereits")
        # keincleanup_lock(fd) !! sondern sofortiger Abbruch
        sys.exit(0)

    try:
        logger.info("================================")
        logger.info("Backup gestartet")
        logger.info("================================")

        config = load_config()


        # Kalender
        try:

            if backup_complete(CALENDAR_DIR / TODAY):
                logger.warning(
                    "Kalenderbackup bereits vollständig."
                    "Wird nicht erneut angelegt."
                )
            else:
                backup_calendars(config)

        except Exception:

            logger.exception(
                "Kalender-Backup vollständig fehlgeschlagen"
            )
            exitcode += 10



        # Kontakte
        try:

            if backup_complete(CONTACTS_DIR / TODAY):
                logger.warning(
                    "Kontaktebackup bereits vollständig."
                    "Wird nicht erneut angelegt."
                )

            else:
                backup_contacts(config)

        except Exception:

            logger.exception(
                "Kontakte-Backup vollständig fehlgeschlagen"
            )
            exitcode += 100

        # alte Backups loeschen
        try:

            cleanup_backups(CALENDAR_DIR)
            cleanup_backups(CONTACTS_DIR)

        except Exception:

            logger.exception(
                "Fehler bei Backup-Bereinigung"
            )
            exitcode += 1000


        logger.info("================================")
        logger.info("Backup beendet")
        logger.info("================================")

    finally:
        if fd is not None:
            cleanup_and_exit(exitcode)

if __name__ == "__main__":
    main()