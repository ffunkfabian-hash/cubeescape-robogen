# Roboter-Generator – Regeln für KI-Agenten

Gilt für alle Agenten (Claude Code, Codex, …). Langform: `README.md` (Teststufen, Messwerte, Aufbau),
`CHANGELOG.md` (Versionen), `docs/FAHRPLAN.md` (Fahrplan; führend ist das Claude Doc, Link dort), `docs/TECHNIK.md`
(Techniken und Lehren; Übertrag auf den Cubegen-Modell-Editor – bei jeder neuen Technik mitpflegen).

## Skills

Arbeitsanweisungen je Bereich in `.agents/skills/<name>/SKILL.md` (`.claude/skills` ist ein Link darauf; andere
Agenten lesen die passende `SKILL.md` vor der Arbeit):

| Aufgabe berührt … | Skill |
|---|---|
| Unity-Testpaket, Unity-Vorlagen (Shader, Look, Appearance), Unity-Laufzeit | `rg-unity-testbed` |
| Übergabe an das Spiel (FBX, Masken, Look, Clip-Daten, Kapseln, Emitter) | `rg-unity-integration` |

## Skins und Masken

- Skins sind Daten (`data/skins.json`), nie Code. IDs von Skins, Abzeichen (`BADGES`), Leuchtmustern
  (`LINE_PATTERNS`), Lackzonen (`PAINT_ZONES`), Schichten (`LAYERS`), Werkstoffen (`MATERIALS`) und Oberflächen sind
  ewig; Listen nur am Ende erweitern (Kanal = Position).
- Neue Abzeichen/Muster/Zonen: Maske im Generator, Formel in `decorate_skin()` **und** im Viewer-Shader gleich halten,
  Eintrag in `robot_config.json`, Prüfung in `verify_robot.py`.
- Viewer-Shader: Look-Werte gehören in die Parameter-Textur (`writeParams`, `P(i)`), nicht in neue Shader-Argumente –
  SceneKit zeichnet sonst kommentarlos pink (`docs/TECHNIK.md` › 8). Abzeichen packt der Viewer je Look in eine
  Textur; neue Abzeichen-Masken brauchen dort keinen Sampler, in Unity einen Wähler mehr.

## Aufsätze und Bewegung

- Jeder Aufsatz (Accessoire-Variante) steckt in mindestens einem Robotertyp – nur so prüft die QA ihn in allen Clips
  (`verify_robot.py` › `verify_bolton`). Neue Slots am Ende von `ACCESSORY_SLOTS`; alte Looks bekommen „None“.
- Schwebende Teile nur absichtlich (`FLOATING_PARTS`), Partikel nie als Mesh, sondern als `EMITTERS`.
- Nachschwingen nur gebacken (`SPRINGS`); Grenzen so, dass nichts in den Körper schwingt.
- Typ-Emotes (`robotTypes[].emote`) gehören zu ihrem Typ: QA und Spiel nur mit diesem Typ. Gewollte Ausnahmen der QA
  stehen benannt in `ACCEPTED_OVERLAPS` / `ACCEPTED_DETACH` / `ACCEPTED_GROUND` (Bodenkontakt je Clip und
  Knochengruppe), nie pauschal.

## Proportionen und Körperformen (1.14)

- Maße ändern über `PROPORTIONS` / `SHAPE_VARIANTS` (Umformung je Knochen nach dem Bauen), nie über Gelenke: Gelenke,
  Clips, IK-Ziele, Kletterhöhen (ClimbUp 1,20 m) und Bodenkontakte sind Spielvertrag. Nach jeder Maßänderung die QA
  aller Typen und Formen (`--tier full --skip-bake --no-render` reicht dafür).
- Neue Teile im alten Maß bauen und mit `conform_front` auflegen; Sockel und Federknochen über `shaped()` platzieren.
- Formen (`FORMS`) sind Daten wie Spezialmaterialien: IDs ewig, nur am Ende erweitern. Mesh-Formen behalten Rig,
  Vertex-Gruppen-Reihenfolge, Materialien und die UV-Ebene `UVMap`; jede Geometrie-Form läuft als `Form_<id>` durch die
  QA aller Clips. Die Lochformel ist in Vorschau, Viewer und Unity-Shader gleich zu halten.

## Animationen

