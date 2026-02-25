#!/bin/sh
set -eu

REPO="novps/cli"
BINARY_NAME="novps"

# --- Color output ---

setup_colors() {
    if [ -t 1 ] && [ -t 2 ]; then
        RED='\033[0;31m'
        GREEN='\033[0;32m'
        YELLOW='\033[0;33m'
        BLUE='\033[0;34m'
        BOLD='\033[1m'
        RESET='\033[0m'
    else
        RED=''
        GREEN=''
        YELLOW=''
        BLUE=''
        BOLD=''
        RESET=''
    fi
}

info() {
    printf '%s==>%s %s%s%s\n' "$BLUE" "$RESET" "$BOLD" "$1" "$RESET"
}

success() {
    printf '%s==>%s %s%s%s\n' "$GREEN" "$RESET" "$BOLD" "$1" "$RESET"
}

warn() {
    printf '%swarning:%s %s\n' "$YELLOW" "$RESET" "$1" >&2
}

error() {
    printf '%serror:%s %s\n' "$RED" "$RESET" "$1" >&2
    exit 1
}

# --- Detection ---

detect_os() {
    os="$(uname -s)"
    case "$os" in
        Linux*)  echo "linux" ;;
        Darwin*) echo "darwin" ;;
        *)       error "Unsupported operating system: $os" ;;
    esac
}

detect_arch() {
    arch="$(uname -m)"
    case "$arch" in
        x86_64)          echo "x86_64" ;;
        amd64)           echo "x86_64" ;;
        arm64)           echo "arm64" ;;
        aarch64)         echo "arm64" ;;
        *)               error "Unsupported architecture: $arch" ;;
    esac
}

# --- Download ---

download() {
    url="$1"
    output="$2"

    if command -v curl >/dev/null 2>&1; then
        curl --fail --silent --location --output "$output" "$url"
    elif command -v wget >/dev/null 2>&1; then
        wget --quiet --output-document="$output" "$url"
    else
        error "Neither curl nor wget found. Please install one of them and try again."
    fi
}

# --- Install directory ---

determine_install_dir() {
    # 1. User-specified via env var
    if [ -n "${NOVPS_INSTALL_DIR:-}" ]; then
        echo "$NOVPS_INSTALL_DIR"
        return
    fi

    # 2. ~/.local/bin if it exists in PATH
    local_bin="$HOME/.local/bin"
    case ":${PATH}:" in
        *":${local_bin}:"*)
            echo "$local_bin"
            return
            ;;
    esac

    # 3. Fall back to /usr/local/bin
    echo "/usr/local/bin"
}

needs_sudo() {
    install_dir="$1"
    if [ -w "$install_dir" ] 2>/dev/null || [ -w "$(dirname "$install_dir")" ] 2>/dev/null; then
        return 1
    fi
    return 0
}

ensure_dir() {
    dir="$1"
    if [ ! -d "$dir" ]; then
        if needs_sudo "$dir"; then
            info "Creating $dir (requires sudo)..."
            sudo mkdir -p "$dir"
        else
            mkdir -p "$dir"
        fi
    fi
}

# --- Main ---

main() {
    setup_colors

    printf '\n'
    printf '%s  novps CLI installer%s\n' "$BOLD" "$RESET"
    printf '\n'

    os="$(detect_os)"
    arch="$(detect_arch)"
    info "Detected platform: ${os}/${arch}"

    install_dir="$(determine_install_dir)"
    ensure_dir "$install_dir"

    artifact="novps-${os}-${arch}"
    url="https://github.com/${REPO}/releases/latest/download/${artifact}"

    info "Downloading ${artifact}..."
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' EXIT

    if ! download "$url" "${tmpdir}/${BINARY_NAME}"; then
        error "Failed to download ${url}. Check that a release exists for your platform."
    fi

    chmod +x "${tmpdir}/${BINARY_NAME}"

    info "Installing to ${install_dir}/${BINARY_NAME}..."
    if needs_sudo "$install_dir"; then
        sudo cp "${tmpdir}/${BINARY_NAME}" "${install_dir}/${BINARY_NAME}"
    else
        cp "${tmpdir}/${BINARY_NAME}" "${install_dir}/${BINARY_NAME}"
    fi

    # Verify installation
    if command -v novps >/dev/null 2>&1; then
        success "novps installed successfully!"
        printf "\n"
        novps --help
    else
        success "novps installed to ${install_dir}/${BINARY_NAME}"

        # Check if install_dir is in PATH
        case ":${PATH}:" in
            *":${install_dir}:"*)
                warn "Binary installed but 'novps' command not found. Try restarting your shell."
                ;;
            *)
                printf '\n'
                warn "${install_dir} is not in your PATH."
                printf '\n'
                printf '  Add it by running:\n'
                printf '\n'
                # shellcheck disable=SC2016
                printf '    %sexport PATH="%s:$PATH"%s\n' "$BOLD" "$install_dir" "$RESET"
                printf '\n'
                printf '  To make it permanent, add the line above to your shell profile:\n'
                printf '    ~/.bashrc, ~/.zshrc, or ~/.profile\n'
                printf '\n'
                ;;
        esac
    fi
}

main
