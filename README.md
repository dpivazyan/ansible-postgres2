# postgres_server — Ansible role for PostgreSQL DBaaS deployment

Installs and configures PostgreSQL on Ubuntu, driven entirely by variables —
intended to be run from AWX with values supplied by a Job Template Survey
(and eventually by an orchestrator calling AWX's API on behalf of HostBill
order events).

## Requirements

- Target host: **Ubuntu** (22.04/24.04) or **Rocky Linux / AlmaLinux** (9.x) —
  branches automatically on `ansible_os_family` (Debian vs RedHat).
- Ansible control side: collections in `requirements.yml` — AWX installs
  these automatically when it syncs the Project, as long as this file sits
  at the repo root.
- SSH access + sudo (`become: true`) on the target — set up via an AWX
  Machine Credential.

## OS support notes

The role branches on `ansible_os_family` in a few places where the two
distro families genuinely differ, not just in package names:

| Aspect | Debian/Ubuntu | Rocky/AlmaLinux |
|---|---|---|
| PGDG repo | apt `.list` + signing key | RPM package from PGDG's yum repo |
| Extra step | — | disable AppStream's built-in `postgresql` module first |
| initdb | automatic on install | explicit `postgresql-{{ version }}-setup initdb` |
| Config file location | always `/etc/postgresql/{{ version }}/main/` | inside the data directory itself — moves if PGDATA moves |
| conf.d auto-included | yes (Debian packaging default) | no — role adds the `include_dir` line explicitly |
| PGDATA relocation gate | AppArmor profile update | SELinux context (`sefcontext` + `restorecon`) |
| Service naming | `postgresql@{{ version }}-main` | `postgresql-{{ version }}` |
| Firewall | not touched (ufw usually inactive on cloud images) | `firewalld` port opened explicitly |

All of this is captured in `vars/Debian.yml` / `vars/RedHat.yml`, loaded
automatically in `tasks/main.yml` — the disk/config/user task files
themselves reference variables like `pg_config_dir` and `pg_service_name`
rather than hardcoding paths, so they don't need to know which OS they're
running on.


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

1. **install_Debian.yml / install_RedHat.yml** (branched by `ansible_os_family`)
   — adds the PGDG repo, installs the exact `pg_version` requested, starts
   the service. RHEL path additionally disables the conflicting AppStream
   module and runs `initdb` explicitly.
2. **disk.yml** — if `pg_use_separate_data_disk`, formats + mounts the
   attached disk, stops Postgres, relocates PGDATA there via rsync, then
   repoints the service at the new location (`data_directory` on Debian,
   the systemd sysconfig file on RHEL), grants access via AppArmor
   (Debian) or SELinux context (RHEL), restarts. If not, just confirms
   the service is running on the default path.
3. **configure.yml** — sets `password_encryption = scram-sha-256`, ensures
   conf.d is included (explicit on RHEL, automatic on Debian), drops
   tier-tuned settings into `conf.d/dbaas_overrides.conf`, templates
   `pg_hba.conf`, opens the firewalld port on RHEL.
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

- Ubuntu + Rocky/AlmaLinux only — `main.yml` asserts this and fails fast
  on anything else.
- RHEL disk-relocation path (SELinux context, sysconfig PGDATA override)
  has not yet been run against a real second disk — worth a dedicated
  test pass on an AlmaLinux VM with an attached data disk before relying
  on it in production.
- No backup/WAL archiving configured yet (pgBackRest/WAL-G — future step).
- No monitoring agent install yet.
- Password is passed as a plain extra_var — fine for testing, but in
  production this should come from AWX's Vault credential type or an
  external secrets manager, not typed into a Survey field in cleartext.
- `pg_allowed_cidrs` defaults to `10.0.0.0/8` — must be tightened per
  deployment by whatever calls this, never left as a broad default in
  production.
