# termauto — zsh integration
# ---------------------------
# Source this file from your .zshrc:
#     source /path/to/termauto/shell/termauto.zsh
#
# Default keybinding: Ctrl-Space invokes the candidate panel.
# Optional:  set TERMAUTO_QQ_TRIGGER=1 to also fire on "??" typed at line start.
#
# Requires: `termauto` CLI on $PATH and the daemon running (`termauto start`).

# ---- Config (override before sourcing) -------------------------------------
: ${TERMAUTO_BIN:=termauto}
: ${TERMAUTO_HOTKEY:=^@}              # Ctrl-Space. Use ^X^T for Ctrl-X Ctrl-T, etc.
: ${TERMAUTO_QQ_TRIGGER:=0}            # 1 = `??` at line start invokes the panel
: ${TERMAUTO_N_CANDIDATES:=5}
: ${TERMAUTO_HISTORY_DEPTH:=8}         # how many recent commands to send as context
: ${TERMAUTO_TIMEOUT:=15}              # CLI request timeout (seconds)

# ---- Internal state --------------------------------------------------------
typeset -ga _TERMAUTO_RECENT          # array of "cmd\texit" strings
typeset -g  _TERMAUTO_LAST_EXIT=0

# ---- precmd / preexec hooks ------------------------------------------------
# preexec captures the about-to-run command; precmd appends it with the exit code.
_termauto_preexec() {
  _TERMAUTO_PENDING_CMD=$1   # $1 is the literal command line
}

_termauto_precmd() {
  _TERMAUTO_LAST_EXIT=$?
  if [[ -n "${_TERMAUTO_PENDING_CMD-}" ]]; then
    # Skip our own invocations — they pollute context.
    if [[ "$_TERMAUTO_PENDING_CMD" != *"$TERMAUTO_BIN suggest"* ]]; then
      _TERMAUTO_RECENT+=("${_TERMAUTO_PENDING_CMD}"$'\t'"${_TERMAUTO_LAST_EXIT}")
      # Cap depth
      if (( ${#_TERMAUTO_RECENT[@]} > TERMAUTO_HISTORY_DEPTH )); then
        _TERMAUTO_RECENT=("${_TERMAUTO_RECENT[@]: -$TERMAUTO_HISTORY_DEPTH}")
      fi
    fi
    unset _TERMAUTO_PENDING_CMD
  fi
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec _termauto_preexec
add-zsh-hook precmd  _termauto_precmd

# ---- Helpers ---------------------------------------------------------------
# Build the --recent JSON payload from _TERMAUTO_RECENT.
_termauto_recent_json() {
  local out="["
  local first=1
  local entry cmd exit
  for entry in "${_TERMAUTO_RECENT[@]}"; do
    cmd="${entry%$'\t'*}"
    exit="${entry##*$'\t'}"
    # JSON-escape the command
    cmd="${cmd//\\/\\\\}"
    cmd="${cmd//\"/\\\"}"
    cmd="${cmd//$'\n'/\\n}"
    cmd="${cmd//$'\r'/\\r}"
    cmd="${cmd//$'\t'/\\t}"
    if (( first )); then first=0; else out+=","; fi
    out+="[\"$cmd\",$exit]"
  done
  out+="]"
  print -r -- "$out"
}

# Render an array of candidates as a numbered panel for `zle -M`.
_termauto_render_panel() {
  local -a cands=("$@")
  local i=1
  local out=""
  local line max_w cols
  cols=${COLUMNS:-80}
  max_w=$(( cols - 6 ))
  (( max_w < 20 )) && max_w=20

  for line in "${cands[@]}"; do
    # Truncate long lines for display only — the full cmd is still in cands.
    if (( ${#line} > max_w )); then
      out+="  ${i}. ${line[1,max_w-1]}…"$'\n'
    else
      out+="  ${i}. ${line}"$'\n'
    fi
    (( i++ ))
  done
  out+="  [1-${#cands[@]}] accept  •  [esc/^G] dismiss  •  [e] edit top"
  print -r -- "$out"
}

# ---- Main widget -----------------------------------------------------------
_termauto_invoke() {
  emulate -L zsh
  setopt local_options no_glob_subst

  # Snapshot current buffer
  local original_buffer="$BUFFER"
  local original_cursor=$CURSOR

  zle -M "termauto: thinking…"
  zle -R

  # Build recent-commands JSON
  local recent_json
  recent_json=$(_termauto_recent_json)

  # Call the daemon. --format lines emits one cmd per line on stdout.
  # We capture stderr separately so error messages can be shown to the user.
  local raw err
  raw="$(
    "$TERMAUTO_BIN" suggest \
      --format lines \
      --n "$TERMAUTO_N_CANDIDATES" \
      --cwd "$PWD" \
      --recent "$recent_json" \
      -- "$original_buffer" \
      2> /tmp/termauto.err
  )"
  local rc=$?
  err="$(< /tmp/termauto.err)"
  rm -f /tmp/termauto.err

  if (( rc != 0 )); then
    zle -M "termauto error (rc=$rc): ${err:-unknown}"
    return 0
  fi

  if [[ -z "$raw" ]]; then
    zle -M "termauto: no candidates returned"
    return 0
  fi

  # Split into array (one candidate per line)
  local -a cands
  cands=("${(@f)raw}")
  # Drop empty trailing element if present
  if [[ -z "${cands[-1]}" ]]; then
    cands=("${cands[@]:0:${#cands[@]}-1}")
  fi
  if (( ${#cands[@]} == 0 )); then
    zle -M "termauto: no candidates parsed"
    return 0
  fi

  # Render panel + wait for selection
  local panel
  panel=$(_termauto_render_panel "${cands[@]}")
  zle -M "$panel"
  zle -R

  local key
  read -k 1 -s key

  # Clear panel
  zle -M ""

  case "$key" in
    [1-9])
      local idx=$key
      if (( idx >= 1 && idx <= ${#cands[@]} )); then
        BUFFER="${cands[$idx]}"
        CURSOR=${#BUFFER}
      fi
      ;;
    e|E)
      # Put top candidate in buffer but don't execute — user edits and presses Enter
      BUFFER="${cands[1]}"
      CURSOR=${#BUFFER}
      ;;
    $'\e'|$'\x07')  # Esc or Ctrl-G
      BUFFER="$original_buffer"
      CURSOR=$original_cursor
      ;;
    *)
      # Anything else: restore and re-emit the key for normal handling
      BUFFER="$original_buffer"
      CURSOR=$original_cursor
      # Note: re-emitting a key inside zle is tricky; for v0.1 we just swallow it.
      ;;
  esac

  zle redisplay
}

zle -N _termauto_invoke
bindkey -- "$TERMAUTO_HOTKEY" _termauto_invoke

# ---- Optional `??` trigger -------------------------------------------------
if [[ "$TERMAUTO_QQ_TRIGGER" == "1" ]]; then
  _termauto_qq_dispatch() {
    if [[ "$LBUFFER" == "?" && -z "$RBUFFER" ]]; then
      LBUFFER=""
      _termauto_invoke
    else
      LBUFFER+="?"
    fi
  }
  zle -N _termauto_qq_dispatch
  bindkey -- '?' _termauto_qq_dispatch
fi

# ---- Convenience aliases ---------------------------------------------------
alias termauto-status="$TERMAUTO_BIN status"
alias termauto-restart="$TERMAUTO_BIN restart"
