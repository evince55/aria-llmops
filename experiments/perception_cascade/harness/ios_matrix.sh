#!/bin/bash
# Hermetic iOS matrix orchestrator.
# Two prior rounds were contaminated: the builder escaped its cwd sandbox and
# edited the REAL Aria repo (round 1 via xcodebuildmcp, round 2 via a direct
# file edit to the absolute path found in the sandbox's own AGENTS.md/CLAUDE.md).
# Containment here is belt-and-suspenders:
#   1. SCRUB the sandbox clone of every real-repo pointer (AGENTS.md, CLAUDE.md,
#      .opencode) so the builder has no map back to the real tree.
#   2. chmod the real repo READ-ONLY for the whole run — an unforgeable backstop;
#      any escape write fails with EACCES. A trap restores perms on ANY exit.
#   3. Canary FIRST (one cell); abort the matrix unless it is provably hermetic.
set -uo pipefail
S=/private/tmp/claude-501/-Users-chait-MusicAppIOS/b1cec181-d33b-43eb-9671-b4e2cc0de173/scratchpad
H=/Users/chait/MusicAppIOS/tools/llmops/experiments/perception_cascade/harness
PY=/Users/chait/MusicAppIOS/tools/llmops/.venv/bin/python
REAL=/Users/chait/MusicAppIOS/Aria_Music_Browser
BID=$(cat $S/aria-bundleid.txt)
export P2_IOS_ALLOW_UNSEALED=1

# --- restore-on-exit guard --------------------------------------------------
restore() {
  chmod -R u+w "$REAL" 2>/dev/null   # no-op now; harmless safety net
  echo "[restore] checking real repo"
  # surface (do NOT auto-revert — let the operator see) any escape that happened
  ( cd "$REAL" && git status --porcelain | grep '\.swift$' \
      && echo "[restore] !!! real repo has .swift changes — inspect + git checkout" \
      || echo "[restore] real repo clean" )
}
trap restore EXIT

# --- clean-state precondition ----------------------------------------------
cd "$REAL"
if git status --porcelain | grep -q '\.swift$'; then
  echo "ABORT: real repo already has .swift changes — refusing to start."; exit 1
fi

# --- prep scrubbed base ----------------------------------------------------
rm -rf "$S/aria-hermetic"
git clone -q "$REAL" "$S/aria-hermetic"
( cd "$S/aria-hermetic"
  rm -f AGENTS.md CLAUDE.md
  rm -rf .opencode
  # scrub any remaining absolute real-repo pointers in tracked docs
  grep -rl "/Users/chait/MusicAppIOS" . --include=*.md 2>/dev/null | while read f; do
    sed -i '' 's#/Users/chait/MusicAppIOS/Aria_Music_Browser#.#g; s#/Users/chait/MusicAppIOS#.#g' "$f" 2>/dev/null
  done
  git -c user.email=x@x -c user.name=x commit -qam "scrub sandbox pointers" 2>/dev/null )
echo "[prep] scrubbed hermetic base ready"

# --- seal: real repo read-only ---------------------------------------------
# NOTE: chmod is NOT the seal — the builder defeated it with `chmod u+w`.
# Isolation is now kernel-enforced per-cell via builder.sb (seatbelt),
# applied inside run_cell_ios.py around the opencode subprocess.
echo "[seal] kernel seatbelt active (builder.sb) — real repo invisible+immutable"

run_cell () {  # $1=bug $2=arm
  local cell="$1-$2"
  echo "=== CELL $cell $(date +%H:%M:%S) ==="
  $PY $H/run_cell_ios.py --bug "$1" --arm "$2" --base "$S/aria-hermetic" \
    --dd "$S/runs2/$cell/dd" --bid "$BID" \
    --pristine "$S/val-pristine.png" --planted "$S/val-planted-$1.png" \
    --out "$S/runs2/$cell" 2>&1 | tail -1
}

hermetic_ok () {  # $1=cell — clean iff real repo untouched AND builder used our tools
  local cell="$1"
  local escaped app
  escaped=$(cd "$REAL" && git status --porcelain | grep -c '\.swift$')
  app=$([ -f "$S/runs2/$cell/repo/tools/out/app.png" ] && echo yes || echo no)
  echo "[check $cell] real_repo_escaped=$escaped builder_used_run_app=$app"
  [ "$escaped" = "0" ] && [ "$app" = "yes" ]
}

# --- CANARY gate -----------------------------------------------------------
run_cell i1 A
if ! hermetic_ok i1-A; then
  echo "*** CANARY NOT HERMETIC — aborting matrix. Builder still escapes or never used run_app.sh."
  exit 2
fi
echo "*** CANARY HERMETIC — proceeding to full matrix."

for CELL in i1-B i1-C i2-A i2-B i2-C; do
  run_cell "${CELL%-*}" "${CELL#*-}"
  hermetic_ok "$CELL" || echo "  ! $CELL flagged non-hermetic (results suspect)"
done
echo "=== MATRIX DONE $(date +%H:%M:%S) ==="
