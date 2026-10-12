---
name: ausgabe-nie-von-hand
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: /output/
---

**`output/` nie von Hand ändern** – auch nicht „schnell“ eine Textur, einen Clip-Wert oder eine FBX (AGENTS.md ›
Ausgaben). Fehler im Generator beheben und die Stufe neu laufen lassen.
