#!/usr/bin/env bash
# scripts/update-agents-md-tree.sh
# Pre-commit hook: regenerates the directory tree + source symbols in AGENTS.md.
# The tree includes line counts for .py/.json files and hides empty __init__.py.
# Source symbols (class/def with line numbers) are shown indented under each file:
#   - src/:   all classes and functions
#   - tests/: only classes and non-test/non-private top-level functions
set -euo pipefail

AGENTS="AGENTS.md"
BEGIN="<!-- BEGIN TREE -->"
END="<!-- END TREE -->"

# --- Build the tree with embedded symbols -----------------------------------

tree=$(rg --files --hidden | sort | while IFS= read -r f; do
  printf "%s\t%s\n" "$(wc -l < "$f")" "$f"
done | awk -F'\t' '
{
  lines = $1 + 0
  n = split($2, p, "/")
  file = p[n]
  if (file == "__init__.py" && lines == 0) next
  path = ""
  for (i = 1; i < n; i++) {
    path = (path ? path "/" : "") p[i]
    if (!seen[path]++) printf "%*s%s/\n", (i-1)*2, "", p[i]
  }
  if (file ~ /\.(py|json)$/)
    printf "%*s%s (%d)\n", (n-1)*2, "", file, lines
  else
    printf "%*s%s\n", (n-1)*2, "", file
}')

# --- Build the symbols section ----------------------------------------------
# src/: all top-level class and function definitions
# tests/: only classes and public non-test functions (skip test_ and _ prefixed)

symbols=$(
  {
    rg -n "^class |^def |^async def " src/ --sort path 2>/dev/null || true
  } | awk -F: '{
    file = $1; lineno = $2; sub(/^[^:]*:[^:]*:/, ""); sig = $0
    gsub(/\(.*/, "(…)", sig)
    gsub(/:$/, "", sig)
    printf "%s\t%d\t%s\n", file, lineno, sig
  }'

  #{
  #  rg -n "^class |^def |^async def " tests/ --sort path 2>/dev/null || true
  #} | grep -v "^[^:]*:[0-9]*:\(async \)\{0,1\}def \(_\|test_\)" \
  #  | awk -F: '{
  #  file = $1; lineno = $2; sub(/^[^:]*:[^:]*:/, ""); sig = $0
  #  gsub(/\(.*/, "(…)", sig)
  #  gsub(/:$/, "", sig)
  #  printf "%s\t%d\t%s\n", file, lineno, sig
  #}'
)

# --- Replace the block between markers --------------------------------------

awk -v begin="$BEGIN" -v end="$END" -v tree="$tree" -v symbols="$symbols" '
  $0 == begin {
    print
    printf "<!-- Line counts shown for .py and .json files. Empty __init__.py hidden. -->\n\n"
    printf "```text\n" tree "\n```\n\n"
    if (symbols != "") {
      printf "### Source symbols\n<!-- Signatures abbreviated with (…). Line numbers indicate definition start. -->\n\n```text\n"
      n = split(symbols, lines, "\n")
      prev_file = ""
      for (i = 1; i <= n; i++) {
        m = split(lines[i], parts, "\t")
        if (m < 3) continue
        file = parts[1]; lineno = parts[2]; sig = parts[3]
        if (file != prev_file) {
          if (prev_file != "") printf "\n"
          printf "%s\n", file
          prev_file = file
        }
        printf "  %4d: %s\n", lineno, sig
      }
      printf "```\n\n"
    }
    skip = 1; next
  }
  $0 == end { skip = 0 }
  !skip
' "$AGENTS" > "$AGENTS.tmp" && mv "$AGENTS.tmp" "$AGENTS"

git add "$AGENTS"
