# Roboter-Generator – Regeln für KI-Agenten
Zentraler Einstieg: [Nexogen](https://github.com/ffunkfabian-hash/cubeescape-nexogen) (Repo-Links, Zuständigkeiten, Status). Globale Regeln dort; hier nur Repo-Eigenes. Langform: `README.md` (Teststufen), `CHANGELOG.md`, `docs/FAHRPLAN.md`, `docs/TECHNIK.md` (Erfahrungen, Techniken, Fallen), `docs/ENTSCHEIDUNGEN.md`.

## Harter Kern

Quelle: Nexogen `templates/kern.md`, nur dort ändern; gilt auch in Cloud-Sitzungen.

1. Kein Commit direkt auf `main`.
2. Kein Force-Push, keine überschriebenen Branches oder Tags.
3. Nichts Fremdes anfassen oder löschen: andere Sitzungen, Worktrees, Stash, Haupt-Checkout.
4. Merge nach `main` nur mit Ja des Nutzers, außer Patch bis 100 Zeilen ohne Format, Vertrag, Kernpfad bei grünem `merge-check`; Tag, Release, Repo öffentlich machen immer nur mit Ja.
5. Tests nie löschen oder abschwächen, um grün zu werden.
6. Keine Zugangsdaten, kein fremdes Material, keine großen Binärdaten nach außen (öffentliche Repos, fremde APIs).
7. Formate, Verträge und IDs nur mit Migration; IDs sind ewig.
8. Nichts als fertig melden, was nicht geprüft ist.

## Projekt

Blender-Generator für den Roboter-Charakter von Cube Escape (Modell, Rig, Clips, Skins, Export, Viewer, Unity-Testbett).
Die Version steht in `scripts/robogen/core.py` (`VERSION`); sie zählt getrennt von Cube Escape und Cubegen.

- **Sprache:** Antworten an den Nutzer, README, CHANGELOG, Doku und Commit-Messages auf **Deutsch**. Code, Bezeichner,
  Kommentare und Log-Ausgaben auf **Englisch**.
- **Skills** (Arbeitsanweisungen in `.agents/skills/<name>/SKILL.md`; `.claude/skills` ist ein Link darauf; andere Agenten
  lesen die passende `SKILL.md` vor der Arbeit): `rg-unity-testbed` (Unity-Testpaket, -Vorlagen, -Laufzeit),
  `rg-unity-integration` (Übergabe an das Spiel: FBX, Masken, Look, Clip-Daten, Kapseln, Emitter).

## Stand und Zuständigkeit

- Version und Stand: `scripts/robogen/core.py`, `git log --oneline -15`, `CHANGELOG.md`; widerspricht ein Doc dem Code, wird es im selben Zuge korrigiert.
- `output/` ist nicht versioniert und entsteht nur durch den Generator. **Nie von Hand ändern** – auch nicht „schnell“ eine
  Textur, einen Clip-Wert oder eine FBX. Fehler werden im Generator behoben und neu erzeugt.
- Das Spielprojekt Cube Escape wird von hier nicht verändert. Namen von Skins, Bauteilen, Ausdrücken, Motiven und
  Oberflächen sind ewige IDs. Entscheidungen des Nutzers: `docs/ENTSCHEIDUNGEN.md` (vor dem Neuverhandeln einer Regel lesen).


## Befehle

```bash
./check                 # schnell, ohne Blender: Werkzeug-Tests (= python3 -m unittest discover -s tests)
./check --full          # dazu der Prüfplan (scripts/testplan.py); .ce-repo.json › check
./release               # Probelauf der gemeinsamen Release-Prüfung; --execute führt aus (Ablauf unten)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 --python scripts/generate_robot.py -- --tier quick
```

| Stufe | wann | Ausgabe |
|---|---|---|
| `quick` | beim Arbeiten, nach jeder Änderung | `output/_quick` |
| `standard` | vor jedem Commit | `output/_standard` |
| `full` | vor Release, für den Lieferstand | `output` |

Jeder Lauf schreibt `<out>/logs/robot_report.json` und bewertet die Zeit im **Zeitband** (`timings.band`): „SLOWER“ auf
ruhiger Maschine ist ein Befund, unter Last nichts schließen (`docs/TECHNIK.md` › 21).
Cloud-Sitzungen: `docs/TECHNIK.md` › 22.

- **Release:** `./release` (Probelauf der gemeinsamen Release-Prüfung), `--execute` führt über `scripts/release.py <Version>` aus: Merge ff-only, Tag `v<Version>`, Branch `release/<Version>`, ein Push ohne `--force`; nur auf Wunsch des Nutzers (`docs/TECHNIK.md` › 22).
- **Schwere Läufe:** `full`, `check_output.sh` und `unity/run_testbed.sh` nur über `./ce lock <unity|blender|gpu> -- <Befehl>` (Ressource nennen; gleiche Ressource nacheinander, GPU exklusiv; Nexogen `templates/regeln-details.md` › Schwere Läufe und Sperren). `scripts/heavy_lock.py` leitet auf die Sperre der Werkzeuge weiter.
- **Ganze Leistung des Geräts:** Braucht eine Sitzung den Rechner für sich, darf sie andere Sitzungen pausieren (`pause_sessions.py run -- <Befehl>` aus Cube Escape › `ce-testing`); nur so lange wie nötig, im Bericht nennen. `full` und `check_output.sh` tun das selbst (`scripts/quiet.py`, `ROBOGEN_NO_PAUSE=1`; `docs/TECHNIK.md` › 22).

## Regeln

### Skins und Masken
- Skins sind Daten (`data/skins.json`), nie Code. IDs von Skins, Abzeichen (`BADGES`), Leuchtmustern (`LINE_PATTERNS`),
  Lackzonen (`PAINT_ZONES`), Schichten (`LAYERS`), Werkstoffen (`MATERIALS`) und Oberflächen sind ewig; Listen nur am Ende
  erweitern (Kanal = Position).
- Neue Abzeichen/Muster/Zonen: Maske im Generator, Formel in `decorate_skin()` **und** im Viewer-Shader gleich halten,
  Eintrag in `robot_config.json`, Prüfung in `verify_robot.py`.
- Viewer-Shader: Look-Werte in die Parameter-Textur (`writeParams`, `P(i)`), nicht in neue Shader-Argumente – SceneKit
  zeichnet sonst kommentarlos pink (`docs/TECHNIK.md` › 8). Neue Abzeichen-Masken: in Unity einen Wähler mehr.

### Aufsätze und Bewegung
- Jeder Aufsatz (Accessoire-Variante) steckt in mindestens einem Robotertyp – nur so prüft die QA ihn in allen Clips
  (`verify_robot.py` › `verify_bolton`). Neue Slots am Ende von `ACCESSORY_SLOTS`; alte Looks bekommen „None“.
- Schwebende Teile nur absichtlich (`FLOATING_PARTS`), Partikel nie als Mesh, sondern als `EMITTERS`.
- Nachschwingen nur gebacken (`SPRINGS`), Grenzen so, dass nichts in den Körper schwingt.
- Typ-Emotes (`robotTypes[].emote`) gehören zu ihrem Typ: QA und Spiel nur mit diesem Typ. Gewollte Ausnahmen der QA stehen
  benannt in `ACCEPTED_OVERLAPS` / `ACCEPTED_DETACH` / `ACCEPTED_GROUND`, nie pauschal.

### Proportionen und Körperformen (1.14)
- Maße ändern über `PROPORTIONS` / `SHAPE_VARIANTS` (Umformung je Knochen nach dem Bauen), nie über Gelenke: Gelenke, Clips,
  IK-Ziele, Kletterhöhen (ClimbUp 1,20 m) und Bodenkontakte sind Spielvertrag. Nach jeder Maßänderung die QA aller Typen und
  Formen (`--tier full --skip-bake --no-render` reicht).
- Neue Teile im alten Maß bauen und mit `conform_front` auflegen; Sockel und Federknochen über `shaped()` platzieren.
- Formen (`FORMS`) sind Daten: IDs ewig, nur am Ende erweitern. Mesh-Formen behalten Rig, Vertex-Gruppen-Reihenfolge,
  Materialien und die UV-Ebene `UVMap`; jede Geometrie-Form läuft als `Form_<id>` durch die QA aller Clips. Lochformel in
  Vorschau, Viewer und Unity-Shader gleich halten.

### Animationen
- Jeder neue Clip bekommt eine Kategorie in `CLIP_CATEGORIES` (der Generator bricht sonst ab); die Bau-Phase (`group`) ist
  nur für Kontaktbögen, QA-Pakete und `--groups`. Kategorie-IDs sind ewig.
- Bestehende Clips nie ändern (Spielvertrag); neue Clips am Ende anhängen (`ANIMS`, Kategorie-Listen), eigene Bau-Phase.
- Clips unter einer Spielkapsel prüft `verify_robot.py` an `clips.<Name>.height` (Kriechen < 0,62 m).
- Lauf-Clips im Spieltempo (1.19): gesetzter Kontakt mit konstantem Winkel, Schrittlänge = Tempo × gesetzte Frames / 30,
  mindestens zwei Frame-Schritte, sonst misst `measure_clips` kein `naturalSpeed`; `verify_longstride` prüft jeden Schritt.

### Code-Aufbau und Worker
- `scripts/generate_robot.py` ist nur Einstieg; die Schritte stehen in `scripts/robogen/` (Reihenfolge und Regeln in
  `robogen/__init__.py`). Ein Modul nutzt nur Namen aus sich und früheren Modulen (`from robogen.<vorher> import *`).
- QA und Vorschau-Renders sind Arbeitspakete (`make_jobs`) in eigenen Blender-Prozessen: nur von Code abhängen, nie von
  Zustand des Hauptprozesses außer den übergebenen Dateien; Ergebnisse in fester Reihenfolge zusammenführen (`docs/TECHNIK.md` › 22).

### Cache-Regeln (geteilter Cache, seit 1.20.2)
- Ein Cache für alle Checkouts (`scripts/robocache.py`): Schreiben nur atomar über `robocache.write_atomic`/`put_file`/`put_dir`,
  nie einen Eintrag löschen, Aufräumen nur nach Größe; Schlüssel hängen am Inhalt, nicht an Pfaden oder Dateizeiten.
- **Schemaversion** für alles, was der Schlüssel nicht sieht: `UV_SCHEMA` (`unwrap()`), `BAKE_SCHEMA` (Backen). Im Zweifel
  erhöhen. **Code im Schlüssel** (`QA_CODE_PARTS`, `CLIP_CODE_PARTS`, `EXPORT_CODE_PARTS`): neue Funktion an einer gecachten
  Ausgabe → ihr Name in die Liste. Einzelheiten: `docs/TECHNIK.md` › 20, 22.


## Definition of Done (Prüfablauf vor dem Commit)

0. **Zuerst `python3 scripts/testplan.py`:** liest den Diff (gegen `main`, auch Ungesichertes) und nennt Weg, Stufen, Prüfungen
   und Zeit – nur Doku: nichts; nur Daten: schneller Weg (unten); Modell-, Rig-, Clip-, UV-, Bake-, Export- oder Cache-Code:
   der volle Ablauf hier; Viewer: Build und Tests; Export/Clips/Texturen/Masken/Looks/Shader: Unity-Testbett. `--run` führt den
   Worktree-Teil aus, `--base <Tag> --after-merge --run` im Haupt-Checkout den Rest; Werte in Code-Tabellen (`FINISH_PARAMS` …):
   `--path data`; neue Dateien in `RULES`/`AREAS` von `testplan.py` eintragen.
1. Vor der Änderung den `robot_report.json` der betroffenen Stufe sichern.
2. Stufe(n) laufen lassen; `qa` (und `clips`, `type_bounds`) gegen den gesicherten Bericht vergleichen. Ohne gewollte
   Modelländerung **identisch**; gewollte Änderungen im Bericht benennen.
3. Auf dem Volllauf: `scripts/check_output.sh` (Ausgabe als Argument, Standard `output`) – `verify_robot.py`, Viewer-Build und
   -Tests und Unity-Testpaket **gleichzeitig**; alle grün.
4. `quick` zweimal: Beim zweiten Lauf müssen UV-, Bake-, QA- und Render-Cache treffen und ohne Änderung an Modell, Rig, Clips
   und Export-Code keine FBX und Viewer-Daten geschrieben werden (`docs/TECHNIK.md` › 12, 21).
5. Sichtbare Änderungen ansehen: `Previews/skins.png`, `Previews/hero.png`, bei Animationen die Kontaktbögen.
6. Viewer: `viewer/build.sh` und `viewer/test_viewer.sh`. Export, Clips, Texturen, Masken, Looks: `unity/run_testbed.sh`
   (eigenes Unity-Projekt, nie das Spiel; Skill `rg-unity-testbed`); Shader-Formeln in `decorate_skin()`, Viewer und
   `RobotBody.shader` gleich halten.
7. Abschlussmeldung auf Deutsch: was geändert, wie geprüft (Stufen, Zahlen), was offen ist. Nichts als erledigt melden, was
   nicht geprüft wurde.

Reine Datenänderungen (nur Werte, nicht Modell, Rig, Clips, UVs, Backen, Export, Viewer, Shader, Cache) sind ein Patch (`./ce session patch`); `testplan.py` erkennt „nur Daten“ selbst und nennt den kurzen Weg.

## Hooks

Repo-eigene Claude-Hooks (`.claude/hookify.*.local.md`, `.claude/settings.json`): **blockieren** Handänderungen an `output/` und am Spielprojekt (`ausgabe-nie-von-hand`, `cube-escape-nicht-aendern`) und **halten das Sitzungsende an**, bis nach einer Änderung in `scripts/` eine Teststufe lief (`teststufe-vor-abschluss`). Die Warnungen `git-nur-auf-wunsch`, `cache-schema`, `viewer-pruefen` sind entfernt (wirkungslos; Git-Regeln stehen im harten Kern, Cache-Regeln oben, Viewer-Prüfung im Prüfablauf). Subagents: `rg-test-runner` (berichtet nur), `rg-doc-keeper` (nur Markdown). Plugins und Werkzeuge: `docs/TECHNIK.md` › 21, `README.md`.
