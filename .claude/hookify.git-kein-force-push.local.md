---
name: git-kein-force-push
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: \bgit\b.*\bpush\b.*(--force|--mirror|\s-f(\s|$)|\s\+\S)
---

**Kein Force-Push** (CHANGELOG.md: Tag und Release-Branch je Version): nie `--force`, nie Branches oder Tags auf GitHub überschreiben, ohne den
Nutzer zu fragen. Bei abgelehntem Push erst den Stand von `origin` holen und klären.
