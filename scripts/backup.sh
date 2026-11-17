#!/bin/sh
# scripts/backup.sh DEST: back up the archive to DEST (docs/ORCHESTRATION.md S0).
#
#   1. data/archive.db -> DEST/archive-YYYYMMDD-HHMM.db with SQLite's online backup
#      (".backup"), which is safe while the database is open; the source is opened read-only;
#   2. rsync -a data/objects/ DEST/objects/ (adds and updates; never deletes anything in DEST);
#   3. restore check: PRAGMA integrity_check on the copy, and every object the copy's
#      source_version rows name (path and text_path) is present under DEST/objects;
#   4. one line per run in data/reports/backups.log.
#
# Refuses a DEST inside the repository or the data folder; warns when DEST is on the same
# volume as data/ (that copy does not survive a disk fault). Set SOGICE_DATA_DIR to back up
# another data folder. Exit: 0 ok; 1 refused or a step failed; 2 integrity check failed;
# 3 the copy is sound but objects are missing in DEST (see scripts/verify_objects.py).
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
DATA=${SOGICE_DATA_DIR:-$ROOT/data}
LOG_DIR=$DATA/reports
LOG=$LOG_DIR/backups.log
STAMP=$(date +%Y%m%d-%H%M)
DEST_ARG=${1:-}

log() {   # log STATUS DETAIL...
    [ -d "$DATA" ] && mkdir -p "$LOG_DIR" 2>/dev/null &&
        printf '%s dest=%s status=%s %s\n' "$(date -u +%FT%TZ)" "${DEST:-$DEST_ARG}" "$1" "${2:-}" >> "$LOG"
}
fail() {  # fail CODE STATUS MESSAGE
    echo "backup: $3" >&2
    log "$2" "$3"
    exit "$1"
}
realpath_any() {   # absolute, symlink-resolved path; the path need not exist yet
    python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}
inside() {   # inside CHILD PARENT: CHILD is PARENT or below it
    case "$1/" in "$2"/*) return 0 ;; esac
    return 1
}

if [ -z "$DEST_ARG" ] || [ $# -ne 1 ]; then
    echo "usage: scripts/backup.sh DEST" >&2
    exit 1
fi
for tool in sqlite3 rsync python3; do
    command -v "$tool" >/dev/null 2>&1 || fail 1 refused "$tool not found"
done
case "$DEST_ARG" in
    *"'"*) fail 1 refused "DEST must not contain a single quote: $DEST_ARG" ;;
esac
[ -f "$DATA/archive.db" ] || fail 1 refused "no database at $DATA/archive.db"
[ -d "$DATA/objects" ] || fail 1 refused "no objects folder at $DATA/objects"

DEST=$(realpath_any "$DEST_ARG")
DATA_REAL=$(realpath_any "$DATA")
CHECKOUT=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
CHECKOUT=${CHECKOUT:+$(realpath_any "$(dirname "$CHECKOUT")")}
for forbidden in "$ROOT" "$DATA_REAL" ${CHECKOUT:+"$CHECKOUT"}; do
    if inside "$DEST" "$forbidden" || inside "$forbidden" "$DEST"; then
        fail 1 refused "DEST $DEST overlaps $forbidden; back up outside the repository and data folder"
    fi
done

mkdir -p "$DEST/objects" || fail 1 failed "cannot create $DEST/objects"

SAME=$(python3 -c 'import os, sys; print(int(os.stat(sys.argv[1]).st_dev == os.stat(sys.argv[2]).st_dev))' \
    "$DATA_REAL" "$DEST")
VOLUME=other
if [ "$SAME" = 1 ]; then
    VOLUME=same
    echo "backup: WARNING: $DEST is on the same volume as $DATA_REAL; this copy does not survive a disk fault" >&2
fi

COPY=$DEST/archive-$STAMP.db
[ -e "$COPY" ] && fail 1 failed "$COPY already exists; not overwriting (wait a minute and run again)"

echo "backup: database -> $COPY" >&2
sqlite3 -readonly "$DATA/archive.db" ".backup '$COPY'" || fail 1 failed "sqlite3 .backup failed"

echo "backup: objects -> $DEST/objects/" >&2
rsync -a "$DATA/objects/" "$DEST/objects/" || fail 1 failed "rsync of objects failed (rc=$?)"

echo "backup: restore check" >&2
INTEGRITY=$(sqlite3 -readonly "$COPY" "PRAGMA integrity_check" 2>&1)
if [ "$INTEGRITY" != ok ]; then
    fail 2 integrity_failed "integrity_check on $COPY: $(echo "$INTEGRITY" | head -3 | tr '\n' ' ')"
fi

# Every object the copy names must be in DEST (paths are relative to the data folder).
COUNTS=$(sqlite3 -readonly "$COPY" \
    "SELECT path FROM source_version UNION SELECT text_path FROM source_version WHERE text_path IS NOT NULL" |
    python3 -c '
import os, sys
dest = sys.argv[1]
names = [line.rstrip("\n") for line in sys.stdin if line.strip()]
missing = [n for n in names if not os.path.isfile(os.path.join(dest, n))]
for n in missing[:20]:
    print("backup: missing in DEST:", n, file=sys.stderr)
print(len(names), len(names) - len(missing))
' "$DEST") || fail 1 failed "object count failed"
NAMED=${COUNTS% *}
PRESENT=${COUNTS#* }
VERSIONS=$(sqlite3 -readonly "$COPY" "SELECT count(*) FROM source_version")
SIZE=$(du -sk "$DEST" 2>/dev/null | cut -f1)

DETAIL="db=$(basename "$COPY") integrity=ok versions=$VERSIONS objects=$PRESENT/$NAMED volume=$VOLUME dest_kb=${SIZE:-?}"
if [ "$PRESENT" != "$NAMED" ]; then
    echo "backup: $((NAMED - PRESENT)) of $NAMED objects are not in DEST; run scripts/verify_objects.py on data/" >&2
    log objects_missing "$DETAIL"
    exit 3
fi
log ok "$DETAIL"
echo "backup: ok: $DETAIL" >&2
exit 0