- Jeder neue Clip bekommt eine Kategorie in `CLIP_CATEGORIES` (der Generator bricht sonst ab); die Bau-Phase
  (`group`) ist nur für Kontaktbögen, QA-Pakete und `--groups`. Kategorie-IDs sind ewig.
- Clips, die unter eine Spielkapsel passen müssen, prüft `verify_robot.py` an `clips.<Name>.height` (Kriechen < 0,62 m).
- Bestehende Clips nie ändern (Spielvertrag); neue Clips am Ende anhängen (`ANIMS`, Kategorie-Listen), eigene Bau-Phase.
- Lauf-Clips im Spieltempo (1.19): ein gesetzter Kontakt läuft mit konstantem Winkel genau mit dem Zieltempo
  (Schrittlänge = Tempo × gesetzte Frames / 30) und überspannt mindestens zwei Frame-Schritte, sonst misst
  `measure_clips` kein `naturalSpeed`; `verify_longstride` prüft jeden Frame-Schritt.

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

Werkzeug-Tests ohne Blender (geteilter Cache, Prüfplan, ruhige Maschine): `python3 -m unittest discover -s tests`.

Jeder Lauf schreibt Schrittzeiten, Systemlast und Worker-Statistik nach `<out>/logs/robot_report.json` und bewertet
die Zeit im **Zeitband** (`timings.band`, `<out>/cache/timings.json`): gegen den Median ruhiger Läufe derselben Stufe und
Cache-Lage, mit Toleranz je Last (ruhig 30 %, leicht × 2,6, schwer = nicht bewertet). Nur ruhige Läufe werden
Referenz. Ein „SLOWER“ auf ruhiger Maschine ist ein Befund; unter Last nichts schließen, sondern ruhig nachmessen
(Abschnitt unten). Das Budget (`TIERS`) bleibt Zielwert.

## Cloud-Sitzungen (claude.ai/code)

Der SessionStart-Hook `.claude/hooks/session-start.sh` (lokal tut er nichts) macht die Teststufen in der Cloud lauffähig:
Blender 5.2 LTS als Python-Modul von PyPI (`bpy`; download.blender.org ist gesperrt) hinter dem Befehl `blender`
(`.claude/hooks/blender_cli.py`, auch für die Worker), ImageMagick 6 hinter einem kleinen `magick`, Arial/Hiragino als
Liberation Sans/IPA Gothic. Dort also `blender -b … --python scripts/generate_robot.py -- --tier quick` statt des
macOS-Pfads. Grenzen: headless auf der CPU; Texturen und Zeiten sind nicht mit dem Mac vergleichbar (anderes
ImageMagick, andere Schriften, wenige Kerne) – `qa`/`clips`/`type_bounds` nur gegen einen Cloud-Lauf vergleichen.
Viewer (Swift), Unity-Testpaket und Blender-MCP gibt es nur lokal; der Lieferstand (`full`) entsteht weiter dort.

## Ganze Leistung des Geräts (gemeinsame Regel mit Cube Escape und Cubegen)

Braucht eine Sitzung den Rechner für sich (Messungen, Benchmarks, Zeitbudgets, Abnahmen), darf sie andere
Sitzungen pausieren. Werkzeug: `pause_sessions.py` aus Cube Escape
(`/Users/ffunk/Claude/Cube Escape/.agents/skills/ce-testing/scripts/pause_sessions.py run -- <Befehl>`): hält nur die
Befehle anderer Claude-/Codex-Sitzungen an (SIGSTOP), setzt sie danach immer fort (SIGCONT), lässt die Sitzungen selbst,
den Unity-Editor und Blender des Nutzers unberührt. Nur so lange wie nötig pausieren und es im Bericht nennen. Ein
pausierter Befehl kann in die Zeitgrenze seines Werkzeugs laufen – dann führt die andere Sitzung ihn erneut aus.
Seit 1.20.2 tun das `full` und `scripts/check_output.sh` selbst (`scripts/quiet.py`): Laufen Unity, Blender oder
der Generator anderer Sitzungen oder liegt die Last über 0,3 je Kern, während Befehle anderer Sitzungen die CPU
nutzen, starten sie sich über das Werkzeug neu; der Bericht nennt es (`robot_report.json` › `quiet`, letzte Zeile von
`check_output.sh`). Pausiert schon eine andere Sitzung, laufen sie normal (zwei Pausen hielten sich gegenseitig für
immer an). `ROBOGEN_NO_PAUSE=1` schaltet es ab; fehlt das Werkzeug, laufen sie mit Warnung normal.

