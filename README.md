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
novps databases list              # List databases
```

### Registry

```bash
novps registry list               # List registry namespaces
```

### Storage

```bash
novps storage list                # List S3 buckets
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
