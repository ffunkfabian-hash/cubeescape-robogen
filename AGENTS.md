# Roboter-Generator – Regeln für KI-Agenten

Gilt für alle Agenten (Claude Code, Codex, …). Langform: `README.md` (Teststufen, Messwerte, Aufbau),
`CHANGELOG.md` (Versionen), `docs/FAHRPLAN.md` (Fahrplan; führend ist das Claude Doc, Link dort).

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