## Prüfablauf vor dem Commit

0. **Zuerst `python3 scripts/testplan.py`:** liest den Diff (gegen `main`, auch Ungesichertes) und nennt Weg, Stufen,
   Prüfungen und Zeit – nur Doku: nichts; nur Daten: schneller Weg (unten); Modell-, Rig-, Clip-, UV-, Bake-, Export-
   oder Cache-Code: der volle Ablauf hier; Viewer: Build und Tests; Export/Clips/Texturen/Masken/Looks/Shader:
   Unity-Testbett. `--run` führt den Worktree-Teil aus (sichert und vergleicht die Berichte, prüft die Cache-Treffer),
   `--base <Tag> --after-merge --run` im Haupt-Checkout den Rest. Ändern sich nur Werte in einer Code-Tabelle
   (`FINISH_PARAMS` …), sieht der Diff das nicht: `--path data`. Die Zuordnung ist eine Tabelle in `testplan.py`
   (`RULES`, `AREAS`) mit Tests – neue Dateien dort eintragen.
1. Vor der Änderung den `robot_report.json` der betroffenen Stufe sichern.
2. Stufe(n) laufen lassen; `qa` (und `clips`, `type_bounds`) gegen den gesicherten Bericht vergleichen. Ohne
   gewollte Modelländerung müssen sie **identisch** sein; gewollte Änderungen im Bericht benennen.
3. Auf dem Volllauf: `scripts/check_output.sh` (Ausgabe als Argument, Standard `output`) – `verify_robot.py`,
   Viewer-Build und -Tests und Unity-Testpaket **gleichzeitig**; alle grün. Einzeln wie in 6 und 7.
4. `quick` zweimal: Beim zweiten Lauf müssen UV-Cache und Bake-Cache treffen (Log: „UV cache hit“, „bake cache hit“);
   dank des geteilten Caches meist schon beim ersten Lauf eines neuen Worktrees. QA-Ergebnisse kommen aus `qa/`, solange Mesh, Rig, Clip und QA-Code gleich sind („QA cache: N clip results
   reused“); nach Änderungen an der QA selbst reicht das (der Code steckt im Schlüssel), `--no-cache` prüft alles neu.
   Vorschaubilder kommen aus `render/`, solange die Szene beim Rendern gleich aussieht („render cache: N images
   reused“); Fehlersuche mit `ROBOT_KEEP_WORKER_LOGS=1` und `ROBOT_RENDER_DIGEST_DUMP` (`docs/TECHNIK.md` › 12).
   Ohne Änderung an Modell, Rig, Clips und Export-Code schreibt der zweite Lauf keine FBX und keine Viewer-Daten
   („unchanged outputs kept: main, modular, forms, viewer“) und misst keine Clips („85 clips from the cache“);
   Fehlersuche mit `ROBOT_OUTPUT_DIGEST_DUMP=<Datei>`.
5. Sichtbare Änderungen ansehen: `Previews/skins.png`, `Previews/hero.png`, bei Animationen die Kontaktbögen.
6. Bei Viewer-Änderungen `viewer/build.sh` und `viewer/test_viewer.sh`.
7. Bei Änderungen an Export, Clips, Texturen, Masken oder Looks: `unity/run_testbed.sh` (eigenes Unity-Projekt, nie das
   Spiel; Bericht `output/UnityTestbed/report.json`; Skill `rg-unity-testbed`). Shader-Formeln in `decorate_skin()`, Viewer und
   `unity/RobotTestbed/Assets/RobotTestbed/Shaders/RobotBody.shader` gleich halten.

### Schneller Weg für reine Datenänderungen (seit 1.20.1)

Gilt, wenn sich **nur Werte** ändern – Tabellen und Konfiguration wie `FINISH_PARAMS`, Skins, Farben, Looks, Texte,
Doku –, nicht Modell, Rig, Clips, UVs, Backen, Export, Viewer-, Shader- oder Cache-Code. Dann statt des vollen Ablaufs:

1. Im Worktree `quick` **einmal** und `standard` **einmal**, `qa`/`clips`/`type_bounds` gegen den gesicherten Bericht
   (Schritt 2); kein zweiter `quick` (Schritt 4), kein `full` und kein kalter Lauf im Worktree.
