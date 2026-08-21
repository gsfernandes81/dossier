#!/usr/bin/env fish
#
# R2 — does this device preserve an exact mtime, and is an identical rewrite
# invisible?
#
# This is the load-bearing check under the "deterministic bundle export" idea
# (docs/dev/model-rethink.md §3). If a device cannot reproduce a byte-identical
# file with an identical mtime, two devices regenerating the same bundle folder
# will look different to Syncthing every time, and shared ownership of derived
# files is off the table.
#
# **Run it inside the real Syncthing folder.** Termux's own $HOME is app-private
# and does not go through the shared-storage FUSE layer, so a run there will pass
# even when the path that matters fails. The script refuses to run in $HOME for
# exactly that reason.
#
#   usage:  fish tools/probe-mtime.fish /path/inside/the/syncthing/folder

set -l dir $argv[1]

if test -z "$dir"
    echo "usage: fish probe-mtime.fish <dir inside the Syncthing folder>"
    exit 2
end
if not test -d "$dir"
    echo "not a directory: $dir"
    exit 2
end

# The false-pass guard. $HOME on Termux is app-private storage and behaves
# nothing like the shared-storage path Syncthing actually uses.
set -l real (cd $dir; and pwd -P)
if test "$real" = "$HOME"; or string match -q "$HOME/.*" -- "$real"
    echo "refusing: $real is inside \$HOME, which is app-private on Termux."
    echo "point this at the real Syncthing folder or the result means nothing."
    exit 2
end

echo "probing: $real"
echo "stat   : "(command -v stat)
echo "touch  : "(command -v touch)
echo

set -l probe "$real/.ds-mtime-probe"
set -l stamp '2026-01-02 03:04:05.123456789'

# ---------------------------------------------------------------- 1. set + read
printf 'dossier mtime probe\n' > $probe
touch -d "$stamp" $probe
or begin
    echo "FAIL  touch -d is not supported here (busybox?). Try: pkg install coreutils"
    rm -f $probe
    exit 1
end

set -l got (stat -c '%y' $probe)
echo "1. set an exact mtime"
echo "   asked for : $stamp"
echo "   got back  : $got"
if string match -q '2026-01-02 03:04:05*' -- "$got"
    echo "   PASS      the second was preserved"
    if string match -q '*.123456789*' -- "$got"
        echo "   (nanosecond precision survived too)"
    else
        echo "   (sub-second precision was dropped — fine, as long as it is consistent)"
    end
else
    echo "   FAIL      the mtime was not preserved. Determinism cannot work on this path."
    rm -f $probe
    exit 1
end
echo

# ------------------------------------------------- 2. is an identical rewrite a no-op
set -l before (stat -c '%s|%y|%i' $probe)
printf 'dossier mtime probe\n' > $probe
touch -d "$stamp" $probe
set -l after (stat -c '%s|%y|%i' $probe)

echo "2. rewrite identical bytes with the same mtime"
echo "   before : $before"
echo "   after  : $after"
if test "$before" = "$after"
    echo "   PASS   size, mtime and inode all unchanged — Syncthing has nothing to see"
else
    echo "   NOTE   something changed. If only the inode moved, Syncthing is likely"
    echo "          still fine (it scans size+mtime), but say so in the results."
end
echo

rm -f $probe
echo "done. Step 3 needs both devices — see the instructions that came with this."
