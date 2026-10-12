---
name: git-nur-auf-wunsch
enabled: true
event: bash
action: warn
conditions:
  - field: command
    operator: regex_match
    pattern: \bgit\s+(-C\s+("[^"]*"|'[^']*'|\S+)\s+)?(commit|push|merge|tag\s+-a|tag\s+v)\b
---

**Commit, Merge, Tag und Push nur auf Wunsch des Nutzers.** Nachricht auf Deutsch („Generator <Version> – <Name>: …“);
jede Version hat einen Tag `v<Version>` und einen Release-Branch `release/<Version>` (CHANGELOG.md); Push ohne
`--force`. Vorher den Prüfablauf aus AGENTS.md (Stufe `standard`, Berichtsvergleich).
