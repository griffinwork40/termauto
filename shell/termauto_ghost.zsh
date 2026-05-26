# termauto — ghost-text strategy for zsh-autosuggestions
# --------------------------------------------------------
# Inline single-suggestion completion. Renders greyed ghost text past the
# cursor as you type, sourced from the termauto LLM daemon. Tab accepts.
#
# REQUIREMENTS:
#   - zsh-autosuggestions installed and sourced
#   - termauto daemon running (`termauto start`)
#
# INSTALL — add to ~/.zshrc, AFTER zsh-autosuggestions is sourced:
#
#     source /opt/homebrew/share/zsh-autosuggestions/zsh-autosuggestions.zsh
#     source /path/to/termauto/shell/termauto_ghost.zsh
#
# Optional: source termauto.zsh too for the Ctrl-Space panel widget. The two
# coexist — one binds a key, the other plugs into the autosuggest hook chain.
#
# HOW IT WORKS
# zsh-autosuggestions composes multiple suggestion sources in order via the
# ZSH_AUTOSUGGEST_STRATEGY array. We prepend `history` so the LLM only fires
# on history misses — most of the time you don't need the model at all. The
# plugin already forks an async child process before calling strategy
# functions, so blocking curl calls inside this function do NOT freeze your
# prompt; the child returns whenever, and the suggestion appears.

# ---- Config (override before sourcing) ------------------------------------
: ${TERMAUTO_GHOST_HOST:=127.0.0.1}
: ${TERMAUTO_GHOST_PORT:=8765}
: ${TERMAUTO_GHOST_TIMEOUT_MS:=400}   # hard ceiling on daemon round-trip
: ${TERMAUTO_GHOST_MIN_CHARS:=2}      # don't fire on 1-char buffers
: ${TERMAUTO_GHOST_MAX_TOKENS:=40}    # passed to the daemon as a generation cap
: ${TERMAUTO_GHOST_DISABLED:=0}       # 1 = no-op (kill switch without unsourcing)

# Register ourselves into the autosuggestions strategy chain. We don't clobber
# any existing user-set chain — if the user already configured something, we
# append. Otherwise we set a sensible default of (history termauto_inline).
typeset -ga ZSH_AUTOSUGGEST_STRATEGY
if (( ${#ZSH_AUTOSUGGEST_STRATEGY[@]} == 0 )); then
  ZSH_AUTOSUGGEST_STRATEGY=(history termauto_inline)
elif (( ${ZSH_AUTOSUGGEST_STRATEGY[(I)termauto_inline]} == 0 )); then
  # Not already in the chain — append so prior strategies short-circuit first.
  ZSH_AUTOSUGGEST_STRATEGY+=(termauto_inline)
fi

# ---- Helpers --------------------------------------------------------------
# JSON-escape a single string. Pure zsh — no jq dependency.
_termauto_ghost_json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  # Strip ASCII control chars below 0x20 (except handled \n \r \t already)
  s="${s//[$'\x01'-$'\x08'$'\x0b'$'\x0c'$'\x0e'-$'\x1f']/}"
  print -r -- "$s"
}

# Build the recent_commands JSON array from termauto.zsh's _TERMAUTO_RECENT
# (a parallel array of "cmd<TAB>exit" entries). If that file isn't sourced
# we just send [].
_termauto_ghost_recent_json() {
  if (( ${+_TERMAUTO_RECENT} )) && (( ${#_TERMAUTO_RECENT[@]} > 0 )); then
    if typeset -f _termauto_recent_json >/dev/null 2>&1; then
      _termauto_recent_json
      return
    fi
  fi
  print -r -- "[]"
}

# ---- The strategy function ------------------------------------------------
# zsh-autosuggestions contract:
#   - function name MUST be _zsh_autosuggest_strategy_<strategy-name>
#   - $1 is the current buffer (prefix to complete)
#   - set the variable `suggestion` in caller scope to the proposed suggestion
#   - the suggestion MUST start with $1 or the plugin discards it
#   - returning without setting `suggestion` means "no suggestion from me"
_zsh_autosuggest_strategy_termauto_inline() {
  (( TERMAUTO_GHOST_DISABLED )) && return

  local buffer="$1"

  # Guard rails before we burn a daemon round-trip:
  # - Skip very short buffers (history strategy handles those better)
  # - Skip leading-whitespace buffers (user is probably mid-edit indentation)
  # - Skip buffers ending in trailing whitespace beyond a single space
  #   (the user is between tokens — let them type the next token before we guess)
  (( ${#buffer} < TERMAUTO_GHOST_MIN_CHARS )) && return
  [[ "$buffer" = ' '* ]] && return
  [[ "$buffer" = *'  ' ]] && return

  # Build payload
  local buf_esc cwd_esc recent_json
  buf_esc=$(_termauto_ghost_json_escape "$buffer")
  cwd_esc=$(_termauto_ghost_json_escape "$PWD")
  recent_json=$(_termauto_ghost_recent_json)

  local payload="{\"buffer\":\"$buf_esc\",\"cwd\":\"$cwd_esc\",\"recent_commands\":$recent_json,\"max_tokens\":$TERMAUTO_GHOST_MAX_TOKENS}"

  # Hard timeout. curl --max-time accepts fractional seconds.
  local max_time
  max_time=$(( TERMAUTO_GHOST_TIMEOUT_MS / 1000.0 ))

  # Request text/plain — daemon returns the bare suggestion (or empty body).
  # No JSON parsing in the shell hot path.
  local response
  response=$(
    curl -sf -X POST \
      --max-time "$max_time" \
      -H 'Accept: text/plain' \
      -H 'Content-Type: application/json' \
      --data-binary "$payload" \
      "http://${TERMAUTO_GHOST_HOST}:${TERMAUTO_GHOST_PORT}/suggest_inline" \
      2>/dev/null
  )
  local rc=$?

  # Daemon down, timeout, network error: silently yield no suggestion. The
  # rest of the strategy chain is unaffected; the user just sees no LLM ghost.
  (( rc != 0 )) && return
  [[ -z "$response" ]] && return

  # Defensive: the daemon already enforces prefix-match, but a misbehaving
  # daemon (older version, manual curl) could send anything. The autosuggest
  # plugin will discard a non-prefix suggestion anyway, but stripping it here
  # avoids a wasted render cycle.
  [[ "$response" != "$buffer"* ]] && return

  # No-continuation: response is exactly the buffer with nothing added.
  (( ${#response} == ${#buffer} )) && return

  typeset -g suggestion="$response"
}

# ---- Convenience kill-switch alias ----------------------------------------
alias termauto-ghost-off='TERMAUTO_GHOST_DISABLED=1'
alias termauto-ghost-on='TERMAUTO_GHOST_DISABLED=0'
