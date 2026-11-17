#!/bin/sh
# scripts/ms_job.sh CMD...: run one model job at a time on the Mac Studio
# (docs/ORCHESTRATION.md section 6, "The Mac Studio rule").
#
#   scripts/ms_job.sh .venv/bin/python -m archive read --limit 50
#
# The lock is the directory data/ms.lock.d (mkdir is atomic; macOS has no flock). It holds
# a file "owner": the holder's PID and its start time. While another job holds the lock this
# script waits, checking every MS_JOB_POLL seconds (default 30). A lock whose PID is dead, or
# whose PID now belongs to another process, is stale and is taken over. Start, wait, end and
# exit code go to data/reports/ms_jobs.log. The command's exit code is passed through.
# Set SOGICE_DATA_DIR to use another data folder.
set -u

if [ $# -eq 0 ]; then
    echo "usage: scripts/ms_job.sh CMD [ARG...]" >&2
    exit 2
fi

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
DATA=${SOGICE_DATA_DIR:-$ROOT/data}
LOCK=$DATA/ms.lock.d
LOG=$DATA/reports/ms_jobs.log
POLL=${MS_JOB_POLL:-30}
mkdir -p "$DATA/reports" || exit 2

log() { printf '%s pid=%s %s\n' "$(date -u +%FT%TZ)" "$$" "$*" >> "$LOG"; }
started() { ps -o lstart= -p "$1" 2>/dev/null | tr -s ' '; }

# Is the lock's owner still the process that took it?
owner_alive() {   # owner_alive PID START
    [ -n "$1" ] && kill -0 "$1" 2>/dev/null || return 1
    [ -z "$2" ] || [ "$(started "$1")" = "$2" ]
}

ME_START=$(started $$)
waited=0
no_owner=0
while ! mkdir "$LOCK" 2>/dev/null; do
    owner=$(cat "$LOCK/owner" 2>/dev/null)
    pid=${owner%%|*}
    start=${owner#*|}
    if [ -z "$owner" ]; then
        # Taken a moment ago and the owner file is not written yet; if it stays empty, the
        # holder died between mkdir and writing it.
        no_owner=$((no_owner + 1))
        if [ "$no_owner" -lt 3 ]; then sleep "$POLL"; continue; fi
    elif owner_alive "$pid" "$start"; then
        no_owner=0
        if [ "$waited" = 0 ]; then
            log "waiting for pid=$pid: $*"
            echo "ms_job: waiting for the model job of pid $pid" >&2
            waited=1
        fi
        sleep "$POLL"
        continue
    fi
    # Stale: move it aside atomically, then check that what we moved is the stale lock.
    aside=$LOCK.stale.$$
    if mv "$LOCK" "$aside" 2>/dev/null; then
        if [ "$(cat "$aside/owner" 2>/dev/null)" = "$owner" ]; then
            log "took over a stale lock (owner: ${owner:-none})"
            rm -rf "$aside"
        else
            mv "$aside" "$LOCK" 2>/dev/null || rm -rf "$aside"   # a live lock moved by mistake
        fi
    fi
    no_owner=0
done

printf '%s|%s\n' "$$" "$ME_START" > "$LOCK/owner"
release() {
    if [ "$(cat "$LOCK/owner" 2>/dev/null)" = "$$|$ME_START" ]; then rm -rf "$LOCK"; fi
}
trap release EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

log "start: $*"
began=$(date +%s)
"$@"
rc=$?
log "end rc=$rc after $(( $(date +%s) - began ))s: $*"
exit "$rc"
