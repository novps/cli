# NoVPS CLI

Command-line tool for managing [novps.io](https://novps.io) infrastructure.

## Installation

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

## Usage

### Applications

```bash
novps apps list                    # List all applications
novps apps resources <app_id>     # List resources for an app
```

### Secrets

```bash
novps secrets list <app_id>       # List secret keys (values are masked)
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

### JSON output

All list commands support `--json` flag for machine-readable output:

```bash
novps apps list --json
novps databases list --json
```

## Configuration

| Setting | Source | Default |
|---------|--------|---------|
| Token | `~/.novps/config.json` | — |
| API URL | `NOVPS_API_URL` env var | `https://api.novps.app` |
