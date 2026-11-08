#!/usr/bin/env bash
# Re-render the architecture diagrams (docs/diagrams/*.mmd) to SVG and PNG.
# Needs the Mermaid CLI: npm install -g @mermaid-js/mermaid-cli
set -euo pipefail
cd "$(dirname "$0")/../docs/diagrams"
for src in *.mmd; do
  name="${src%.mmd}"
  mmdc -i "$src" -o "$name.svg" -c theme.json -b white -q
  mmdc -i "$src" -o "$name.png" -c theme.json -b white -s 2 -q
  echo "rendered $name"
done
