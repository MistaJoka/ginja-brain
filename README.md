# ginja

A FOSS-first personal AI second brain for your terminal. Runs entirely locally
on Ollama + Qdrant — zero cloud APIs required by default.

```
ginja ask "how does cosine similarity work?"
ginja remember "my homelab IP is 192.168.1.10"
ginja recall "homelab network"
ginja ingest ~/notes/architecture.md
ginja chat
ginja status
```

## Requirements

- **Ollama** — `curl -fsSL https://ollama.com/install.sh | sh`
- **Docker** with Compose — for Qdrant
- **Python 3.10+** with pip
- **GPU**: GTX 970 (4GB VRAM) or better; CPU fallback works but is slow

## Install

```bash
git clone <this-repo> ~/ginja-brain
cd ~/ginja-brain
bash setup.sh
source ~/.bashrc
```

### Pull Ollama models

```bash
ollama pull nomic-embed-text    # embedding (required)
ollama pull llama3.2:3b         # fast model
ollama pull mistral:7b          # default general model
ollama pull qwen2.5-coder:7b    # code model
```

Then verify everything is working:

```bash
ginja status
```

## Subcommands

### `ginja ask`

One-shot question with streaming output and automatic RAG context injection.

```bash
ginja ask "what is docker compose?"
ginja ask --fast "quick summary of this"         # llama3.2:3b
ginja ask --code "write a python class for X"    # qwen2.5-coder:7b
ginja ask --hard "very complex reasoning task"   # Gemini (needs API key)
ginja ask --no-rag "clean slate, no memories"
ginja ask --model llama3.2:3b "specific model"
ginja ask --top-k 10 "use more memories"
```

