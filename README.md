# postgres_server — Ansible role for PostgreSQL DBaaS deployment

Installs and configures PostgreSQL on Ubuntu, driven entirely by variables —
intended to be run from AWX with values supplied by a Job Template Survey
(and eventually by an orchestrator calling AWX's API on behalf of HostBill
order events).

## Requirements

- Target host: **Ubuntu** (22.04/24.04 tested; any release PGDG supports
  should work since the repo is selected via `ansible_distribution_release`).
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
| `pg_app_user_can_create_db`| Whether the app user gets `CREATEDB` (extra databases on this instance) | `false` |
| `target_ip`                | (top-level playbook, not the role) IP of the target VM — built into an in-memory inventory group via `add_host`, never a persisted AWX host | `10.0.5.50` |

## Access model for the customer's user

The app user is made **OWNER of both the database and its public schema**,
not granted the Postgres `SUPERUSER` attribute. Ownership gives them
everything reasonably meant by "admin on my database" — create/alter/drop
any object, manage extensions, grant privileges to any additional users
they create — without the ability to touch the OS or server-wide
configuration that real superuser would allow (arbitrary file access via
`COPY ... PROGRAM`, untrusted extensions, disabling logging, etc.). This
matters even though each customer has a dedicated VM, since it's what
keeps the instance in a state your own backup/monitoring/support tooling
can reliably reason about.

Additional roles/users beyond `pg_app_db_user` are handled by support on
request, not self-service — `pg_app_db_user` has `NOCREATEROLE`.

## What it does, in order

1. **install_postgres.yml** — adds the PGDG apt repo matched to the
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
4. **users_db.yml** — creates the app user, then the app database and
   public schema owned by that user, and prints a deployment summary.

A `meta: flush_handlers` runs between Step 3 and Step 4 to guarantee any
pending config restart (e.g. `password_encryption`) is actually applied
before the user's password gets created — Ansible only runs notified
handlers at the end of the play by default, which would otherwise let
Step 4 run against stale config.

## How the target VM is selected (no static inventory needed)

`deploy_postgres.yml` (the top-level playbook) takes `target_ip` as an
extra_var and uses `add_host` to build the target in-memory for that run
only — no AWX inventory objects are created, updated, or need cleanup
between requests. This is what makes concurrent runs against different
IPs safe by construction: nothing is written to shared AWX state.

## Running it manually (outside AWX) for testing

```bash
ansible-galaxy collection install -r requirements.yml
ansible-playbook deploy_postgres.yml \
  -u YOUR_SSH_USER --private-key ~/.ssh/id_rsa \
  -e "target_ip=TARGET_IP pg_version=16 pg_use_separate_data_disk=false pg_app_db_password=TestPass123"
```
(Avoid `!` in test passwords typed on an interactive shell — bash's
history expansion mangles it even inside single quotes. Use `set +H` if
you need one for a real test.)

## Wiring into AWX

1. **Project** — point at this repo. AWX syncs `requirements.yml`
   automatically.
2. **Credential** — Machine type, SSH key matching what's trusted on
   target VMs (or the key CloudStack injects at deploy time).
3. **Inventory** — the built-in `localhost`-only inventory is enough;
   `target_ip` supplies the real target per run (see above).
4. **Job Template** — playbook = `deploy_postgres.yml`, attach the
   Project/Inventory/Credential above. Enable **"Prompt on launch"** for
   Variables (or use a Survey) — without it, extra_vars sent at launch
   are silently ignored in favor of anything saved on the template.
   Enable **"Enable Concurrent Jobs"** so simultaneous requests for
   different VMs actually run in parallel rather than queuing.
5. **Survey** — one field per variable in the table above, plus
   `target_ip`. This Survey *is* the API contract: whatever calls
   `POST /api/v2/job_templates/{id}/launch/` needs to supply
   `extra_vars` matching these field names exactly. Booleans should be
   sent as real JSON booleans (`false`, not `"false"`) — the role
   guards the disk-toggle variable with a `| bool` filter for safety,
   but it's worth sending them correctly regardless.

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
- `community.postgresql` is pinned to a `3.x` range in `requirements.yml`
  after hitting a parameter rename (`update_password`) between minor
  versions — re-verify this range if bumping the collection later.
