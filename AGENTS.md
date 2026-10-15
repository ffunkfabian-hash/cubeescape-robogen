# Roboter-Generator – Regeln für KI-Agenten

Gilt für alle Agenten (Claude Code, Codex, …). Langform: `README.md` (Teststufen, Messwerte, Aufbau),
`CHANGELOG.md` (Versionen), `docs/FAHRPLAN.md` (Fahrplan; führend ist das Claude Doc, Link dort), `docs/TECHNIK.md`
(Techniken, Lehren und Betriebsdetails; Übertrag auf den Cubegen-Modell-Editor – bei jeder neuen Technik mitpflegen).
Diese Datei hält nur, was jede Aufgabe braucht; Begründungen, Messwerte, Fallen: `docs/TECHNIK.md`.

## Skills

Arbeitsanweisungen je Bereich in `.agents/skills/<name>/SKILL.md` (`.claude/skills` ist ein Link darauf; andere
Agenten lesen die passende `SKILL.md` vor der Arbeit):

| Aufgabe berührt … | Skill |
|---|---|
| Unity-Testpaket, Unity-Vorlagen (Shader, Look, Appearance), Unity-Laufzeit | `rg-unity-testbed` |
| Übergabe an das Spiel (FBX, Masken, Look, Clip-Daten, Kapseln, Emitter) | `rg-unity-integration` |

## Sprache

Antworten an den Nutzer, README, CHANGELOG, Doku und Commit-Messages auf **Deutsch**. Code, Bezeichner,
Kommentare und Log-Ausgaben auf **Englisch**.

## Ausgaben

- `output/` ist nicht versioniert und entsteht nur durch den Generator. **Nie von Hand ändern** – auch nicht
  „schnell“ eine Textur, einen Clip-Wert oder eine FBX. Fehler werden im Generator behoben und neu erzeugt.
- Das Spielprojekt Cube Escape wird von diesem Repo aus nicht verändert.
- Namen von Skins, Bauteilen, Ausdrücken, Motiven und Oberflächen sind ewige IDs (gespeicherte Looks).

## Skins und Masken

- Skins sind Daten (`data/skins.json`), nie Code. IDs von Skins, Abzeichen (`BADGES`), Leuchtmustern
  (`LINE_PATTERNS`), Lackzonen (`PAINT_ZONES`), Schichten (`LAYERS`), Werkstoffen (`MATERIALS`) und Oberflächen sind
  ewig; Listen nur am Ende erweitern (Kanal = Position).
- Neue Abzeichen/Muster/Zonen: Maske im Generator, Formel in `decorate_skin()` **und** im Viewer-Shader gleich halten,
  Eintrag in `robot_config.json`, Prüfung in `verify_robot.py`.
- Viewer-Shader: Look-Werte in die Parameter-Textur (`writeParams`, `P(i)`), nicht in neue Shader-Argumente – SceneKit
  zeichnet sonst kommentarlos pink (`docs/TECHNIK.md` › 8). Neue Abzeichen-Masken: in Unity einen Wähler mehr.

## Aufsätze und Bewegung

- Jeder Aufsatz (Accessoire-Variante) steckt in mindestens einem Robotertyp – nur so prüft die QA ihn in allen Clips
  (`verify_robot.py` › `verify_bolton`). Neue Slots am Ende von `ACCESSORY_SLOTS`; alte Looks bekommen „None“.
- Schwebende Teile nur absichtlich (`FLOATING_PARTS`), Partikel nie als Mesh, sondern als `EMITTERS`.
- Nachschwingen nur gebacken (`SPRINGS`), Grenzen so, dass nichts in den Körper schwingt.
- Typ-Emotes (`robotTypes[].emote`) gehören zu ihrem Typ: QA und Spiel nur mit diesem Typ. Gewollte Ausnahmen der QA
  stehen benannt in `ACCEPTED_OVERLAPS` / `ACCEPTED_DETACH` / `ACCEPTED_GROUND`, nie pauschal.

## Proportionen und Körperformen (1.14)

- Maße ändern über `PROPORTIONS` / `SHAPE_VARIANTS` (Umformung je Knochen nach dem Bauen), nie über Gelenke: Gelenke,
  Clips, IK-Ziele, Kletterhöhen (ClimbUp 1,20 m) und Bodenkontakte sind Spielvertrag. Nach jeder Maßänderung die QA
  aller Typen und Formen (`--tier full --skip-bake --no-render` reicht).
