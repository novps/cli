# NoVPS CLI

Command-line tool for managing [novps.io](https://novps.io) infrastructure.

## Installation

```bash
curl https://cli.novps.io | sh
```

This will download the appropriate binary for your platform and install it to `~/.local/bin` or `/usr/local/bin`.

You can specify a custom install directory:

```bash
NOVPS_INSTALL_DIR=~/bin curl https://cli.novps.io | sh
```

### From source

Requires Python 3.12+.

```bash
pip install .
```

For development:

```bash
pip install -e .
```

## Authentication

Create a Personal Access Token in the NoVPS web dashboard, then:

```bash
novps auth login
# Enter your token when prompted (nvps_...)
```

Other auth commands:

```bash
novps auth status   # Show current auth status
novps auth logout   # Remove saved token
```

Token is stored in `~/.novps/config.json`.

### Multi-project setup

All commands accept `--project` (`-p`) option to work with multiple projects:

```bash
novps auth login --project=staging
novps apps list --project=staging
```

## Usage

### Applications

```bash
novps apps list                   # List all applications
novps apps resources <app_id>     # List resources for an app
```

### Resources

```bash
novps resources get <resource_id>           # Show resource details
novps resources logs <resource_id>          # View resource logs
novps resources logs <resource_id> -f       # Follow log output
novps resources logs <resource_id> -n 500   # Last 500 lines
novps resources logs <resource_id> --since 30m --search "error"
```

### Secrets

```bash
novps secrets list <app_id>                    # List secret keys (values masked)
novps secrets list <app_id> --with-values      # Include secret values
novps secrets list <app_id> -r <resource_id>   # Resource-level secrets
novps secrets get <app_id> DATABASE_URL        # Get a single secret value
```

### Databases

```bash
novps databases list                                  # List databases
novps databases get <id>                              # Show details (table)
novps databases get <id> --show-password              # Include password + DATABASE_URL
novps databases get <id> --format env --show-password # Print as DB_HOST=, DATABASE_URL=, etc.
novps databases get <id> --format json
novps databases create --engine postgres --size sm    # Create (engine: postgres|redis, size: xs..xl, default count=1)
novps databases create --engine postgres --size sm --postgres-version 16 --count 1
novps databases create --engine postgres --size sm --wait   # Wait (with spinner) until the database is ready
novps databases delete <id>                            # Delete (prompts to type DELETE)
novps databases delete <id> --force                    # Delete without confirmation
novps databases resize <id> --size lg [--count 2]     # Resize (only upscale is allowed)
novps databases allow-apps <id> --app <uuid> --app <uuid>   # Restrict inbound to listed apps; no --app clears
```

#### Read-only replica (postgres)

```bash
novps databases replica create <db_id> --size sm
novps databases replica resize <db_id> --size md
novps databases replica delete <db_id>
```

#### Backups (postgres)

```bash
novps databases backups list <db_id>
novps databases backups create <db_id>
novps databases backups delete <db_id> <backup_id> [--force]
```

#### Connection pools (postgres)

```bash
novps databases pool list <db_id>
novps databases pool create <db_id> --size 50 --mode transaction --target primary
novps databases pool update <db_id> <pool_id> --size 80 --mode session
novps databases pool delete <db_id> <pool_id> [--force]
```

#### Postgres databases inside an instance

```bash
novps databases pg-db list <db_id>
novps databases pg-db create <db_id> --name analytics
novps databases pg-db delete <db_id> <entry_id> [--force]
```

#### Postgres users inside an instance

```bash
novps databases pg-user list <db_id>                            # passwords hidden
novps databases pg-user list <db_id> --show-password            # passwords in clear
novps databases pg-user create <db_id> --name app_user \
    --grant analytics=ro --grant reporting=all                  # password printed once in output
novps databases pg-user delete <db_id> <entry_id> [--force]
```

### Registry

```bash
novps registry list               # List registry namespaces
```

### Storage

Buckets and keys are referenced by their **unique identifier** (`internal_domain` for buckets, `internal_name` for keys), not the display name — names are not guaranteed unique within a project. Run the corresponding `list` command to see identifiers in the first column.

```bash
novps storage list                                    # List S3 buckets (first column = identifier)
novps storage create my-bucket [--region eu]          # Display name; identifier is returned in output
novps storage delete <bucket> [--force]               # Delete a bucket (prompts to type DELETE)
novps storage set-access <bucket> private|public-read|public-full
```

#### Files

```bash
novps storage files list <bucket> [--path logs/] [--page-size 100]
novps storage files list <bucket> --all               # Fetch all pages
novps storage files list <bucket> --continuation-token <token>   # Resume pagination

novps storage files upload <bucket> ./data.bin [--key path/data.bin] [--content-type application/octet-stream]
novps storage files download <bucket> path/data.bin [-o ./local.bin] [--duration 600]

novps storage files rename <bucket> old/key.txt new/key.txt
novps storage files delete <bucket> key1 key2 ... [--force]
```

#### Access keys

```bash
novps storage keys list
novps storage keys create prod-key --bucket <bucket-a>:rw --bucket <bucket-b>:ro   # Secret shown once
novps storage keys update <key> [--name new-name] [--bucket <bucket-a>:rw]         # --bucket replaces permissions
novps storage keys update <key> --replace-permissions                              # Clear all permissions
novps storage keys regenerate <key> [--force]                                      # Old secret stops working
novps storage keys delete <key> [--force]
```

### Port forwarding

```bash
novps port-forward resource <resource_id> <remote_port>          # Forward to a resource
novps port-forward resource <resource_id> <remote_port> -l 8080  # Custom local port
novps port-forward database <database_id>                        # Forward to a database
novps port-forward database <database_id> -l 5433                # Custom local port
```

### JSON output

All list/get commands support `--json` flag for machine-readable output:

```bash
novps apps list --json
novps resources get <resource_id> --json
novps secrets list <app_id> --json
```

## Configuration

| Setting | Source | Default |
|---------|--------|---------|
| Token | `~/.novps/config.json` | — |
| API URL | `NOVPS_API_URL` env var | `https://api.novps.app` |
| WebSocket URL | `NOVPS_WS_URL` env var | `wss://api.novps.app` |

## Supported platforms

| OS | Architecture | Binary |
|----|-------------|--------|
| Linux | x86_64 | `novps-linux-x86_64` |
| Linux | arm64 | `novps-linux-arm64` |
| macOS | x86_64 | `novps-darwin-x86_64` |
| macOS | arm64 (Apple Silicon) | `novps-darwin-arm64` |