Model auto-routing: if your question contains code blocks (` ``` `), file
extensions (`.py`, `.ts`, etc.), or multiple programming keywords, ginja
automatically routes to `qwen2.5-coder:7b`.

### `ginja remember`

Embed and store text or a file as a searchable memory.

```bash
ginja remember "I use vim with the gruvbox theme"
ginja remember --file ~/notes/meeting-2025.md
ginja remember --file ~/projects/plan.txt
```

Re-storing the same text is idempotent (same content → same UUID).

### `ginja recall`

Semantic search over all stored memories.

```bash
ginja recall "editor preferences"
ginja recall "homelab" --top-k 10
```

### `ginja ingest`

Chunk a file or URL, embed every chunk, and store them all.

```bash
ginja ingest ~/books/pragmatic-programmer.txt
ginja ingest https://en.wikipedia.org/wiki/Qdrant
ginja ingest ./codebase/README.md --source "project-docs"
ginja ingest ./notes.md --chunk-size 256 --overlap 30
```

### `ginja chat`

Interactive REPL with readline history, persistent sessions, and RAG.

```bash
ginja chat                          # new timestamped session
ginja chat --session work-project   # named session (resumable)
ginja chat --fast                   # use llama3.2:3b throughout
ginja chat --no-rag                 # disable memory injection
```

Type `exit`, `quit`, `:q`, or Ctrl-D to quit. Sessions are saved to
`~/.ginja/history/<session>.json` and can be resumed by name.

### `ginja status`

Shows connected Ollama models, Qdrant health, memory count, and current config.

```bash
ginja status
```

### `ginja serve`

Starts an HTTP server for n8n webhook integration.

```bash
ginja serve              # default port 8765
ginja serve --port 9000
```

## Shell Aliases

Installed automatically by `setup.sh`:

| Alias | Expands to |
|-------|-----------|
| `g`   | `ginja ask` |
| `gc`  | `ginja ask --code` |
| `gf`  | `ginja ask --fast` |
| `gh`  | `ginja ask --hard` |
| `gr`  | `ginja recall` |
| `gn`  | `ginja remember` |
| `gi`  | `ginja ingest` |
| `gs`  | `ginja status` |
| `gch` | `ginja chat` |

The `cd` command is wrapped to silently store your working directory as a
memory — letting you later ask things like _"where was I working on the auth
module?"_

## Configuration

Edit `~/.ginja/config.yaml` to override defaults:

```yaml
ollama_url: "http://localhost:11434"
qdrant_url: "http://localhost:6333"
embed_model: "nomic-embed-text"
default_model: "mistral:7b"
fast_model: "llama3.2:3b"
code_model: "qwen2.5-coder:7b"
top_k: 5
serve_port: 8765
```

Environment variable overrides: `GINJA_OLLAMA_URL`, `GINJA_QDRANT_URL`,
`GINJA_QDRANT_API_KEY`, `GINJA_GEMINI_API_KEY`, `GINJA_TOP_K`,
`GINJA_DEFAULT_MODEL`.

## Persona

Edit `~/.ginja/persona.md` to customise the system prompt injected into every
LLM call. This file is never overwritten by `setup.sh` after initial creation.

## Gemini Fallback

For unusually hard tasks, ginja can route to Google Gemini (free tier):

```bash
export GINJA_GEMINI_API_KEY="your-key-here"
ginja ask --hard "compare transformer architectures in depth"
```

Get a free key at [aistudio.google.com](https://aistudio.google.com/app/apikey).

## n8n Integration

### Architecture

```
n8n workflow
  └─ HTTP Request node
       └─ POST http://localhost:8765/ask
            body: {"query": "summarise today's emails"}
       ← {"answer": "...", "model": "mistral:7b"}
```

### Start the server

```bash
ginja serve &
```

Or as a persistent systemd user service:

```bash
cat > ~/.config/systemd/user/ginja-serve.service << 'EOF'
[Unit]
Description=Ginja HTTP server
After=network.target

[Service]
ExecStart=%h/bin/ginja serve
Restart=on-failure

[Install]
WantedBy=default.target
EOF

systemctl --user enable --now ginja-serve
```

### Endpoints

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `POST` | `/ask` | `{"query":"...", "fast":false, "hard":false, "no_rag":false}` | `{"answer":"...", "model":"..."}` |
| `POST` | `/remember` | `{"text":"...", "source":"n8n"}` | `{"stored":true, "id":"..."}` |
| `POST` | `/recall` | `{"query":"...", "top_k":5}` | `{"results":[...]}` |
| `GET`  | `/health` | — | `{"status":"ok"}` |

### n8n workflow example

1. **Trigger**: Telegram / webhook / schedule
2. **HTTP Request node**: `POST http://localhost:8765/ask` with JSON body `{"query": "{{ $json.message }}"}`
3. **Respond**: use `{{ $json.answer }}` in your reply node

## Docker Compose

Start/stop Qdrant:

```bash
docker compose up -d qdrant     # start
docker compose down             # stop (data persists in volume)
docker compose logs -f qdrant   # logs
```

Qdrant dashboard: [http://localhost:6333/dashboard](http://localhost:6333/dashboard)

To also run n8n, uncomment the `n8n` service block in `docker-compose.yml`.

## GTX 970 (4GB VRAM) Notes

- Ollama loads one model at a time; swapping takes ~3–5 seconds
- `num_ctx: 4096` is set automatically to cap VRAM usage
- For daily speed, set `default_model: "llama3.2:3b"` in config
- 7B models in 4-bit quant (~4GB) fit with nothing else running on the GPU
- Monitor VRAM: `nvidia-smi --query-gpu=memory.used,memory.free --format=csv -l 2`

## Troubleshooting

**`Cannot reach Ollama`** — Run `ollama serve` (or ensure the systemd service is running)

**`Cannot reach Qdrant`** — Run `docker compose up -d qdrant`

**`Model not found`** — Run `ollama pull <model-name>`

**Collection dimension mismatch** — If you change `embed_model`, delete and
recreate the collection:
```bash
# In Qdrant dashboard or via API:
curl -X DELETE http://localhost:6333/collections/ginja_memories
# Then re-ingest your content
```

**`cd` hook breaks something** — Temporarily disable with `unalias cd`.
Re-enable by sourcing `~/.ginja/shell.bashrc`.