2. Nach dem Merge genau **ein** `full` im Haupt-Checkout (warm aus dem geteilten Cache) und darauf **einmal**
   `scripts/check_output.sh`. Das ist zugleich der Lieferstand in `output/`.
3. Ansehen: `Previews/skins.png` und `Previews/hero.png` (Schritt 5); Viewer- und Unity-Testbett laufen in
   `check_output.sh` mit.
4. Unter Last pausieren `full` und `check_output.sh` andere Sitzungen selbst (oben) – im Bericht nennen.
5. Zielzeit: unter 15 Minuten vom fertigen Diff bis zum Release. Fällt dabei etwas auf (Cache-Fehltreffer, rote
   Prüfung, unerwartete Änderung im Bericht), gilt ab da der volle Prüfablauf oben.

### Aufträge an andere Sitzungen

Wer Arbeit an eine andere Sitzung oder einen Subagent gibt, schreibt den Auftrag so, dass keine Erkundungsläufe nötig
sind – Vorlage:

```text
Änderung: <was, welche Dateien; Art: Doku | Daten | Modell/Rig/Clips | Export | Viewer | Unity | Werkzeug>
Prüfweg: <Ausgabe von `python3 scripts/testplan.py` (Weg und Schritte), ggf. `--path data`>
Erwartet im Bericht: <qa/clips/type_bounds identisch | gewollte Abweichung: welcher Schlüssel, welche Clips/Typen>
Release: <ja: Version x.y.z „Name“, Tag, Release-Branch, full im Haupt-Checkout | nein: nur Commit/Branch>
Melden: Commit/Tag, Zeiten je Schritt, Cache-Treffer, Pause ja/nein, Prüfergebnisse (Zahlen), Offenes
```

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
| `hookify` | Repo-eigene Regeln als `.claude/hookify.<name>.local.md` (eingecheckt) |
| `ce-shared` | eigenes Plugin für Cube Escape, Cubegen und den Roboter-Generator (privates GitHub-Repo ffunkfabian-hash/ce-agent-tools, eingetragen in `.claude/settings.json`): gemeinsame Regeln und die Werkzeug-Profile der Subagents (`.claude/agent-guard.json`) |
| `unity`, `unity-perf` | erst, wenn der Roboter in Cube Escape eingebaut wird – dann gelten die Regeln dort |

**Hooks blockieren** (hookify) Änderungen von Hand an `output/` und Änderungen am Spielprojekt Cube Escape, (Plugin
`ce-shared`) nacktes `git stash` (geteilter Stash-Stapel), Force-Push, Teleportieren einer Cloud-Sitzung und Unity bzw.
die Unity-CLI auf Cube-Escape-Checkouts oder ohne `--project-path`; sie **halten das Sitzungsende an**, bis nach einer
Änderung in `scripts/` eine Teststufe lief, und **warnen** bei Commit/Merge/Tag/Push (nur auf Wunsch), bei Code rund
um `unwrap()`/Backen (Schemaversion), bei Viewer-Änderungen und GitHub-Workflows. Ändert sich eine dieser Regeln,
ändert sich ihre Hook-Datei mit (gemeinsame im Plugin-Repo, mit Tests).
**Subagents** (`.claude/agents/`): `rg-test-runner` (Teststufe und Prüfablauf, berichtet nur) und `rg-doc-keeper`
(Doku auf Endstand, nur Markdown) – Befehle und Schreibpfade begrenzt `.claude/agent-guard.json`.

## Cache-Regeln (geteilter Cache, seit 1.20.2)

- **Ein Cache für alle Checkouts:** `scripts/robocache.py`, Ordner `~/Library/Caches/RobotGen/cache` (`ROBOGEN_CACHE`;
  Linux `$XDG_CACHE_HOME/RobotGen/cache`), je Art ein Unterordner (`uv`, `ao`, `bake`, `textures`, `skins`, `source`,
  `render`, `qa`, `clips`, `outputs` = FBX, Formen, Viewer-Daten). Haupt-Checkout, Worktrees und Nachtläufe teilen
  ihn; er überlebt gelöschte Worktrees. In `<out>/cache` bleibt nur Checkout-Zustand: `outputs.json` (Pfade relativ
  zur Ausgabe), `timings.json` (Zeitband dieser Ausgabe) und die Arbeitskopien der Vorschau-Skins (`skin_textures`).
