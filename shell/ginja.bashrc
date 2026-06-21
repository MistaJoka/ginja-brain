# ginja shell integration
# Installed by setup.sh — edit ~/.ginja/shell.bashrc to customise

# ── Ensure ~/bin is on PATH ───────────────────────────────────────────────────
if [[ ":$PATH:" != *":$HOME/bin:"* ]]; then
    export PATH="$HOME/bin:$PATH"
fi

# ── Aliases ───────────────────────────────────────────────────────────────────
alias g='ginja ask'
alias gc='ginja ask --code'
alias gf='ginja ask --fast'
alias gh='ginja ask --hard'
alias gr='ginja recall'
alias gn='ginja remember'
alias gi='ginja ingest'
alias gs='ginja status'
alias gch='ginja chat'

# ── cd hook: silently update working directory context ────────────────────────
_ginja_cd_hook() {
    builtin cd "$@" || return $?
    # Fire and forget — must never block or produce output
    (ginja context --dir "$PWD" &>/dev/null </dev/null &) 2>/dev/null
}
alias cd='_ginja_cd_hook'

# ── Tab completion ────────────────────────────────────────────────────────────
_ginja_completions() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local prev="${COMP_WORDS[COMP_CWORD-1]}"
    local subcommands="ask remember recall ingest chat status serve context"

    case "$prev" in
        ginja|g)
            COMPREPLY=($(compgen -W "$subcommands" -- "$cur"))
            return
            ;;
        ask)
            COMPREPLY=($(compgen -W "--fast --code --hard --no-rag --top-k --model" -- "$cur"))
            return
            ;;
        recall)
            COMPREPLY=($(compgen -W "--top-k" -- "$cur"))
            return
            ;;
        ingest)
            COMPREPLY=($(compgen -f -- "$cur"))
            return
            ;;
        remember)
            COMPREPLY=($(compgen -W "--file" -- "$cur"))
            return
            ;;
        chat)
            COMPREPLY=($(compgen -W "--fast --code --hard --no-rag --session" -- "$cur"))
            return
            ;;
    esac
    COMPREPLY=($(compgen -W "$subcommands" -- "$cur"))
}
complete -F _ginja_completions ginja
complete -F _ginja_completions g