- Neue Teile im alten Maß bauen und mit `conform_front` auflegen; Sockel und Federknochen über `shaped()` platzieren.
- Formen (`FORMS`) sind Daten: IDs ewig, nur am Ende erweitern. Mesh-Formen behalten Rig, Vertex-Gruppen-Reihenfolge,
  Materialien und die UV-Ebene `UVMap`; jede Geometrie-Form läuft als `Form_<id>` durch die QA aller Clips. Lochformel
  in Vorschau, Viewer und Unity-Shader gleich halten.

## Animationen

- Jeder neue Clip bekommt eine Kategorie in `CLIP_CATEGORIES` (der Generator bricht sonst ab); die Bau-Phase
  (`group`) ist nur für Kontaktbögen, QA-Pakete und `--groups`. Kategorie-IDs sind ewig.
- Bestehende Clips nie ändern (Spielvertrag); neue Clips am Ende anhängen (`ANIMS`, Kategorie-Listen), eigene Bau-Phase.
- Clips unter einer Spielkapsel prüft `verify_robot.py` an `clips.<Name>.height` (Kriechen < 0,62 m).
- Lauf-Clips im Spieltempo (1.19): gesetzter Kontakt mit konstantem Winkel, Schrittlänge = Tempo × gesetzte Frames / 30,
  mindestens zwei Frame-Schritte, sonst misst `measure_clips` kein `naturalSpeed`; `verify_longstride` prüft jeden Schritt.

## Code-Aufbau und Worker

- `scripts/generate_robot.py` ist nur Einstieg; die Schritte stehen in `scripts/robogen/` (Reihenfolge und Regeln in
  `robogen/__init__.py`). Die Module teilen einen Namensraum per `from robogen.<vorher> import *`: ein Modul nutzt nur Namen
  aus sich und früheren Modulen (sonst `NameError`). Gemeinsamer Zustand als Container; ein zur Laufzeit gesetzter einfacher
  Wert wird in seinem Modul gesetzt (`robogen.preview.PREVIEW_SAMPLE_SCALE`). Pfade über `SCRIPTS_DIR`/`GENERATOR` aus `core`.
- QA und Vorschau-Renders sind Arbeitspakete (`make_jobs`) in eigenen Blender-Prozessen, die das Modell aus demselben
  Code neu bauen: nur von Code abhängen, nie von Zustand des Hauptprozesses außer den übergebenen Dateien (UV-Cache,
  Texturen). Ergebnisse in fester Reihenfolge zusammenführen (nicht davon abhängig, wer rechnete); nur für Vorschauen
  Umgeschaltetes (schnelle Augen, Raytracing) vor dem Speichern der `.blend` zurücksetzen.

