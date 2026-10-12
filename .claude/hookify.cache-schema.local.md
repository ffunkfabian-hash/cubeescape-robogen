---
name: cache-schema
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: scripts/[^/]*\.py$
  - field: content
    operator: regex_match
    pattern: \bunwrap\b|\.bake\(|\bbake_
---

**Cache-Schema prüfen:** Was der Cache-Schlüssel nicht sieht, braucht eine neue Schemaversion – `UV_SCHEMA` bei
Änderungen an `unwrap()`, `BAKE_SCHEMA` bei Änderungen am Backen. Im Zweifel erhöhen (AGENTS.md › Cache-Regeln).
