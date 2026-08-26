# postgres_server — Ansible role for PostgreSQL DBaaS deployment

Installs and configures PostgreSQL on Ubuntu, driven entirely by variables —
intended to be run from AWX with values supplied by a Job Template Survey
(and eventually by an orchestrator calling AWX's API on behalf of HostBill
order events).

## Requirements

- Target host: Ubuntu (22.04 or 24.04 tested; any release PGDG supports
  should work since the repo is selected via `ansible_distribution_release`)
- Ansible control side: collections in `requirements.yml` — AWX installs
  these automatically when it syncs the Project, as long as this file sits
  at the repo root.
- SSH access + sudo (`become: true`) on the target — set up via an AWX
  Machine Credential.

## Variables (the parameter contract)

All defined with defaults in `roles/postgres_server/defaults/main.yml`.
In production, every one of these should come from the AWX Survey /
orchestrator call — the defaults are for manual testing only.

| Variable                  | Meaning                                              | Example            |
|----------------------------|-------------------------------------------------------|---------------------|
| `pg_version`               | Postgres major version to install                     | `"16"`              |
| `pg_use_separate_data_disk`| Whether a second disk was attached for data            | `true`              |
| `pg_data_disk_device`      | Block device of that disk                              | `/dev/sdb`           |
| `pg_data_mount_point`      | Where to mount it / relocate PGDATA to                 | `/var/lib/postgresql/data` |
| `pg_data_fs_type`          | Filesystem to create                                   | `ext4`               |
| `pg_listen_addresses`      | postgresql.conf `listen_addresses`                     | `*`                  |
| `pg_port`                  | Port Postgres listens on                                | `5432`                |
| `pg_allowed_cidrs`         | List of CIDRs allowed to connect (pg_hba.conf)          | `["10.0.5.0/24"]`     |
| `pg_tier`                  | `small` / `medium` / `large` — selects tuning preset from `vars/main.yml` | `small` |
| `pg_app_db_name`           | Database created for the customer                       | `appdb`               |
| `pg_app_db_user`           | App user created                                          | `appuser`             |
| `pg_app_db_password`       | App user password — **must** be overridden, never left default | (from Vault) |

## What it does, in order

1. **repo_and_install.yml** — adds the PGDG apt repo matched to the
   target's Ubuntu release, installs the exact `pg_version` requested,
   starts the service.
2. **disk.yml** — if `pg_use_separate_data_disk`, formats + mounts the
   attached disk, stops Postgres, relocates PGDATA there via rsync,
   updates `data_directory`, grants AppArmor access to the new path,
   restarts. If not, just confirms the service is running on the
   default PGDG path.
3. **configure.yml** — sets `password_encryption = scram-sha-256`,
   drops tier-tuned settings into `conf.d/dbaas_overrides.conf`,
   templates `pg_hba.conf` from `pg_allowed_cidrs`.
4. **users_db.yml** — creates the app database, app user (scram
   password, no superuser), grants, and prints a deployment summary.

## Running it manually (outside AWX) for testing

```bash
ansible-galaxy collection install -r requirements.yml
ansible-playbook -i "TARGET_IP," deploy_postgres.yml \
  -u YOUR_SSH_USER --private-key ~/.ssh/id_rsa \
  -e "pg_version=16 pg_use_separate_data_disk=false pg_app_db_password=TestPass123!"
```

## Wiring into AWX

1. **Project** — point at this repo. AWX syncs `requirements.yml`
   automatically.
2. **Credential** — Machine type, SSH key matching what's trusted on
   target VMs (or the key CloudStack injects at deploy time).
3. **Inventory** — one host per deployment request; `ansible_host` set
   to the IP CloudStack returned. Can be built dynamically later.
4. **Job Template** — playbook = `deploy_postgres.yml`, attach the
   Project/Inventory/Credential above.
5. **Survey** — one field per variable in the table above. This
   Survey *is* the API contract: whatever calls
   `POST /api/v2/job_templates/{id}/launch/` needs to supply
   `extra_vars` matching these field names exactly.

## Known limitations / things to revisit

- Ubuntu only — `main.yml` asserts this and fails fast on anything else.
- No backup/WAL archiving configured yet (pgBackRest/WAL-G — future step).
- No monitoring agent install yet.
- Password is passed as a plain extra_var — fine for testing, but in
  production this should come from AWX's Vault credential type or an
  external secrets manager, not typed into a Survey field in cleartext.
- `pg_allowed_cidrs` defaults to `10.0.0.0/8` — must be tightened per
  deployment by whatever calls this, never left as a broad default in
  production.
