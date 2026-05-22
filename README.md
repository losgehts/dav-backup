# DAV Backup (whole repository is KI generated)

Simple CalDAV and CardDAV backup script for Linux systems.

This project creates daily backups of:
- calendars (.ics)
- contacts (.vcf)

It supports:
- automatic daily backups using cron
- backup rotation / cleanup
- logging
- protection against parallel runs
- secure file permissions

The project works well with providers like mailbox.org, Nextcloud, or any CalDAV/CardDAV server.

---

# Repository Contents

backup.py  
install.sh  
dav-backup-run.sh  
requirements.txt  
example.env  
.gitignore  

---

# Features

- Daily calendar backups
- Daily contact backups
- Atomic backups
- Automatic cleanup of old backups
- Rotating log files
- Dedicated Linux user (davbackup)
- Cron-based scheduling
- Protection against parallel execution

---

# Backup Structure
```
backups/
├── calendar/
│   └── YYYY-MM-DD/
│       └── *.ics
└── contacts/
    └── YYYY-MM-DD/
        └── *.vcf
``` 

---

# Retention Policy

- keep all backups for 14 days
- after 14 days: keep only Friday backups
- after 6 months: keep only the first backup of each month

---

# Installation

Clone the repository:

git clone <repository-url>
cd dav-backup

Copy the example configuration:
```
cp example.env .env
```

Edit `.env`:
```
CALDAV_URL=https://example.com/caldav/
CARDDAV_URL=https://example.com/carddav/
USERNAME=myuser
PASSWORD=mypassword
```

Run the installer as root:

sudo bash install.sh

The installer will:
- create the `davbackup` user
- install the project into `/opt/dav-backup`
- create a Python virtual environment
- install dependencies
- configure permissions
- create a cron job
- perform a test run

---

# Manual Backup

Run manually:

dav-backup-run.sh

This script:
- runs backup with correct Python environment
- uses dedicated `davbackup` user
- prints latest log entries

---

# Logs

```
/opt/dav-backup/logs/backup.log
```

---

# Security Notes

- credentials stored in `.env`
- `.env` restricted to `davbackup`
- backups and logs are not publicly readable
- .gitignore prevents accidental commits

---

# Requirements

- Linux
- Python 3
- cron

---

# License

MIT
