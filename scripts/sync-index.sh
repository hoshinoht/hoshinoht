#!/usr/bin/env bash
# Vendor the resume index YAML into data/.
# The resume repo is private, so CI cannot read it; data/ is the committed source of truth.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="${RESUME_INDEX_PATH:-$here/../resume/index}"

if [ ! -d "$src" ]; then
  echo "sync-index: index not found at $src" >&2
  echo "            set RESUME_INDEX_PATH to the resume index directory" >&2
  exit 1
fi

files=(experience.yaml projects.yaml skills.yaml summary.yaml)

for f in "${files[@]}"; do
  if [ ! -f "$src/$f" ]; then
    echo "sync-index: missing $src/$f" >&2
    exit 1
  fi
done

echo "sync-index: $src -> $here/data"
for f in "${files[@]}"; do
  if [ -f "$here/data/$f" ] && cmp -s "$src/$f" "$here/data/$f"; then
    echo "  = $f (unchanged)"
  else
    if [ -f "$here/data/$f" ]; then
      before=$(wc -l < "$here/data/$f" | tr -d ' ')
      after=$(wc -l < "$src/$f" | tr -d ' ')
      echo "  ~ $f ($before -> $after lines)"
    else
      echo "  + $f (new)"
    fi
    cp "$src/$f" "$here/data/$f"
  fi
done
