#!/usr/bin/env bash
# ginja setup — idempotent installer
# Run from the repo directory: bash setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GINJA_DIR="$HOME/.ginja"
BIN_DIR="$HOME/bin"

echo "==> ginja setup starting..."
echo "    Repo: $REPO_DIR"

# 1. Create directory structure
mkdir -p "$GINJA_DIR/history" "$BIN_DIR"
echo "    Created ~/.ginja/history and ~/bin"

# 2. Install Python dependencies
echo "==> Installing Python dependencies..."
pip3 install --user --quiet click httpx qdrant-client rich pyyaml flask litellm
echo "    Dependencies installed"

# 3. Symlink the executable
chmod +x "$REPO_DIR/ginja"
ln -sf "$REPO_DIR/ginja" "$BIN_DIR/ginja"
echo "    Symlinked ~/bin/ginja → $REPO_DIR/ginja"

# 4. Create default config (never overwrites existing)
if [ ! -f "$GINJA_DIR/config.yaml" ]; then
    cat > "$GINJA_DIR/config.yaml" << 'EOF'
# ginja configuration — all values are optional (defaults are shown)
# Override any setting with a GINJA_* env var (e.g. GINJA_OLLAMA_URL)

ollama_url: "http://localhost:11434"
qdrant_url: "http://localhost:6333"

# Embedding model (must be pulled in Ollama)
embed_model: "nomic-embed-text"

# Generation models
default_model: "mistral:7b"      # ginja ask
fast_model:    "llama3.2:3b"     # ginja ask --fast
code_model:    "qwen2.5-coder:7b" # ginja ask --code (or auto-detected)

# Gemini fallback (ginja ask --hard)
# gemini_model: "gemini/gemini-1.5-flash"
# gemini_api_key: ""   # or set GINJA_GEMINI_API_KEY env var

# RAG settings
top_k: 5          # memories to retrieve per query

# ginja serve port
serve_port: 8765
EOF
    echo "    Created ~/.ginja/config.yaml"
else
    echo "    Keeping existing ~/.ginja/config.yaml"
fi

# 5. Install default persona (never overwrites existing)
if [ ! -f "$GINJA_DIR/persona.md" ]; then
    cp "$REPO_DIR/templates/persona.md.template" "$GINJA_DIR/persona.md"
    echo "    Created ~/.ginja/persona.md (edit this to customise your AI persona)"
else
    echo "    Keeping existing ~/.ginja/persona.md"
fi

# 6. Install shell integration
cp "$REPO_DIR/shell/ginja.bashrc" "$GINJA_DIR/shell.bashrc"
echo "    Installed ~/.ginja/shell.bashrc"

cp "$REPO_DIR/shell/auto-evolve.sh" "$GINJA_DIR/auto-evolve.sh"
chmod +x "$GINJA_DIR/auto-evolve.sh"
echo "    Installed ~/.ginja/auto-evolve.sh"

SHELL_LINE='[ -f "$HOME/.ginja/shell.bashrc" ] && source "$HOME/.ginja/shell.bashrc"'
BASHRC="$HOME/.bashrc"

if ! grep -qF "ginja/shell.bashrc" "$BASHRC" 2>/dev/null; then
    printf '\n# ginja shell integration\n%s\n' "$SHELL_LINE" >> "$BASHRC"
    echo "    Added shell integration to ~/.bashrc"
else
    echo "    Shell integration already in ~/.bashrc"
fi

# 7. Docker network
if docker network inspect homelab &>/dev/null 2>&1; then
    echo "    Docker network 'homelab' already exists"
else
    docker network create homelab
    echo "    Created Docker network 'homelab'"
fi

# 8. Start Qdrant
echo "==> Starting Qdrant..."
(cd "$REPO_DIR" && docker compose up -d qdrant)
echo "    Qdrant running at http://localhost:6333"

echo ""
echo "==> ginja installed successfully!"
echo ""
echo "    Next steps:"
echo "    1. Reload your shell:    source ~/.bashrc"
echo "    2. Pull Ollama models:"
echo "       ollama pull nomic-embed-text"
echo "       ollama pull llama3.2:3b"
echo "       ollama pull mistral:7b"
echo "       ollama pull qwen2.5-coder:7b"
echo "    3. Verify setup:         ginja status"
echo ""
echo "    Quick start:"
echo "      g 'what is the meaning of life?'           # alias for: ginja ask"
echo "      gn 'I prefer dark mode in all editors'     # store a memory"
echo "      gr 'editor preferences'                     # recall memories"
echo "      ginja chat                                  # interactive REPL"
