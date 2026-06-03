#!/bin/bash
# Checks that all folders in projects/ are listed in README.md
# Auto-adds missing projects to the README table.

PROJECTS_DIR="$(dirname "$0")/projects"
README="$(dirname "$0")/README.md"

missing=()
for dir in "$PROJECTS_DIR"/*/; do
    name=$(basename "$dir")
    if ! grep -q "$name" "$README"; then
        missing+=("$name")
    fi
done

if [ ${#missing[@]} -eq 0 ]; then
    echo "✓ README.md is up to date — all projects listed."
else
    echo "⚠️  Adding missing projects to README.md:"
    for name in "${missing[@]}"; do
        echo "   + $name"
        # Get current highest index number
        last_num=$(grep -oE '^\| [0-9]+' "$README" | grep -oE '[0-9]+' | sort -n | tail -1)
        next_num=$((last_num + 1))
        # Insert new row before the closing --- line at end of table
        new_row="| $next_num | [$name](projects/$name/) | _(no description)_ | Active |"
        # Append new row after the last table row
        awk -v row="$new_row" '
            /^\| [0-9]/ { last=NR; lines[NR]=$0; next }
            { lines[NR]=$0 }
            END {
                for (i=1; i<=NR; i++) {
                    print lines[i]
                    if (i==last) print row
                }
            }
        ' "$README" > "$README.tmp" && mv "$README.tmp" "$README"
    done
    echo ""
    echo "✓ README.md updated. Edit descriptions at: $(realpath "$README")"
fi
