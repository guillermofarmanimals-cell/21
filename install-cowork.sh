#!/usr/bin/env bash
#
# install-cowork.sh — Install Claude Cowork (Desktop) on Linux
#
# Usage:
#   ./install-cowork.sh              # auto-download DMG and install
#   ./install-cowork.sh Claude.dmg   # use a local DMG/ZIP you already have
#
# Requirements: Ubuntu/Debian 22.04+, Arch, Fedora 39+, or openSUSE
# Architecture: x86_64 only
# Account: Claude Pro or higher subscription required for Cowork
#
# Source: https://github.com/johnzfitch/claude-cowork-linux

set -euo pipefail

REPO_URL="https://github.com/johnzfitch/claude-cowork-linux.git"
INSTALL_DIR="$HOME/.local/share/claude-desktop"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()     { echo -e "${RED}[ERR ]${NC} $*" >&2; exit 1; }

# ── Preflight checks ─────────────────────────────────────────────────────────

[[ "$(uname -m)" == "x86_64" ]] || die "Only x86_64 is supported."

if [[ $EUID -eq 0 ]]; then
  die "Do not run as root. Run as your regular desktop user."
fi

echo ""
echo "========================================"
echo "  Claude Cowork Installer for Linux"
echo "========================================"
echo ""

# ── Step 1: System packages ──────────────────────────────────────────────────

info "Checking system dependencies..."

install_pkg() {
  if command -v apt-get &>/dev/null; then
    sudo apt-get install -y "$@"
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm --needed "$@"
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y "$@"
  elif command -v zypper &>/dev/null; then
    sudo zypper install -y "$@"
  else
    die "Unsupported package manager. Install manually: $*"
  fi
}

MISSING=()
for cmd in git 7z node npm bwrap; do
  command -v "$cmd" &>/dev/null || MISSING+=("$cmd")
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  info "Installing missing packages: ${MISSING[*]}"
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y git p7zip-full nodejs npm bubblewrap
  elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm --needed git p7zip nodejs npm bubblewrap
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y git p7zip nodejs npm bubblewrap
  elif command -v zypper &>/dev/null; then
    sudo zypper install -y git 7zip nodejs-default npm bubblewrap
  else
    die "Install these manually then re-run: git p7zip nodejs npm bubblewrap"
  fi
fi

# npm global to user prefix (avoids sudo for npm installs)
NPM_PREFIX="$HOME/.local"
mkdir -p "$NPM_PREFIX"
npm config set prefix "$NPM_PREFIX" 2>/dev/null || true
export PATH="$NPM_PREFIX/bin:$PATH"

command -v asar &>/dev/null || npm install --silent -g @electron/asar
command -v electron &>/dev/null || npm install --silent -g electron

for cmd in git 7z node npm asar electron bwrap; do
  command -v "$cmd" &>/dev/null && ok "Found: $cmd" || die "Still missing: $cmd"
done

NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
[[ "$NODE_VER" -ge 18 ]] || die "Node.js 18+ required (found v$NODE_VER)"
ok "Node.js v$NODE_VER"

# ── Step 2: Clone or update the cowork repo ──────────────────────────────────

info "Setting up claude-cowork-linux..."
if git -C "$INSTALL_DIR" rev-parse --is-inside-work-tree &>/dev/null 2>&1; then
  info "Updating existing install..."
  git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || warn "Local modifications present, skipping pull"
  ok "Repository updated"
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone "$REPO_URL" "$INSTALL_DIR"
  ok "Repository cloned"
fi

# ── Step 3: Get Claude Desktop archive ───────────────────────────────────────

ARCHIVE_ARG="${1:-}"
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

ARCHIVE="$WORK_DIR/Claude.archive"

if [[ -n "$ARCHIVE_ARG" ]]; then
  info "Using provided archive: $ARCHIVE_ARG"
  cp "$(realpath -e "$ARCHIVE_ARG")" "$ARCHIVE"
else
  info "Auto-downloading Claude Desktop (macOS DMG)..."
  DOWNLOAD_URL=$(node "$INSTALL_DIR/fetch-dmg.js" --url 2>/dev/null) || {
    warn "Could not auto-fetch download URL."
    echo ""
    echo "  Please download Claude Desktop manually:"
    echo "    https://claude.ai/download"
    echo "  Select 'macOS (Universal)', save it, then re-run:"
    echo "    $0 /path/to/Claude-*.dmg"
    echo ""
    exit 1
  }
  info "Downloading from CDN..."
  curl -fSL --progress-bar -o "$ARCHIVE" "$DOWNLOAD_URL" \
    || die "Download failed. Try downloading manually and passing the path as an argument."
  ok "Download complete"
fi

# ── Step 4–9: Run the cowork installer with our archive ──────────────────────

info "Running claude-cowork-linux installer..."
cd "$INSTALL_DIR"
CLAUDE_ARCHIVE="$ARCHIVE" bash install.sh

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
ok "Claude Cowork installed successfully!"
echo ""
echo "  Launch with:    claude-desktop"
echo "  Health check:   claude-desktop --doctor"
echo ""
echo "  Note: You need a Claude Pro (or higher) account to use Cowork."
echo "  Sign in at https://claude.ai after first launch."
echo ""
