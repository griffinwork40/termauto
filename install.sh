#!/usr/bin/env bash
# termauto installer.
# Creates a project-local venv, installs the package editable, downloads the
# default small model, and offers to wire up your .zshrc.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

VENV_DIR="$REPO_DIR/.venv"
PYTHON_BIN="${TERMAUTO_PYTHON:-python3}"

echo "==> termauto install"
echo "    repo:    $REPO_DIR"
echo "    venv:    $VENV_DIR"
echo "    python:  $($PYTHON_BIN --version)"
echo

# 1. Create venv
if [[ ! -d "$VENV_DIR" ]]; then
  echo "==> creating venv"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

# 2. Install package
echo "==> installing termauto (editable)"
pip install --quiet --upgrade pip
pip install --quiet -e "$REPO_DIR"

# 3. Suggest shim for $PATH
SHIM_DIR="$HOME/.local/bin"
mkdir -p "$SHIM_DIR"
SHIM_PATH="$SHIM_DIR/termauto"
cat > "$SHIM_PATH" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/termauto" "\$@"
EOF
chmod +x "$SHIM_PATH"
echo "==> wrote shim: $SHIM_PATH"

if ! command -v termauto >/dev/null 2>&1; then
  case ":$PATH:" in
    *":$SHIM_DIR:"*) ;;
    *)
      echo
      echo "WARNING: $SHIM_DIR is not on \$PATH."
      echo "Add this to your shell profile:"
      echo "    export PATH=\"$SHIM_DIR:\$PATH\""
      ;;
  esac
fi

# 4. Optional: prefetch the default model
DEFAULT_MODEL="mlx-community/Qwen3-1.7B-4bit"
echo
echo "==> default model: $DEFAULT_MODEL (~1GB)"
read -r -p "Prefetch it now? [Y/n] " ans
ans="${ans:-Y}"
if [[ "$ans" =~ ^[Yy] ]]; then
  echo "==> downloading model (one-time)"
  "$VENV_DIR/bin/python" -c "from mlx_lm import load; load('$DEFAULT_MODEL'); print('ok')"
fi

# 5. Offer to install the zsh hook
echo
read -r -p "Add 'source shell/termauto.zsh' to ~/.zshrc? [Y/n] " ans
ans="${ans:-Y}"
if [[ "$ans" =~ ^[Yy] ]]; then
  "$VENV_DIR/bin/termauto" install-shell
fi

echo
echo "==> install complete."
echo
echo "Next steps:"
echo "  1) termauto start              # boot the daemon (loads model)"
echo "  2) open a new zsh, or run: source ~/.zshrc"
echo "  3) at the prompt, press Ctrl-Space to invoke the candidate panel"
echo "  4) termauto status             # confirm daemon is running"
echo
