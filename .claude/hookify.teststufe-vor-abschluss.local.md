---
name: teststufe-vor-abschluss
enabled: true
event: stop
action: block
conditions:
  - field: transcript
    operator: regex_match
    pattern: "name":"(Edit|Write|MultiEdit)","input":\{"file_path":"[^"]*/scripts/[^"]*\.py"
  - field: transcript
    operator: not_contains
    pattern: tier=
---

**Generator geändert, aber keine Teststufe gelaufen:** vor dem Abschluss mindestens `--tier quick`, vor einem Commit
`--tier standard` laufen lassen und `qa`, `clips`, `type_bounds` mit dem gesicherten `robot_report.json` vergleichen
(AGENTS.md › Teststufen, Prüfablauf vor dem Commit).