## Teststufen

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 --python scripts/generate_robot.py -- --tier quick
```

| Stufe | wann | Ausgabe |
|---|---|---|
| `quick` | beim Arbeiten, nach jeder Änderung | `output/_quick` |
| `standard` | vor jedem Commit | `output/_standard` |
| `full` | vor Release, für den Lieferstand | `output` |

Werkzeug-Tests ohne Blender: `python3 -m unittest discover -s tests`. Jeder Lauf schreibt `<out>/logs/robot_report.json` und
bewertet die Zeit im **Zeitband** (`timings.band`): ein „SLOWER“ auf ruhiger Maschine ist ein Befund, unter Last nichts
schließen, sondern ruhig nachmessen (`docs/TECHNIK.md` › 21).

**Cloud-Sitzungen (claude.ai/code):** Der SessionStart-Hook stellt `blender` (PyPI-`bpy`) und `magick` bereit: `blender -b …
--python scripts/generate_robot.py -- --tier quick` statt des macOS-Pfads. Berichte nur gegen einen Cloud-Lauf vergleichen;
Viewer, Unity-Testpaket und der Lieferstand (`full`) gibt es nur lokal (`docs/TECHNIK.md` › 21).

## Ganze Leistung des Geräts (gemeinsame Regel mit Cube Escape und Cubegen)

- Braucht eine Sitzung den Rechner für sich (Messungen, Benchmarks, Zeitbudgets, Abnahmen), darf sie andere Sitzungen
  pausieren: `pause_sessions.py run -- <Befehl>` (`/Users/ffunk/Claude/Cube Escape/.agents/skills/ce-testing/scripts/`) hält nur
  Befehle anderer Claude-/Codex-Sitzungen an und setzt sie immer fort. Nur so lange wie nötig, im Bericht nennen.
- `full` und `scripts/check_output.sh` tun das selbst (`scripts/quiet.py`; Bericht in `robot_report.json` › `quiet`);
  pausiert schon eine andere Sitzung, laufen sie normal. `ROBOGEN_NO_PAUSE=1` schaltet es ab.
- **Ein schwerer Lauf zur Zeit:** `full`, `check_output.sh` und `unity/run_testbed.sh` warten auf die rechnerweite Sperre
  `~/Library/Caches/CubeEscape/heavy-run.lock` (`scripts/heavy_lock.py`; `… status` zeigt den Halter, `CE_HEAVY_LOCK=0`
  überspringt sie). **Erst die Sperre, dann die Pause**. Vertrag nur zusammen mit Cube Escape und Cubegen ändern
  (Einzelheiten: `docs/TECHNIK.md` › 21).

## Prüfablauf vor dem Commit

0. **Zuerst `python3 scripts/testplan.py`:** liest den Diff (gegen `main`, auch Ungesichertes) und nennt Weg, Stufen,
   Prüfungen und Zeit – nur Doku: nichts; nur Daten: schneller Weg (unten); Modell-, Rig-, Clip-, UV-, Bake-, Export- oder
   Cache-Code: der volle Ablauf hier; Viewer: Build und Tests; Export/Clips/Texturen/Masken/Looks/Shader: Unity-Testbett.
   `--run` führt den Worktree-Teil aus (sichert und vergleicht die Berichte, prüft die Cache-Treffer), `--base <Tag>
   --after-merge --run` im Haupt-Checkout den Rest. Nur Werte in einer Code-Tabelle (`FINISH_PARAMS` …) sieht der Diff
   nicht: `--path data`. Die Zuordnung ist eine Tabelle in `testplan.py` (`RULES`, `AREAS`) – neue Dateien dort eintragen.
1. Vor der Änderung den `robot_report.json` der betroffenen Stufe sichern.
2. Stufe(n) laufen lassen; `qa` (und `clips`, `type_bounds`) gegen den gesicherten Bericht vergleichen. Ohne gewollte
   Modelländerung müssen sie **identisch** sein; gewollte Änderungen im Bericht benennen.
3. Auf dem Volllauf: `scripts/check_output.sh` (Ausgabe als Argument, Standard `output`) – `verify_robot.py`, Viewer-Build
   und -Tests und Unity-Testpaket **gleichzeitig**; alle grün. Einzeln wie in 6 und 7.
4. `quick` zweimal: Beim zweiten Lauf müssen UV-, Bake-, QA- und Render-Cache treffen und ohne Änderung an Modell, Rig, Clips
   und Export-Code keine FBX und Viewer-Daten geschrieben werden (Log-Zeilen und Fehlersuche: `docs/TECHNIK.md` › 12, 21).
5. Sichtbare Änderungen ansehen: `Previews/skins.png`, `Previews/hero.png`, bei Animationen die Kontaktbögen.
6. Bei Viewer-Änderungen `viewer/build.sh` und `viewer/test_viewer.sh`.
7. Bei Änderungen an Export, Clips, Texturen, Masken oder Looks: `unity/run_testbed.sh` (eigenes Unity-Projekt, nie das
   Spiel; Skill `rg-unity-testbed`). Shader-Formeln in `decorate_skin()`, Viewer und `RobotBody.shader` gleich halten.

### Schneller Weg für reine Datenänderungen (seit 1.20.1)

Gilt, wenn sich **nur Werte** ändern (Tabellen/Konfiguration wie `FINISH_PARAMS`, Skins, Farben, Looks, Texte, Doku), nicht
Modell, Rig, Clips, UVs, Backen, Export, Viewer-, Shader- oder Cache-Code. Dann statt des vollen Ablaufs:

1. Im Worktree `quick` **einmal** und `standard` **einmal**, `qa`/`clips`/`type_bounds` gegen den gesicherten Bericht
   (Schritt 2); kein zweiter `quick`, kein `full`, kein kalter Lauf im Worktree.
2. Nach dem Merge genau **ein** `full` im Haupt-Checkout (warm aus dem geteilten Cache), darauf **einmal**
   `scripts/check_output.sh` (Viewer und Unity-Testbett laufen mit) – das ist der Lieferstand in `output/`.
3. `Previews/skins.png` und `Previews/hero.png` ansehen; Pausen im Bericht nennen. Ziel: unter 15 Minuten bis zum Release;
   fällt etwas auf (Cache-Fehltreffer, rote Prüfung, unerwartete Änderung), gilt ab da der volle Prüfablauf.

### Release (seit 1.20.3)

`python3 scripts/release.py <Version>` – aus Worktree oder Haupt-Checkout; ohne `--apply` nur Probelauf (prüft saubere
Checkouts, nur Fast-Forward, freie Tag-/Branch-Namen, `## <Version>` im `CHANGELOG.md`, Dateigröße, Zugangsdaten). Mit
`--apply`: `merge --ff-only`, annotierter Tag `v<Version>` (`-m`/`-F`), Branch `release/<Version>`, **ein** atomarer Push
ohne `--force`; ein Abbruch setzt mit demselben Befehl fort. Danach die Schritte „nach dem Merge“ aus `testplan.py` und den
Worktree entfernen.

