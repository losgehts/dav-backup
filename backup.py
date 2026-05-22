#!/usr/bin/env python3

import os
import fcntl
import atexit
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta, date
import shutil
import tempfile
import sys

import requests
from requests.auth import HTTPBasicAuth
from lxml import etree

from caldav import DAVClient
from icalendar import Calendar
from dotenv import dotenv_values


# ============================================================
# KONFIGURATION
# ============================================================

LOCK_FILE = "/tmp/dav-backup.lock"
# LOCK_FILE = "/var/lock/dav-backup.lock" => Schreibreichte!!

BASE_DIR = Path(__file__).resolve().parent

BACKUP_DIR = BASE_DIR / "backups"
CALENDAR_DIR = BACKUP_DIR / "calendar"
CONTACTS_DIR = BACKUP_DIR / "contacts"

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "backup.log"

ENV_FILE = BASE_DIR / ".env"

TODAY = datetime.now().strftime("%Y-%m-%d")


# ============================================================
# LOGGING
# ============================================================

def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("dav_backup")
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


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

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
            ".env ist für andere Benutzer lesbar!"
        )

    config = dotenv_values(ENV_FILE)

    required = [
        "CALDAV_URL",
        "CARDDAV_URL",
        "USERNAME",
        "PASSWORD",
    ]

    missing = [key for key in required if key not in config]

    if missing:
        logger.error(f"Fehlende Konfiguration: {', '.join(missing)}")
        sys.exit(1)

    return config


def backup_exists() -> bool:
    calendar_today = CALENDAR_DIR / TODAY
    contacts_today = CONTACTS_DIR / TODAY

    return calendar_today.exists() or contacts_today.exists()


def create_atomic_directory(target_dir: Path) -> Path:
    temp_dir = Path(
        tempfile.mkdtemp(
            prefix=f"{target_dir.name}_",
            dir=target_dir.parent
        )
    )

    return temp_dir


def acquire_lock():
    lock_fp = open(LOCK_FILE, "w")

    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)

    except BlockingIOError:
        logger.warning(
            "Backup läuft bereits. Abbruch."
        )
        sys.exit(1)

    lock_fp.write(str(os.getpid()))
    lock_fp.flush()

    def cleanup():
        try:
            fcntl.flock(lock_fp, fcntl.LOCK_UN)
            lock_fp.close()

        except Exception:
            pass

    atexit.register(cleanup)

    return lock_fp


# ============================================================
# KALENDER BACKUP
# ============================================================

def backup_calendars(config):
    logger.info("Starte Kalender-Backup")

    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)

    target_dir = CALENDAR_DIR / TODAY

    temp_dir = create_atomic_directory(target_dir)

    try:
        client = DAVClient(
            url=config["CALDAV_URL"],
            username=config["USERNAME"],
            password=config["PASSWORD"],
        )

        principal = client.principal()

        calendars = principal.calendars()

        logger.debug(f"{len(calendars)} Kalender gefunden")

        for calendar in calendars:
            try:
                logger.debug(
                    f"Exportiere Kalender: {calendar.get_display_name()}"
                )

                cal = Calendar()

                for event in calendar.events():
                    cal.add_component(event.icalendar_instance)

                filename = (
                    f"{sanitize_filename(calendar.get_display_name())}.ics"
                )

                filepath = temp_dir / filename

                with open(filepath, "wb") as f:
                    f.write(cal.to_ical())

                logger.debug(
                    f"Kalender gespeichert: {filepath.name}"
                )

            except Exception as e:
                logger.error(
                    f"Fehler bei Kalender "
                    f"'{calendar.name}': {e}"
                )

        temp_dir.rename(target_dir)

        logger.info("Kalender-Backup abgeschlossen")

    except Exception as e:
        logger.error(f"Kalender-Backup fehlgeschlagen: {e}")

        shutil.rmtree(temp_dir, ignore_errors=True)

        raise


# ============================================================
# KONTAKT BACKUP
# ============================================================

def backup_contacts(config):
    logger.info("Starte Kontakte-Backup")

    CONTACTS_DIR.mkdir(parents=True, exist_ok=True)

    target_dir = CONTACTS_DIR / TODAY

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
    <d:propfind xmlns:d="DAV:"
                xmlns:cs="http://calendarserver.org/ns/">
      <d:prop>
        <d:displayname />
      </d:prop>
    </d:propfind>
    """

    try:
        response = requests.request(
            "PROPFIND",
            config["CARDDAV_URL"],
            headers=headers,
            data=propfind_body,
            auth=auth,
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

            if href is not None:
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

                    base = (
                        f"{parsed.scheme}://{parsed.netloc}"
                    )

                    abook_url = base + abook_url

                report_body = """<?xml version="1.0"
encoding="utf-8" ?>
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

                    href = resp.find("d:href", ns)

                    if href is None:
                        continue

                    href_text = href.text

                    if href_text.endswith(".vcf"):

                        contact_url = href_text

                        if contact_url.startswith("/"):

                            parsed = (
                                requests.utils.urlparse(
                                    config["CARDDAV_URL"]
                                )
                            )

                            base = (
                                f"{parsed.scheme}"
                                f"://{parsed.netloc}"
                            )

                            contact_url = (
                                base + contact_url
                            )

                        c = requests.get(
                            contact_url,
                            auth=auth
                        )

                        c.raise_for_status()

                        contacts.append(c.text)

                safe_name = sanitize_filename(
                    abook["name"]
                )

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

                logger.debug(
                    f"Addressbook gespeichert: "
                    f"{backup_file.name}"
                )

            except Exception as e:
                logger.error(
                    f"Fehler bei Addressbook "
                    f"{abook['name']}: {e}"
                )

        temp_dir.rename(target_dir)

        logger.info("Kontakte-Backup abgeschlossen")

    except Exception as e:
        logger.error(
            f"Kontakte-Backup fehlgeschlagen: {e}"
        )

        shutil.rmtree(temp_dir, ignore_errors=True)

        raise


# ============================================================
# RETENTION POLICY
# ============================================================

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

        shutil.rmtree(path, ignore_errors=True)


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info("================================")
    logger.info("Backup gestartet")
    logger.info("================================")

    acquire_lock()

    if backup_exists():
        logger.warning(
            "Backup für heute existiert bereits. "
            "Abbruch."
        )
        return

    config = load_config()

    try:
        backup_calendars(config)

    except Exception:
        logger.exception(
            "Kalender-Backup vollständig fehlgeschlagen"
        )

    # try:
    #     backup_contacts(config)

    # except Exception:
    #     logger.exception(
    #         "Kontakte-Backup vollständig fehlgeschlagen"
    #     )

    try:
        cleanup_backups(CALENDAR_DIR)
        cleanup_backups(CONTACTS_DIR)

    except Exception:
        logger.exception(
            "Fehler bei Backup-Bereinigung"
        )

    logger.info("================================")
    logger.info("Backup beendet")
    logger.info("================================")


if __name__ == "__main__":
    main()