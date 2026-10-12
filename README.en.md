<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/manticore-logo-dark.png">
    <img src="docs/images/manticore-logo.png" alt="Manticore" width="420">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/UnicornisIT/manticore/actions/workflows/ci.yml"><img src="https://github.com/UnicornisIT/manticore/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/UnicornisIT/manticore/actions/workflows/security.yml"><img src="https://github.com/UnicornisIT/manticore/actions/workflows/security.yml/badge.svg?branch=main" alt="Security"></a>
  <a href="https://github.com/UnicornisIT/manticore/releases"><img src="https://img.shields.io/github/v/release/UnicornisIT/manticore?include_prereleases&sort=semver" alt="Release"></a>
  <img src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white" alt="Python 3.11">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/UnicornisIT/manticore" alt="License: MIT"></a>
</p>

<p align="center">
  <a href="README.md">Русский</a> · <b>English</b>
</p>

# Manticore

Manticore is a Flask/SQLite application with a Windows client for university
and college admissions offices: applicant import, reconciliation against
enrollment orders, student and group management, data quality checks and
Moodle export preparation.

> **Status: alpha.** The project already handles personal and production data,
> but is not yet considered fully stable. Before deploying it, your organization
> should verify backup and restore, role separation, HTTPS/VPN, file permissions
> and the update plan. Do not expose the application directly to the internet
> and never rely on a single copy of the database.

## Features

- Excel/CSV import with preview and validation;
- admission campaigns, duplicate detection, login conflicts and archiving;
- two-step transfer of applicants to students, reconciled against the
  enrollment order;
- user roles, audit log and SQLite backups;
- Windows desktop mode with a local or server-hosted database;
- a safe `cron` placeholder in the Moodle password field: Manticore never
  accepts or stores real student passwords.

## Quick start

Requires CPython 3.11 x64.

### Windows

1. Download the repository or a release.
2. Run `start_one_click.bat`.
3. Enter the initial administrator password at the hidden prompt.
4. Open the local address shown and change the password on first sign-in.

### Manual setup

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

On Windows use `py -3.11 -m venv .venv` and `.venv\Scripts\python.exe`.

Copy `.env.example` to `.env`, set strong `SECRET_KEY` and
`ADMIN_DEFAULT_PASSWORD` values, then start the app:

```bash
.venv/bin/python app.py
```

Detailed commands for Windows, Linux and production are in the
[installation guide](docs/installation.md).

## Documentation

The detailed guides are currently available in Russian only.

- [Installation and configuration](docs/installation.md)
- [Roles and workflow](docs/roles.md)
- [Import and export file formats](docs/file-formats.md)
- [Linux deployment and operations](docs/deployment.md)
- [Security and residual risks](docs/security.md)
- [Windows desktop and updates](docs/desktop-updates.md)
- [Updating the application](docs/updates.md)
- [Project structure](docs/project-structure.md)
- [Changelog](CHANGELOG.md) and [release notes](docs/releases/)
- [Contributing](CONTRIBUTING.md)
- [Vulnerability disclosure policy](SECURITY.md)

## Project structure

```text
app.py                     Flask app factory and compatible business wiring
config.py                  typed runtime configuration
db/                        registry of versioned SQLite migrations
routes/                    Blueprints: auth, abiturients, students, files, admin,
                           migration and management
services/                  file import and pure reconciliation logic
contingent.py              compatibility import of services/reconciliation.py
contingent_web.py          web layer for enrollment reconciliation
db_safety.py               SQLite integrity, versioning, backup/restore
email_validation.py        syntax, DNS and mail-domain reference checks
desktop_releases.py        allowed Windows client releases
app_metadata.py            single source of public contact details
sync_app_metadata.py       syncs contact details into the READMEs
data/                      static email_providers.json reference, not a runtime DB
desktop/                   Windows client, packaging and release tooling
deploy/                    systemd, Nginx and sudoers examples
scripts/                   canonical run, update and check scripts
static/, templates/        UI and static file templates
tests/                     Python and frontend regression tests
VERSION                    single application version
RELEASE_CHECKLIST.md       release checklist
```

A full map of files and their purpose is in
[docs/project-structure.md](docs/project-structure.md).

## Operating safely

- put any network access behind an HTTPS reverse proxy or a trusted VPN;
- keep `.env`, SQLite databases, uploads and backups out of Git;
- install Linux production dependencies from `requirements-prod.lock`;
- run `python -m unittest discover -s tests` before every update;
- run `pip-audit` against the prod and desktop lock files;
- remember that backups are protected only by file permissions and are not yet
  encrypted by the application itself.

## License

Released under the [MIT](LICENSE) license.

<!-- app-metadata:start (generated by sync_app_metadata.py) -->
### Developer

Developer: UnicornisIT

[GitHub](https://github.com/UnicornisIT) · [Telegram](https://t.me/unicornis_pulse) · [Email](mailto:dev-eva@outlook.com)
<!-- app-metadata:end -->
