---
name: viewer-pruefen
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: /viewer/(?!\.build/)
---

**Viewer geändert:** vor dem Abschluss `viewer/build.sh` und `viewer/test_viewer.sh` ausführen (AGENTS.md ›
Prüfablauf vor dem Commit, Punkt 6).
