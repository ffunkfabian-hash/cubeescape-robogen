---
name: cube-escape-nicht-aendern
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: /Claude/Cube Escape/
---

**Das Spielprojekt Cube Escape wird von diesem Repo aus nicht verändert** (AGENTS.md › Ausgaben). Änderungen am Spiel
gehören in eine eigene Cube-Escape-Sitzung mit deren Regeln (Version, Testkopie, `ce-delivery`).