### Aufträge an andere Sitzungen

Aufträge so schreiben, dass keine Erkundungsläufe nötig sind – Vorlage:

```text
Änderung: <was, welche Dateien; Art: Doku | Daten | Modell/Rig/Clips | Export | Viewer | Unity | Werkzeug>
Prüfweg: <Ausgabe von `python3 scripts/testplan.py` (Weg und Schritte), ggf. `--path data`>
Erwartet im Bericht: <qa/clips/type_bounds identisch | gewollte Abweichung: welcher Schlüssel, welche Clips/Typen>
Release: <ja: Version x.y.z „Name“ mit `python3 scripts/release.py x.y.z --apply`, danach im Haupt-Checkout `testplan.py --base <vorheriger Tag> --after-merge --run` | nein: nur Commit/Branch>
Melden: Commit/Tag, Zeiten je Schritt, Cache-Treffer, Pause ja/nein, Prüfergebnisse (Zahlen), Offenes
```

## Cache-Regeln (geteilter Cache, seit 1.20.2)

- **Ein Cache für alle Checkouts:** `scripts/robocache.py`, `~/Library/Caches/RobotGen/cache` (`ROBOGEN_CACHE`). Schreiben
  nur atomar über `robocache.write_atomic`/`put_file`/`put_dir` (nie `open()` + `os.replace`), nie einen Eintrag löschen,
  weil es einen neueren gibt; Aufräumen nur nach Größe (`robocache.prune`).
- **Schlüssel** hängen am Inhalt, nicht an Pfaden oder Dateizeiten (was in einem anderen Checkout gleich ist, gibt denselben
  Schlüssel); Geometrie in kanonischer Ecken-Reihenfolge.
- **Schemaversion** für alles, was der Schlüssel nicht sieht: `UV_SCHEMA` bei Änderungen an `unwrap()`, `BAKE_SCHEMA` beim
  Backen. Im Zweifel erhöhen – ein falscher Treffer ist schlimmer als ein Neu-Backen.
- **Code im Schlüssel** (`QA_CODE_PARTS`, `CLIP_CODE_PARTS`, `EXPORT_CODE_PARTS`, per Funktionsname): schreibt eine neue
  Funktion an einer gecachten Ausgabe mit, gehört ihr Name in die passende Liste – sonst bleibt eine veraltete Datei liegen.
- `--no-cache` liest keinen Cache, schreibt die UVs aber trotzdem (Render-Worker brauchen sie). Mehr: `docs/TECHNIK.md` › 20, 21.

## Plugins und Hooks (Claude Code)

`CLAUDE.md` importiert diese Datei; andere Agenten befolgen dieselben Regeln aus dem Text. Plugins (Blender-MCP, `code-review`
vor jedem Release, `github`, `quick-gate-python`, `hookify`, `ce-shared` …): `docs/TECHNIK.md` › 21. **Hooks blockieren**
Handänderungen an `output/` und am Spielprojekt Cube Escape, nacktes `git stash`, Force-Push, das Teleportieren einer
Cloud-Sitzung und Unity bzw. Unity-CLI auf Cube-Escape-Checkouts oder ohne `--project-path`; sie **halten das
Sitzungsende an**, bis nach einer Änderung in `scripts/` eine Teststufe lief, und **warnen** bei Commit/Merge/Tag/Push
(nur auf Wunsch), Code rund um `unwrap()`/Backen, Viewer-Änderungen und GitHub-Workflows. Ändert sich eine Regel, ändert
sich ihre Hook-Datei mit. **Subagents** (`.claude/agents/`): `rg-test-runner` (berichtet nur), `rg-doc-keeper` (nur Markdown).
