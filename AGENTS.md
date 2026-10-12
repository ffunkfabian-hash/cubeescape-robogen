# Roboter-Generator – Regeln für KI-Agenten

Gilt für alle Agenten (Claude Code, Codex, …). Langform: `README.md` (Teststufen, Messwerte, Aufbau),
`CHANGELOG.md` (Versionen), `docs/FAHRPLAN.md` (Fahrplan; führend ist das Claude Doc, Link dort), `docs/TECHNIK.md`
(Techniken und Lehren; Übertrag auf den Cubegen-Modell-Editor – bei jeder neuen Technik mitpflegen).

## Skins und Masken

- Skins sind Daten (`data/skins.json`), nie Code. IDs von Skins, Abzeichen (`BADGES`), Leuchtmustern
  (`LINE_PATTERNS`), Lackzonen (`PAINT_ZONES`) und Oberflächen sind ewig; Listen nur am Ende erweitern (Kanal = Position).
- Neue Abzeichen/Muster/Zonen: Maske im Generator, Formel in `decorate_skin()` **und** im Viewer-Shader gleich halten,
  Eintrag in `robot_config.json`, Prüfung in `verify_robot.py`.
- Viewer-Shader: höchstens etwa 23 eigene Argumente, sonst zeichnet SceneKit kommentarlos pink (`docs/TECHNIK.md` › 8).

## Sprache

Antworten an den Nutzer, README, CHANGELOG, Doku und Commit-Messages auf **Deutsch**. Code, Bezeichner,
Kommentare und Log-Ausgaben auf **Englisch**.

## Ausgaben

- `output/` ist nicht versioniert und entsteht nur durch den Generator. **Nie von Hand ändern** – auch nicht
  „schnell“ eine Textur, einen Clip-Wert oder eine FBX. Fehler werden im Generator behoben und neu erzeugt.
- Das Spielprojekt Cube Escape wird von diesem Repo aus nicht verändert.
- Namen von Skins, Bauteilen, Ausdrücken, Motiven und Oberflächen sind ewige IDs (gespeicherte Looks).

## Teststufen

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 --python scripts/generate_robot.py -- --tier quick
```

| Stufe | wann | Ausgabe |
|---|---|---|
| `quick` | beim Arbeiten, nach jeder Änderung | `output/_quick` |
| `standard` | vor jedem Commit | `output/_standard` |
| `full` | vor Release, für den Lieferstand | `output` |

Jeder Lauf schreibt Schrittzeiten, Systemlast und Worker-Statistik nach `<out>/logs/robot_report.json` und warnt
beim Überschreiten des Budgets (`TIERS`). Zeiten nur auf ruhiger Maschine vergleichen (`timings.load`; parallele
Unity- oder Blender-Läufe anderer Agenten verfälschen sie um ein Vielfaches).

## Prüfablauf vor dem Commit

1. Vor der Änderung den `robot_report.json` der betroffenen Stufe sichern.
2. Stufe(n) laufen lassen; `qa` (und `clips`, `type_bounds`) gegen den gesicherten Bericht vergleichen. Ohne
   gewollte Modelländerung müssen sie **identisch** sein; gewollte Änderungen im Bericht benennen.
3. Auf dem Volllauf: `Blender -b output/CubeEscape_Robot.blend --python-exit-code 1 --python scripts/verify_robot.py -- --out output`
   (alle Prüfungen grün).
4. `quick` zweimal: Beim zweiten Lauf müssen UV-Cache und Bake-Cache treffen (Log: „UV cache hit“, „bake cache hit“).
5. Sichtbare Änderungen ansehen: `Previews/skins.png`, `Previews/hero.png`, bei Animationen die Kontaktbögen.
6. Bei Viewer-Änderungen `viewer/build.sh` und `viewer/test_viewer.sh`.
7. Bei Änderungen an Export, Clips, Texturen, Masken oder Looks: `unity/run_testbed.sh` (eigenes Unity-Projekt, nie das
   Spiel; Bericht `output/UnityTestbed/report.json`). Shader-Formeln in `decorate_skin()`, Viewer und
   `unity/RobotTestbed/Assets/RobotTestbed/Shaders/RobotBody.shader` gleich halten.

## Plugins und Hooks (Claude Code)

`CLAUDE.md` importiert diese Datei. Nur Claude Code hat die Plugins; andere Agenten befolgen dieselben Regeln aus dem
Text. Gemeinsam mit Cube Escape und Cubegen (dort je eine eigene Tabelle in `AGENTS.md`).

| Plugin | Einsatz im Roboter-Generator |
|---|---|
| Blender-MCP | laufendes Blender des Nutzers als Prüfstand: `CubeEscape_Robot.blend` und Exporte laden, nachmessen, Ansichten rendern – in eigener Szene, nichts speichern |
| `quick-gate-python` | Ruff (und pytest, wo es reine Python-Teile gibt) für `scripts/`; Pyright kennt `bpy` nicht. Verbindlich bleiben die Teststufen |
| `code-review` | Slash-Befehl `code-review` vor jedem Release auf `main` |
| `github` | Pull Requests, Issues, Release-Branches und Tags auf GitHub (oder `gh`) |
| `engineering` | `debug`, `testing-strategy`, `documentation` |
| `design` | `design-critique` für Vorschauen und den Viewer |
| `hookify` | Regeln als `.claude/hookify.<name>.local.md` (eingecheckt) |
| `unity`, `unity-perf` | erst, wenn der Roboter in Cube Escape eingebaut wird – dann gelten die Regeln dort |

**Hooks** (hookify) **blockieren** Änderungen von Hand an `output/`, Änderungen am Spielprojekt Cube Escape, nacktes
`git stash` (geteilter Stash-Stapel) und Force-Push; sie **halten das Sitzungsende an**, bis nach einer Änderung in
`scripts/` eine Teststufe lief, und **warnen** bei Commit/Merge/Tag/Push (nur auf Wunsch), bei Code rund um
`unwrap()`/Backen (Schemaversion) und bei Viewer-Änderungen. Ändert sich eine dieser Regeln, ändert sich ihre Hook-Datei mit.

## Cache-Regeln (`<out>/cache`)

- Schlüssel hängen am Inhalt, nicht an Dateizeiten: Geometrie in kanonischer Ecken-Reihenfolge (Blenders
  Flächenreihenfolge und Insel-Packen sind nicht deterministisch), Materialien als Node-Signatur, Quelltexturen,
  Einstellungen.
- Was der Schlüssel nicht sehen kann, braucht eine Schemaversion: `UV_SCHEMA` bei Änderungen an `unwrap()`,
  `BAKE_SCHEMA` bei Änderungen am Backen. Im Zweifel erhöhen – ein falscher Treffer ist schlimmer als ein Neu-Backen.
- `--no-cache` liest keinen Cache, schreibt die UVs aber trotzdem (die Render-Worker brauchen sie).
- ImageMagick-Texturen hängen am Skript-Hash von `make_textures.py` und an der ImageMagick-Version.

## Worker

QA und Vorschau-Renders sind Arbeitspakete (`make_jobs`). Worker sind eigene Blender-Prozesse, die das Modell aus
demselben Code neu bauen; sie dürfen deshalb nur von Code abhängen, nie von Zustand des Hauptprozesses außer den
übergebenen Dateien (UV-Cache, gebackene Texturen, Quelltexturen). Ergebnisse werden in fester Reihenfolge
zusammengeführt – die Ausgabe darf nicht davon abhängen, wer welches Paket gerechnet hat. Was nur für Vorschauen
umgeschaltet wird (schnelle Augen, Raytracing), wird vor dem Speichern der `.blend` zurückgesetzt.