- **Gleichzeitige Läufe:** schreiben nur atomar (`robocache.write_atomic`/`put_file`/`put_dir`: Temp-Datei, dann
  Umbenennen) und löschen nie einen Eintrag, weil es einen neueren gibt – ein anderer Checkout braucht ihn vielleicht.
  Aufgeräumt wird nur nach Größe (`robocache.prune` am Ende eines Laufs, `ROBOGEN_CACHE_LIMIT_GB`, Standard 6):
  am längsten nicht benutzte Einträge zuerst, nichts, was in den letzten 2 Stunden benutzt wurde; ein Treffer
  berührt den Eintrag. Neue Cache-Arten über diese Funktionen, nie mit `open()` + `os.replace` von Hand.
- Schlüssel ohne Pfade: Was in einem anderen Checkout gleich ist, muss denselben Schlüssel geben (Bilder nach Inhalt
  oder Dateiname, nie mit Ordner).
- Schlüssel hängen am Inhalt, nicht an Dateizeiten: Geometrie in kanonischer Ecken-Reihenfolge (Blenders
  Flächenreihenfolge und Insel-Packen sind nicht deterministisch), Materialien als Node-Signatur, Quelltexturen,
  Einstellungen.
- Was der Schlüssel nicht sehen kann, braucht eine Schemaversion: `UV_SCHEMA` bei Änderungen an `unwrap()`,
  `BAKE_SCHEMA` bei Änderungen am Backen. Im Zweifel erhöhen – ein falscher Treffer ist schlimmer als ein Neu-Backen.
- `--no-cache` liest keinen Cache, schreibt die UVs aber trotzdem (die Render-Worker brauchen sie).
- Beim ersten Lauf übernimmt `robocache.prepare` alte Caches (`<out>/cache` bis 1.20.1): die eigene Ausgabe
  verschiebt er, die Ausgaben des Haupt-Checkouts (`output`, `_quick`, `_standard`) klont er einmal (`migrated.json`)
  samt Quelltexturen, statischen Texturen, Skins und Exporten – bytegleich, sonst träfe kein Vorschaubild mehr; alte,
  pfadhaltige Bake-Schlüssel erkennt `bake_textures` über `legacy_sources.json` (`docs/TECHNIK.md` › 20).
- Code im Schlüssel (QA, Clip-Daten, Export: `QA_CODE_PARTS`, `CLIP_CODE_PARTS`, `EXPORT_CODE_PARTS`) wird per
  Funktionsname über alle `robogen`-Module gesucht (`code_source`). Schreibt eine neue Funktion an einer gecachten
  Ausgabe mit, gehört ihr Name in die passende Liste – sonst bleibt eine veraltete Datei liegen.
- Vorschau-Skins liegen in `<out>/cache/skin_textures` (Kopien aus `skins/`), in `Textures/` nur der Standard-Skin.
- ImageMagick-Texturen hängen am Skript-Hash von `make_textures.py` und an der ImageMagick-Version.

## Code-Aufbau (1.17)

`scripts/generate_robot.py` ist nur Einstieg und `main()`; die Schritte stehen in `scripts/robogen/` (Reihenfolge und
Regeln in `robogen/__init__.py`). Die Module teilen einen Namensraum per `from robogen.<vorher> import *`: ein Modul
nutzt nur Namen aus sich und früheren Modulen (sonst `NameError` zur Laufzeit). Gemeinsamer Zustand als Container
(Listen, Dicts); ein einfacher Wert, der zur Laufzeit gesetzt wird, wird in seinem Modul gesetzt
(`robogen.preview.PREVIEW_SAMPLE_SCALE`). Pfade über `SCRIPTS_DIR` und `GENERATOR` aus `core`, nie über `__file__`
eines Moduls.

## Worker

QA und Vorschau-Renders sind Arbeitspakete (`make_jobs`). Worker sind eigene Blender-Prozesse, die das Modell aus
demselben Code neu bauen; sie dürfen deshalb nur von Code abhängen, nie von Zustand des Hauptprozesses außer den
übergebenen Dateien (UV-Cache, gebackene Texturen, Quelltexturen). Ergebnisse werden in fester Reihenfolge
zusammengeführt – die Ausgabe darf nicht davon abhängen, wer welches Paket gerechnet hat. Was nur für Vorschauen
umgeschaltet wird (schnelle Augen, Raytracing), wird vor dem Speichern der `.blend` zurückgesetzt.
