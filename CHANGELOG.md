# Changelog

Versionen des Roboter-Generators (zählen getrennt von Cube Escape und Cubegen). Jede Version hat einen Tag
`v<Version>` und einen Release-Branch `release/<Version>`.

## 1.20.7 „Powdercoat“ – 2026-10-15

Nur Werkzeuge, Modell und Lieferstand unverändert (der Generator trägt nur die neue Versionsnummer).

* **Gemeinsamer Standard** (Plugin `ce-shared` 0.4): `.ce-repo.json`, `./check` / `./release`, eingebettete Werkzeuge in
  `scripts/ce_tools/` (per `ce_sync.py`, nicht von Hand ändern); `scripts/heavy_lock.py` leitet auf die gemeinsame Sperre weiter.
* `AGENTS.md` in der Standard-Gliederung auf 150 Zeilen (Langtext nach `docs/TECHNIK.md` › 22), neu `docs/ENTSCHEIDUNGEN.md`.

## 1.20.6 „Powdercoat“ – 2026-10-15

Nur Werkzeuge, Modell und Lieferstand unverändert (der Generator trägt nur die neue Versionsnummer). Baut auf 1.20.5 auf.

* **`full` rechnet nie im Halter der Sperre** (`scripts/generate_robot.py` › `quiet_full_run`, gemeinsam mit Cube Escape
  0.61.4): Auf ruhiger Maschine (kein Grund zu pausieren) lief der Volllauf im Blender weiter, der die Sperre für schwere
  Läufe selbst hielt – ein TERM/HUP beendete ihn sofort (Standardaktion), die Sperre war frei, während seine Worker noch
  rechneten. Jetzt startet `full` sich in jedem Modus über `quiet.run` neu (wie bisher nur mit Pause): Der erste Blender
  hält die Sperre und wartet nur, `run_forwarding` gibt Signale an die ganze Prozessgruppe des Laufs weiter und kehrt
  erst zurück, wenn sie leer ist. Kosten: ein zusätzlicher Blender-Start (≈ 0,6 s gemessen).
* **Neue Tests** (`tests/test_heavy_lock.py`): ein TERM an den Halter eines vollen Laufs (Python und – wenn installiert –
  Blender als Halter) erreicht die Worker, bevor die Sperre frei wird; `quiet_full_run` gibt den Lauf in jedem Modus an
  `quiet.run` (keine eigene Sperre, kein Weiterlaufen im Halter).

## 1.20.5 „Powdercoat“ – 2026-10-15

Nur Werkzeuge, Modell und Lieferstand unverändert (der Generator trägt nur die neue Versionsnummer).

* **Die Sperre für schwere Läufe hält bis zum letzten Enkelprozess** (`scripts/heavy_lock.py`, gemeinsam mit Cube Escape
  0.61.3, Cubegen 0.27.0 und Toolgen; Befund eines Codex-Reviews von Toolgen 0.4 „Fieldstrip“): `run_forwarding` – also
  `heavy_lock.py run`, `quiet.py run` und der volle Lauf über `pause_sessions.py` – startet den Befehl in einer eigenen
  Prozessgruppe, gibt TERM/HUP/INT an die ganze Gruppe weiter und kehrt erst zurück, wenn die Gruppe leer ist. Vorher war
  die Sperre frei, sobald der direkte Kindprozess endete – starb `check_output.sh`, lief Blender ohne Sperre weiter.
  Bleibt nach dem Ende des Befehls etwas übrig, sagt der Wrapper nach fünf Sekunden, welche Prozessgruppe die Sperre
  noch hält. Prozesse, die sich absichtlich lösen (`start_new_session`), halten sie nicht.
* **Neue Tests** (`tests/test_heavy_lock.py`): ein Enkelprozess hält die Sperre, nachdem sein Elternprozess per SIGKILL
  starb; ein TERM an den Wrapper erreicht den Enkelprozess, bevor die Sperre frei wird.

## 1.20.4 „Powdercoat“ – 2026-10-15

Nur Doku, Modell und Lieferstand unverändert (der Generator trägt nur die neue Versionsnummer).

* **`AGENTS.md` schlanker:** in jeder Sitzung geladen, daher auf das gekürzt, was jede Aufgabe braucht (263 → 184 Zeilen,
  20,7 → 13,7 KB). Alle Regeln bleiben, Überschriften (`Teststufen`, `Prüfablauf`, `Release`, `Cache-Regeln` …) und
  Prüfablauf-Schritte 0–7 unverändert, damit Verweise aus Skripten, Hooks und Subagents stimmen.
* **Verschoben nach `docs/TECHNIK.md` › 21 (Betriebsdetails):** Cloud-Sitzungen (Hook, Grenzen), Zeitband und seine
  Toleranzen, Pause und Schwerer-Lauf-Sperre im Einzelnen, Log-Zeilen und Fehlersuche des zweiten `quick`, Release-Prüfungen,
  Cache im Detail (Unterordner, Aufräumen, Migration, Code im Schlüssel) und die Plugin-Tabelle.

## 1.20.3 „Powdercoat“ – 2026-10-15

Nur Werkzeuge und Ablauf, Modell und Lieferstand unverändert (`qa`, `clips`, `type_bounds` identisch zu 1.20.2).

* **Ein schwerer Lauf zur Zeit** (`scripts/heavy_lock.py`, gemeinsam mit Cube Escape 0.59 „Turnstile“ und Cubegen
  0.25): `full`, `scripts/check_output.sh` und `unity/run_testbed.sh` warten auf die rechnerweite Sperre
  `~/Library/Caches/CubeEscape/heavy-run.lock` (exklusives `flock`; der Halter schreibt `{"pid", "what", "checkout",
  "since"}`, Wartende nennen ihn und melden sich alle fünf Minuten). Der Dateideskriptor wird nie vererbt
  (`O_CLOEXEC`), Kinder des Halters bekommen `CE_HEAVY_HELD=1` (das Unity-Testbett in `check_output.sh` wartet nicht
  auf seinen Elternprozess), `CE_HEAVY_LOCK=0` überspringt die Sperre; Signale an den Wartenden gehen an den Befehl
  weiter. `python3 scripts/heavy_lock.py status` zeigt den Halter.
* **Erst die Sperre, dann die Pause** (`scripts/quiet.py`): Wer die Sperre hält, zählt schwere Prozesse anderer
  Sitzungen nur noch, wenn sie rechnen (über 5 % CPU) – wer an der Sperre wartet, ist kein Grund zu pausieren. Der
  Bericht nennt die Sperre (`robot_report.json` › `quiet`, letzte Zeile von `check_output.sh`: „heavy lock: held,
  waited N s“).
* **Release-Befehl** `scripts/release.py <Version>`: Probelauf als Standard, `--apply` führt aus; aus Worktree oder
  Haupt-Checkout. Prüft saubere Checkouts, Fast-Forward gegen `main` und `origin/main`, freie Tag- und Branch-Namen,
  `## <Version>` im CHANGELOG, keine Datei über 100 MB im Push, keine Zugangsdaten in neuen Zeilen oder Dateien; dann
  `merge --ff-only`, annotierter Tag, `release/<Version>` und ein atomarer Push ohne `--force`, Links im Bericht. Ein
  abgebrochener Release setzt mit demselben Befehl fort. AGENTS.md › Release, Vorlage für Aufträge verweist darauf;
  Hook `git-nur-auf-wunsch` warnt auch bei `release.py --apply`, `rg-test-runner` darf den Probelauf.
* `testplan.py`: Regeln für die neuen Skripte, Test „jede versionierte Datei hat eine Regel“.
* **Tests:** `python3 -m unittest discover -s tests` – 79 Tests ohne Blender (33 neu): Halterzeile und Status (auch
  mit dem Werkzeug von Cube Escape gelesen), Warten und Freigabe, kein Erben durch ein Hintergrundkind,
  `CE_HEAVY_LOCK=0`/`CE_HEAVY_HELD=1`, `quiet.py run` mit Sperre, wartende Läufe kein Pausengrund, `run_testbed.sh`
  wartet bzw. nicht im Halter; Release in temporären Repositories (Probelauf ändert nichts, Worktree-Release mit
  Fast-Forward, annotiertem Tag, Release-Branch, atomarem Push, Fortsetzen nach abgelehntem Push, dreckige Checkouts,
  kein Fast-Forward, entfernter Stand weiter, vergebener Tag, große Dateien, Zugangsdaten). Gefunden beim Volllauf:
  Blender setzt SIGINT außerhalb von Python – das Zurücksetzen des Handlers schlug fehl; jetzt nur, was Python setzte.
* **Prüfung** (`testplan.py --run`, voller Weg, 3:48 min): `quick` ×2 (alle Cache-Treffer), `standard`, `full` –
  `qa`/`clips`/`type_bounds` (und Formen, Dreiecke, LODs, Kapseln) identisch zu 1.20.2, 1 649/1 649 Bilder aus dem
  Cache, keine FBX neu; `check_output.sh` grün (verify 323/323, Viewer 50 PASS, Unity 29/29 + 16/16). Die Sperre hielt
  der Volllauf sichtbar für Cube Escapes `heavy_lock.py status`.

## 1.20.2 „Powdercoat“ – 2026-10-15

Nur Werkzeuge und Ablauf, Modell und Lieferstand unverändert (Volllauf: `qa`, `clips`, `type_bounds`, Dreiecke, LODs,
Kapseln, Formen identisch zu 1.20.0; alle 1 649 Vorschaubilder bytegleich aus dem Cache). Ziel: kürzere Prüf- und
Release-Zeit – 1.20.0 brauchte rund 55 Minuten, weil jeder neue Worktree mit leerem Cache begann.

* **A – ein Cache für alle Checkouts** (`scripts/robocache.py`): alle inhaltsadressierten Einträge (UVs, AO,
  Bake-Ebenen, statische Texturen, Skins, ImageMagick-Texturen, Renders, QA, Clip-Daten und neu die Exporte FBX/
  Formen/Viewer-Daten) in `~/Library/Caches/RobotGen/cache` (`ROBOGEN_CACHE`), geteilt von Haupt-Checkout, Worktrees
  und Nachtläufen. In `<out>/cache` bleiben nur `outputs.json` (jetzt mit Pfaden relativ zur Ausgabe), das Zeitband
  und die Skin-Arbeitskopien. Schreiben atomar (Temp-Datei, Umbenennen), Kopien als APFS-Klone; kein Schritt löscht
  mehr ältere Einträge (`unwrap()` behielt nur die neueste UV-Datei, das Backen nur den neuesten Bake). Aufräumen nur
  nach Größe am Ende eines Laufs: `ROBOGEN_CACHE_LIMIT_GB` (Standard 6), am längsten unbenutzte zuerst, nichts aus den
  letzten 2 Stunden; das 7-Tage-Löschen von QA und Renders entfällt. Bake-Schlüssel ohne Ordner der Quelltexturen
  (sonst traf ein anderer Checkout nie). `--no-cache` wie bisher.
* **Migration:** Der erste Lauf klont die Caches des Haupt-Checkouts (`output`, `_quick`, `_standard`) samt
  Quelltexturen, statischen Texturen, Standard-Skin und Exporten bytegleich; den alten, pfadhaltigen Bake-Schlüssel
  erkennt das Backen wieder (`legacy_sources.json`, `bake/alias_*`). Ein neues Backen wäre nicht bytegleich (Cycles,
  Normal-Map) und hätte alle Vorschauen ungültig gemacht. Die eigene Ausgabe des Haupt-Checkouts wird verschoben.
* **B – Prüfplan aus dem Diff** (`scripts/testplan.py`): Datei → Bereich → Weg als Tabelle (`RULES`, `AREAS`): Doku
  nichts, Daten der schnelle Weg, Modell/Rig/Clips/UV/Bake/Export/Cache der volle Ablauf mit zweitem `quick` und
  Cache-Treffer-Prüfung, Viewer Build und Tests, Export/Clips/Texturen/Shader das Unity-Testbett; mit Zeitschätzung.
  `--run` führt den Worktree-Teil aus (sichert und vergleicht `qa`/`clips`/`type_bounds`, prüft die Treffer),
  `--after-merge` den Teil im Haupt-Checkout, `--path data` für reine Werte in Code-Tabellen. Erster Schritt im
  Prüfablauf (`AGENTS.md`).
* **C – ruhige Maschine** (`scripts/quiet.py`): `full` und `scripts/check_output.sh` prüfen vor dem Start, ob Unity,
  Blender oder der Generator anderer Agenten-Sitzungen laufen oder die Last über 0,3 je Kern liegt, während deren
  Befehle die CPU nutzen; dann starten sie sich über `pause_sessions.py` aus Cube Escape neu (nur so lange wie der
  Befehl, der Bericht nennt es: `robot_report.json` › `quiet`). Pausiert schon eine andere Sitzung, laufen sie normal
  (Sperre `~/Library/Caches/RobotGen/quiet.lock`). `ROBOGEN_NO_PAUSE=1` schaltet ab, `ROBOGEN_PAUSE_TOOL`,
  `ROBOGEN_QUIET_LOAD` stellen ein; fehlt das Werkzeug, laufen sie mit Warnung normal. Unity-Editor und Blender des
  Nutzers bleiben unberührt (das Werkzeug hält nur Befehle unter anderen Agenten an).
* **D – Aufträge an andere Sitzungen:** Vorlage in `AGENTS.md` (Art der Änderung, Prüfweg aus `testplan.py`,
  erwartete Abweichungen im Bericht, Release ja/nein, was zu melden ist).
* **Tests:** `python3 -m unittest discover -s tests` – 46 Tests ohne Blender: zweiter Checkout ganz warm, gleichzeitige
  Schreiber (nie eine halbe Datei, ein Ordner-Eintrag genau einmal), Aufräum-Reihenfolge und Schonfrist, relative
  `outputs.json`, Migration (klonen/verschieben, Standard-Skin, Exporte, Haupt-Checkout per `git worktree`),
  Prüfplan-Beispiele, Pausen-Entscheidung (fremde vs. eigene Last, nie zwei Pausen). Subagent `rg-test-runner` darf
  `testplan.py`, die Werkzeug-Tests und `quiet.py status`.
* **Gemessen** (Apple M5, andere Sitzungen mit Blender aktiv): alter Generator im frischen Worktree, kalt: `quick`
  54 s, `standard` 328 s, `full` 628 s (1 649 Bilder gerendert). Neu, erster Volllauf in einem frischen Worktree:
  87 s (pausiert) bzw. 125 s (unter Last, ohne Pause), 1 649 von 1 649 Bildern aus dem Cache, keine FBX neu
  geschrieben. Voller Prüfweg im Worktree mit warmem Cache (`testplan.py --run`: Werkzeug-Tests, `quick` ×2,
  `standard`, `full`, `check_output.sh`): 3:01 min; nach dem Merge im Haupt-Checkout `full` 85 s und
  `check_output.sh` 52 s (beide mit Pause der anderen Sitzungen). **Abnahme:** ein neuer Worktree nach dem Merge,
  erste Läufe alle warm – `quick` 25 s, `standard` 94 s (425/425 Bilder), `full` 95 s (1 649/1 649 Bilder), keine
  FBX neu geschrieben, Berichte identisch.
* Bekannt (nicht neu): Die Voxel-Form liest die gebackenen Karten; zwei kalte Bakes geben leicht andere Voxel-Meshes
  (Bericht `forms`, QA `Form_Voxel` PushStart/MudWalk 0,048 statt 0,05 m/s Standgeschwindigkeit). Der geteilte Cache
  backt nur noch einmal je Modelländerung.

## 1.20.1 „Powdercoat“ – 2026-10-15

Nur Arbeitsregel, Ausgabe unverändert: **Schneller Weg für reine Datenänderungen** in `AGENTS.md` › Prüfablauf. Anlass:
1.20.0 änderte nur zwei Werte und ein neues Finish, die Prüfung dauerte trotzdem rund 55 Minuten – kalte Läufe im neuen
Worktree, zwei Vollläufe im Haupt-Checkout, Last durch andere Sitzungen. Ein warmer Volllauf braucht etwa 100 s
(standard 70 s, quick 12 s). Ziel jetzt: unter 15 Minuten vom fertigen Diff bis zum Release.

## 1.20.0 „Powdercoat“ – 2026-10-14

**Oberfläche „Standard“ ist matt – für alle Skins.** Cube Escape 0.58 hat gemessen: Die Station ist dunkel und matt
(Metallic 0, Smoothness 0,4–0,5); die alte Standard-Panzerung (im Mittel Metallic 0,42, Smoothness 0,68 aus
`T_Robot_U_MetalWear`) spiegelte die dunkle Umgebung, Classic wirkte grau. Der Glanz bleibt als neue Oberfläche wählbar.

| Oberfläche (`robot_config.json` › `finishes`) | bis 1.19 (Rauheit ×, Metall +) | ab 1.20 | Panzerung wirksam (Metallic / Smoothness) |
|---|---|---|---|
| Standard | 1,0 / 0,0 | **1,9 / −0,25** (die bisherigen Matt-Werte) | 0,42 / 0,68 → **0,17 / 0,40** |
| Matt | 1,9 / −0,25 | **2,2 / −0,35** | 0,17 / 0,40 → **0,07 / 0,30** |
| Satin, Glanz, Candy, Chrom | unverändert | unverändert | unverändert |
| **Lack** (neu, Position 7 am Ende) | – | 1,0 / 0,0 (der bisherige Standard) | 0,42 / 0,68 |

* **Wo:** nur die Oberflächen-Werte (`FINISH_PARAMS`), nicht die gepackten Masken – Metall und Glätte dort tragen alle
  Oberflächen, eine Änderung hätte Glanz, Candy und Chrom mitgezogen. Generator-Vorschauen, Viewer und Unity-Testbett
  lesen dieselben Werte; Standardwerte im Viewer (`Finish.params`), im Shader `RobotBody` (`_Finish`) und im gebackenen
  Blender-Material (`FinishRough`/`FinishMetal`) ziehen mit.
* **Matt eine Stufe matter:** sonst wären Standard und Matt gleich und die 9 Skins, die Matt bewusst wählen (Shadow,
  Tank, Void, Pixel, Origami, Bone …), stächen nicht mehr heraus; die 42 Skins mit Standard sind jetzt matt. Ein im
  Spiel erzwungenes „Matt“ ist nicht mehr nötig.
* **Looks:** IDs unverändert, kein neues Look-Format (wie neue Varianten oder Skins): gespeicherte Looks mit „Standard“
  zeigen jetzt die matte Oberfläche – so gewollt. Ein Werkstoff bringt bei „Standard“ weiter seine eigene Oberfläche.
  Ältere Leser kennen „Lack“ nicht und fallen auf Standard zurück. Unity-Testbett und Viewer melden eine unbekannte
  Oberfläche jetzt als Warnung und nehmen Standard (vorher: still Standard bzw. die alten Glanzwerte).
* **Prüfungen:** `verify_powdercoat` in `verify_robot.py` (IDs in Reihenfolge, Werte, Schema, wirksame Werte der
  Panzerung aus den Masken: Standard 0,171 / 0,399, Lack 0,421 / 0,683); Viewer-Tests „finish Lack kept/without
  warning“, „unknown finish replaced/warning“, „Lack differs from Standard“; EditMode-Tests `LackIsAKnownFinish`,
  `UnknownFinishFallsBackToStandard`, `StandardIsMattAndLackKeepsTheOldGloss`. Neue Fixtures
  `viewer/tests/look_v6_finish_lack.json`, `look_v6_unknown_finish.json` (alte unverändert).
* Modell, Rig, Clips, QA, Clip-Daten, Typ-Maße, LODs und Kapseln unverändert (Bericht `qa`/`clips`/`type_bounds`/
  `triangles`/`lods`/`hit_capsules` identisch zu 1.19).
* **Render-Cache (Fehler behoben):** Der Szenen-Abdruck nahm nur unverknüpfte Sockel auf; die Werte der
  Value-Knoten `FinishRough`/`FinishMetal` (und `TintColor`, `EyeColor` …) stehen im verknüpften Ausgang. Ein warmer
  Cache lieferte deshalb nach der Oberflächen-Änderung alle Vorschauen im alten Glanz. Die Ausgänge von Value- und
  RGB-Knoten zählen jetzt mit (`_tree_digest`, nur diese: andere Ausgänge tragen Reste, die je Prozess schwanken);
  der erste Lauf danach rendert alle Vorschauen neu. Damit die Spezialfarben eines vorigen Skins im Worker keine
  Fehltreffer erzeugen, setzt `set_special` sie ohne Spezialmaterial auf den Ausgangswert zurück (Bild unverändert);
  der zweite Standardlauf trifft wieder alle 425 Bilder.
* Ergebnis Volllauf: `verify_robot.py` 323/323, Viewer 50 PASS, Unity-Testbett 29/29 Prüfungen und 16/16 EditMode-Tests;
  Viewer: Classic/Hazard/Medic mit „Lack“ weichen höchstens 21 von 160 000 Pixeln vom alten Standard ab.

## 1.19.0 „Longstride“ – 2026-10-14

**Clips im Spieltempo für Cube Escape 0.58** (Gehen 5 m/s, Sprint 8 m/s, Kriechen unter der 0,65-m-Kapsel 2,4 m/s,
Wasser 4 m/s, Tragen der Magnetkiste 5 m/s, Schulterkamera mit Seit- und Rückwärtslaufen). Zwölf neue Clips in der
neuen Bau-Phase `Phase9` („Spieltempo & Aktionen“), jeweils am Ende ihrer Kategorie angehängt. Alle bestehenden Clips,
Clip-Daten, QA-Ergebnisse und Typ-Maße sind unverändert (73 Clips aus dem Clip-Cache, Berichte `qa`/`clips`/
`type_bounds` für alle alten Einträge identisch).

| Clip | Kategorie | Frames (30 fps) | Loop | `naturalSpeed` | Schritte |
|---|---|---|---|---|---|
| Jog | Move | 0–16 | ja | 5,0 m/s | 0 L, 8 R |
| SprintFull | Move | 0–20 (zwei Doppelschritte) | ja | 8,0 m/s | 0 L, 5 R, 10 L, 15 R |
| StrafeLeft / StrafeRight | Move | 0–16 | ja | 5,0 m/s | 0 L, 8 R |
| RunBack | Move | 0–16 | ja | 5,0 m/s | 0 L, 8 R |
| Wade | Move | 0–18 | ja | 4,0 m/s | 0 L, 9 R |
| CrawlFast | Crouch | 0–14 | ja | 2,4 m/s (Hände) | 0 Hand_L, 7 Hand_R; Höhe 0,55–0,58 m |
| CarryIdle / CarryWalk | Interact | 0–60 / 0–16 | ja | – / 5,0 m/s | – / 0 L, 8 R |
| Crank | Interact | 0–45 (1,5 s) | ja | – | Ereignis `Grab` bei 0 |
| Knockback | Damage | 0–26 (0,87 s) | nein | – | `Impact` 1, `Land` 8 |
| LandRoll | Air | 0–27 (0,9 s) | nein | – | `Impact` 0 (Aufsetzen), `Land` 19 |

* **Gesetzte Füße rutschen nicht:** Ein Bodenkontakt läuft mit konstantem Neigungs- und Gierwinkel genau mit
  `naturalSpeed` gegen die Laufrichtung (Schrittlänge = Tempo × gesetzte Frames / 30) und überspannt mindestens zwei
  Frame-Schritte, damit `measure_clips` ihn misst. Bei 8 m/s reicht ein Doppelschritt dafür nicht – SprintFull hat zwei.
  Bei CrawlFast rollt die Faust über die Knöchel: das Knöchelziel läuft 2,4 % schneller, gemessen 2,40 m/s.
* **Strafe:** Hüfte 70° in die Laufrichtung gedreht, Beine auf zwei Spuren (kreuzen nie), Brust und Kopf zurück zur
  Kamera. **RunBack** auf den Fußballen. **Wade** mit hohen Knien und seitlich balancierenden Armen.
* **Kiste und Ventilrad:** CarryIdle/CarryWalk halten die 0,8-m-Kiste an festen Punkten des Charakterraums (sie wippt
  nicht mit), Daten in `clips.<Name>.prop` = `{"type": "crate", "size": 0.8, "forward": 0.30, "bottom": 0.36}` und
  `hands`; Crank dreht ein Rad `{"type": "wheel", "up": 0.72, "forward": 0.40, "radius": 0.20}` wie ein Lenkrad um 36°
  und greift nach. Der Viewer zeigt beide als Hilfsobjekt.
* **LandRoll** rollt über Kopf und Rücken: der Körper wird je Frame so weit angehoben, dass die Hülle aller Teile
  (alle Slot-Varianten, die Formen des Standard-Roboters) den Boden gerade berührt (`lifted()`, `roll_hull()`). Die
  Aufsätze an Kopf- und Rückensockel und der hohe TV-Kopf würden den Standard-Roboter bis 0,3 m über den Boden heben;
  sie sind aus der Hülle genommen und dürfen beim Rollen den Boden berühren – benannte QA-Ausnahme
  `ACCEPTED_GROUND` (nur LandRoll, nur diese Knochengruppen). Der Standard-Roboter schwebt dabei höchstens 4 cm.
* QA aller 24 Typen gegen alle neuen Clips ohne Befund. Neue Prüfungen in `verify_robot.py` (`verify_longstride`):
  Tempi ±1 %, Schritte, Loops, Root Motion 0, CrawlFast unter 0,60 m, Ereignisse, Hilfsobjekte, Kategorien am Ende,
  **jeder Frame-Schritt eines gesetzten Kontakts** innerhalb 3 % von `naturalSpeed` und in Gegenrichtung,
  stehende Füße in CarryIdle, Crank, Knockback, LandRoll unter 2 mm je Frame, Kiste frei vom Körper und Hände an ihren
  Seiten.

## 1.18.2 – 2026-10-14

* Fahrplan und README: Tempo, Kapselbreite und Augenhöhe des Spieler-Roboters entschieden (Spieltempo bleibt,
  Animation höchstens 1,4-fach bis zu längeren Run-/Sprint-Schritten; Kapselradius 0,30 m; Ego-Kamera auf `eyeHeight`).
  Generator und Ausgabe unverändert.

## 1.18.1 – 2026-10-14

* Skill `rg-unity-integration`: Werte im Spiel als Materialinstanz je Roboter statt `MaterialPropertyBlock`
  (Entscheidung von Cube Escape 0.57 „Undercoat“, Shader-Familie `CubeEscape/Surface`, Rezept ROBOT); `RobotBody` im
  Testbett bleibt die Bildreferenz. Generator und Ausgabe unverändert.

## 1.18.0 „Ground Crew“ – 2026-10-14

**Agenten-Werkzeuge ohne Modelländerung** (gemeinsamer Auftrag mit Cube Escape 0.56 „Pit Crew“ und Cubegen 0.23.0
„Wingman“). Generator, Ausgaben und QA sind unverändert; nur die Versionsnummer steht in `robot_config.json` und den
Vorschaubildern.

1. **Subagents** in `.claude/agents/`: `rg-test-runner` (Teststufe, Berichtsvergleich `qa`/`clips`/`type_bounds`,
   Zeitband, Cache-Treffer, Viewer- und Unity-Prüfungen – berichtet nur) und `rg-doc-keeper` (Changelog, README,
   `docs/TECHNIK.md`, AGENTS.md und Skills auf Endstand, nur Markdown; Änderungen am Fahrplan meldet er für das Claude
   Doc). Was jeder Agent mit seinen Werkzeugen darf, steht in `.claude/agent-guard.json`.
2. **Gemeinsames Plugin `ce-shared`** (privates Repo `ffunkfabian-hash/ce-agent-tools`, eingetragen in
   `.claude/settings.json`): Force-Push und nacktes `git stash` blockiert jetzt das Plugin für alle drei Repos (die
   eigenen Hook-Dateien entfallen), dazu neu Teleport aus einer Sitzung, Unity auf Cube-Escape-Checkouts und Unity-CLI
   ohne `--project-path`, Warnung bei GitHub-Workflows. Die alte Force-Push-Regel traf `-f` irgendwo nach „push“.
3. **Fahrplan:** die zwei in 1.9.2 geprüften Neural-Engine-Vorschläge (Bildvergleich per Vision, Vorlagen per
   Bildgenerator) sind im Claude Doc und in `docs/FAHRPLAN.md` als verworfen abgehakt.

Geprüft: `quick` und `standard` mit dem UV-Cache von 1.17 – `qa`, `clips`, `type_bounds` identisch. Mit kaltem Cache
wich nur die Voxel-Form in Lauf-Clips minimal ab (Standgeschwindigkeit bis 0,002 m/s): neu gepackte UVs (Blenders
Insel-Packen ist nicht deterministisch, Cache-Regeln) ändern die abgetasteten Texel – kein Code-Unterschied.

## 1.17.0 „Slimline“ – 2026-10-13

**Optimierung ohne Modelländerung** (Vorschlag vom 13.10., Nutzer: „1–5 ausführen“). QA, Clip-Daten und Typ-Maße sind
identisch mit 1.16; die Voxel-Form hat weniger Dreiecke bei gleichem Bild.

1. **Schnelle Stufe unter 10–12 s:** Haupt-FBX samt LODs, Modular-FBX, Form-FBX und Viewer-Daten werden nur noch
   geschrieben, wenn sich ihr Inhalt geändert hat (`cache/outputs.json`, Abdruck aus Meshes, Rig, Clips und
   Export-Code), Clip-Daten je Clip aus `cache/clips`. `quick` mit warmem Cache 22 s → 9–12 s, `full` mit warmem
   Cache 90–115 s → 54–59 s (alle 1 505 Bilder, 1 919 QA-Ergebnisse und alle Ausgaben aus dem Cache).
2. **Generator in Module aufgeteilt:** `scripts/generate_robot.py` (Einstieg, `main()`) und 16 Module in
   `scripts/robogen/` (`core` … `config`). Ohne Verhaltensänderung: nach dem Umbau trafen alle Caches (UV, Bake,
   73 Clips, 73 QA-Ergebnisse, alle Ausgaben, Render-Cache), die am Quelltext der Funktionen hängen.
3. **Schlanke Ausgaben:** In `Textures/` liegt nur noch der Standard-Skin; die 75 übrigen gemischten Skin-Karten für
   die Vorschauen liegen in `cache/skin_textures` und werden nur bei geänderter Palette neu geschrieben
   (`Textures/` im Volllauf 334 → 47 MB). Viewer-Daten: gleiche Ecken je Teil zusammengefasst, `vertices.bin`
   rund dreimal kleiner.
4. **Voxel-Form mit weniger Dreiecken:** gleich aussehende Nachbarseiten (Farbe, Masken und Werkstoff am Texel
   gleich) werden zu Rechtecken verschmolzen – Standard-Look 17 136 → 11 348 Dreiecke (−34 %, Volllauf), im Bild kein Unterschied.
5. **Zeitband statt festem Budget:** Laufzeiten werden gegen den Median ruhiger Läufe derselben Stufe und Cache-Lage
   bewertet (`cache/timings.json`, `timings.band` im Bericht); Toleranz nach Last zu Beginn: ruhig 30 %, leicht
   × 2,6, schwer = nicht bewertet. Das Stufenbudget bleibt als Ziel.

* Behoben (beim Cachen gefunden): Die Facetten-Form war zwischen Prozessen verschieden (Decimate hängt an der
  Element-Reihenfolge; jetzt kanonische Kopie vorher). Der Innen-Test der Voxel-Form nutzt drei leicht schräge
  Strahlen mit Mehrheit statt eines achsparallelen, der auf dem Zellraster genau Kanten streifen kann.
* Facetten-Form liegt jetzt innerhalb der Grundform: Decimate setzt zusammengefasste Punkte an die Quadrik-
  Optimalstelle, die einige Millimeter außerhalb liegen kann; mit der kanonischen Reihenfolge berührte der Rumpf in
  CrouchDeep den Oberschenkel. Solche Punkte werden bis auf 2 mm an die Oberfläche ihres Teils zurückgezogen
  (`inside_surface`, `FACET_BULGE`; ganz auf die Fläche gezogen, löste sich in ClimbUp die Hand vom Unterarm).
  Die Bezugsfläche ist kanonisch geordnet, damit das Ergebnis in jedem Prozess gleich ist.
* Im Volllauf gefunden: Die kanonische Kopie trug keine gültigen Element-Indizes – die Inselsuche fasste ganze
  Objekte zusammen (beide Füße als `Foot_R`), die QA meldete Durchdringungen in 59 Clips. Behoben, QA aller Typen und
  Formen wieder ohne Befund. Neue Prüfung in `verify_robot.py`: jede Knochengruppe einer Mesh-Form liegt innerhalb
  derselben Gruppe ihres Grundteils (+ eine Voxel-Zelle) – der Fehler lag 23 cm daneben.
* Neu: `ROBOT_OUTPUT_DIGEST_DUMP=<Datei>` schreibt die Ausgaben-Abdrücke je Objekt (Fehlersuche).
* Code-Review vor dem Release: Der Schlüssel der Clip-Daten enthielt die Eigenschaften des Clips selbst nicht
  (Länge, Loop, `gait`, Requisit) und der Stempel der Skin-Karten nicht alle Funktionen und Konstanten der Mischung –
  beides hätte nach einer Änderung veraltete Daten liegen lassen. `verify_robot.py` verlangt die Vorschau-Skins nur
  noch, wo der Cache mitkommt (eine ohne Cache kopierte Ausgabe ist gültig). Modular- und Form-FBX hängen nicht mehr
  an Clips und LOD-Einstellung.

## 1.16.0 „Crawlspace“ – 2026-10-13

**Kriechen unter der Duck-Kapsel** (Entscheidung 13.10.: die Kapsel bleibt 0,65 m): zwei neue Clips in Bauchlage.

* **CrawlIdle** (liegen und atmen) und **Crawl** (robben: Unterarme ziehen im Wechsel per Arm-IK, Ellbogen außen
  neben dem großen Kopf, Fußspitzen schieben), Oberkante ≈ 0,57 m, Grundtempo ≈ 0,30 m/s. Neues Clip-Feld `gait`
  („feet“ oder „hands“): bei Crawl kommen Bodenkontakt, Schritt-Frames (`footsteps` = Handzüge `Hand_L`/`Hand_R`, Kontaktschwelle 3,5 cm) und Grundtempo von
  den aufliegenden Händen; gehaltene Gegenstände sind ausgeblendet.
* QA aller 24 Typen und der Formen gegen beide Clips ohne Befund (Jetpack, Flügel und Linsenauge brauchten einen
  flacheren Kopf und weiter außen liegende Hände).

**Animationen nach Inhalt sortiert** (Hinweis des Nutzers: die Kategorien wirkten unsortiert – sie folgten der
Entstehung in Phasen): zehn Kategorien `CLIP_CATEGORIES` (Gehen & Laufen, Springen & Fallen, Ducken & Kriechen,
Klettern & Hängen, Interaktion & Werkzeuge, Treffer & Gefahren, Warten & Charakter, Gesten, Sitzen & Schlafen, Tänze),
jeder Clip genau einmal (der Generator bricht sonst ab), Feld `category` in den Clip-Daten, `clipCategories` in
`robot_config.json`; Viewer und `ANIMATIONS.md` nach Kategorien. Die Bau-Phasen bleiben für Kontaktbögen und QA.

* Viewer: „Oberfläche“ als Auswahlmenü (sechs Segmente ragten rechts aus der Spalte).
* `robot_config.json` › `eyeHeight` (1,161 m): Augenhöhe für die Ego-Kamera im Spiel (Wunsch aus Cube Escape 0.52,
  Kapsel dort jetzt 1,40 m).
* Behoben: `--groups` brach ab, wenn ein Typ-Emote herausgefiltert war.
* Code-Review vor dem Release: Kontakte und Schritte von Crawl kamen zuerst von den schleifenden Zehen (jetzt die
  Hände, über `gait` am Clip statt einer eigenen Namensliste), Prüfungen der Kriech-Clips überspringen Läufe mit
  `--groups`.

## 1.15.0 „Pack Rat“ – 2026-10-13

**Textur-Atlas dichter gepackt:** Smart UV Project ließ große Lücken zwischen seinen Tausenden kleinen Inseln – nur
17,6 % des Atlas waren belegt. Danach packt jetzt `pack_islands` (konkave Form, freie Drehung, Abstand 0,002 = 4 px
bei 2048) die Inseln neu: rund 51 % belegt, also etwa 1,7-mal so viele Texel je Zentimeter in jeder Karte und Maske.

* Gemessen: der Inselabstand bestimmt die Belegung, nicht der Abwicklungswinkel (60° → 80°: 41 → 43 %; Abstand
  0,003 → 0,002: 41 → 51 %; 0,0015 brächte nur noch 4 Punkte bei doppelter Packzeit).
* Der AO-Bake mit mehreren Varianten-Durchgängen nutzt als Rand höchstens den halben Inselabstand (vorher fest 2/4 px,
  die in den kleinen Teststufen schon in Nachbarinseln liefen).
* UV- und Bake-Cache neu (Schema `1.15-a`); Packen ≈ 2–5 s, danach aus dem Cache.
* Entscheidungen zur Spielkapsel festgehalten (Fahrplan): Stehen 1,40 m (Änderung in Cube Escape), Ducken bleibt
  0,65 m – der Generator bekommt Kriech-Clips (1.16).

## 1.14.1 „Reshape“ – 2026-10-13

Viewer: In einem bildschirmbreiten Fenster wurde die Seitenleiste in ihrer Wunschbreite (≈ 525 pt) statt in der
Spaltenbreite (440 pt) gezeichnet, über den Fensterrand geschoben und beidseitig abgeschnitten; über ihr fehlte die
Titelleiste, weil die Scroll-Ansicht unter die Titelleiste lief (macOS 26+).

* Der Inhalt nimmt jetzt genau die Breite der Spalte, die Kopfzeile („Cube Escape Roboter“, Zufall) steht fest über
  der Scroll-Ansicht, die Titelleiste geht über das ganze Fenster.
* „Oberfläche“: Beschriftung über den sechs Segmenten (daneben wurde sie senkrecht gequetscht).
* Neue Layout-Prüfung `--sidebar-check out.png [--type …] [--skin …]` (Mindestbreite des Inhalts gegen die Spalte,
  in `viewer/test_viewer.sh`), Startoption `--window 1710x1000` für Layout-Prüfungen in großen Fenstern.

## 1.14.0 „Reshape“ – 2026-10-13

**Proportionen wie auf den Konzeptbildern:** eine Proportions-Stufe nach dem Bauen formt die Teile je Knochen um
(`PROPORTIONS`, Ausnahmen je Variante in `SHAPE_VARIANTS`). Kopf breiter (× 1,14) und höher (× 1,24), Arme, Hände und
Beine kräftiger, Rumpf und Becken breiter; Fugen und Schrauben auf Beinen, Hüfte und Brust. Gelenke, Clips, IK-Ziele
und Bodenkontakte bleiben unverändert. Der Classic ist jetzt 1,38 m hoch (vorher 1,30 m).

* Posen angepasst, damit nichts in den größeren Kopf greift: Think, Wave, DanceRobot, IdleScratch, EdgeBalance,
  HatTip, Jump, Crouch, CrouchWalk, Sleep. Schulterpanzer und runde Füße behalten ihr Maß, der Kastenkopf wird nur
  höher, der Daumen nur wenig dicker.
* Gefunden: Bei nach vorn gestrecktem Arm wirkt der Abspreiz-Kanal (XYZ-Euler) als Drehung um die Armachse – nach
  außen führt dann der dritte Kanal.

**Körperformen** (`robot_config.json` › `forms`, Skin- und Look-Feld `form`, Look-Format 6):

* *Facettiert* (Origami, Geo): eigene Teil-Objekte, Decimate „Collapse“ auf 16 %, flache Facetten.
* *Voxel* (Pixel): Würfel von 3 cm im Weltraster; eine Zelle zählt, wenn ihr Mittelpunkt eine halbe Zelle tief im
  Teil liegt (kein Würfel ragt über die Grundform). Jede Würfelseite nimmt UV und Material des nächsten
  Originalpunkts. Gelenkkugeln, Finger, dünne Teile, Visier und Bildschirme bleiben glatt.
* *Durchbrochen* (Bone): Löcher in der Panzerung, Raster im UV-Raum (Abstand 4,5 cm aus der Texeldichte), gleiche
  Formel in Vorschau, Viewer (`discard`) und Unity-Testbett (`clip`).
* *Offene Kabel* (Wire): Kabelstränge als Gruppe `Form_Wired`, je Knochen starr.
* Mesh-Formen haben dasselbe Rig, dieselben Materialien und den Textur-Atlas: jeder Skin passt auf jede Form.
  Export `Forms/CubeEscape_Robot_<Form>.fbx` (+ modular), Viewer-Daten mit Formkennung je Geometrie-Stück.
* QA: Facettiert, Voxel und Kabel laufen wie Robotertypen über alle Clips (`Form_*`), alle ohne Befund.
* 5 neue Skins: Pixel ★, Origami ★, Geo, Bone, Wire (76 insgesamt).
* Viewer: Abschnitt „Form“, Schalter `--form`, Tests für Migration v5 → v6, unbekannte Form und sichtbare Form.
  Unity-Testbett: Form-FBX mit den Hauptknochen, Form je Skin gelesen, Lochung im Shader, 2 neue EditMode-Tests.
* Code-Review vor dem Release: das Unity-Testbett legte je Checkout eine neue URP-Datei an und schrieb deren GUID in
  die Projekteinstellungen – die URP-Datei ist jetzt versioniert; Unity-Body-Shader beidseitig (Innenseiten in den
  Löchern), die URP-Beschreibung verlangt den Loch-Clip auch in Schatten- und Tiefenpässen; Voxel ohne toten
  Verschiebe-Code (Würfel liegen ohnehin innen), Viewer-Schlüssel als Struktur, Prüfungen der Formen-QA an der
  Typenliste statt an der Größe des Berichts.
* Offen notiert: der Textur-Atlas ist nur zu ≈ 19 % belegt (Smart UV Project ohne Skalierung).
* Doku: README, AGENTS, TECHNIK.md › 15, FAHRPLAN.

## 1.13.3 „Spectral“ – 2026-10-13

**Render-Cache:** jedes Vorschaubild (Ansichten, Skins, Karten, Typen, Ausdrücke, Icons, Gegenüberstellung,
Kontaktbögen, GIFs) wird unter einem Abdruck der Szene beim Rendern gespeichert – sichtbare Objekte mit Geometrie,
Matrix und Pose, Materialien als Knotengraph mit Werten, Bilddateien nach Inhalt, Kamera, Lichter, Welt, Compositor,
Render-Einstellungen. Ohne sichtbare Änderung kommen alle 405 Bilder des Standardlaufs aus dem Cache (Standardlauf
44 s); eine geänderte Skin-Farbe rendert genau deren drei Bilder neu.

* Gefunden und behoben beim Bau: gleichnamige eingebettete Materialbäume (falsche Treffer), veraltete Weltmatrizen
  bewegter Lichter/Kameras, unwirksame Restwerte (Brennweite einer Ortho-Kamera, Bildnummer, Thread-Zahl) im
  Schlüssel, Posen über Aktion + Bild statt gerundeter Matrizen. Volllauf mit warmem Cache 257 → 108–115 s (bei
  Fremdlast 21), alle 1 461 Bilder aus dem Cache.
* Code-Review vor dem Release: Worker beachten `--no-cache`, Geometrie wird bei jedem Abdruck neu gelesen (keine
  veralteten Netze), Modifikatoren mit allen Einstellungen, Bilder ohne Datei nach Pixelinhalt, Schärfentiefe der
  Kamera, flüchtige Editor-Werte (Auswahl, aufgeklappt, Pin) nicht mehr im Schlüssel.
* Der Cache deckte eine echte Unschärfe auf: das Icon der Antenne zeigte die Federpose des Clips, den der Worker
  zuvor bearbeitet hatte. Icons entstehen jetzt in der Ruhepose und sind damit reproduzierbar.
* Fehlersuche: `ROBOT_KEEP_WORKER_LOGS=1`, `ROBOT_RENDER_DIGEST_DUMP=<Dateiname>`; Statistik im Bericht
  (`timings.render_cached`, `timings.rendered`), Aufräumen nach 7 Tagen wie beim QA-Cache.
* Doku: README, AGENTS, TECHNIK.md › 12.

## 1.13.2 „Spectral“ – 2026-10-13

Befunde aus dem nachgeholten Code-Review von 1.13.1 (das Review vor dem Release war ausgelassen worden):

* Viewer-Tests: Round-Trip-Pfade mit Leerzeichen (z. B. unter „Cube Escape“) werden nicht mehr zerlegt.
* Viewer-Build: der Stempel enthält `build.sh` selbst – geänderte Compiler-Schalter kompilieren neu.
* QA-Cache: genutzte Ergebnisse werden aufgefrischt, seit 7 Tagen ungenutzte gelöscht (jede Modelländerung legte
  bisher dauerhaft ~1 700 neue Dateien an).
* `check_output.sh`: stirbt ein Prüfprozess ohne Status, meldet die Zusammenfassung FAIL statt abzubrechen.

## 1.13.1 „Spectral“ – 2026-10-13

Testzeit: Standardlauf 121 → 51 s, Prüfungen danach parallel (≈ 55 → 35 s). Modell, QA-Werte und Ausgaben unverändert.

* **QA-Cache** (`cache/qa`): Ergebnisse je Typ und Clip werden wiederverwendet, solange Mesh, Rig, Clip-Schlüssel und
  QA-Code gleich sind (Quelltext der QA-Funktionen im Schlüssel); `--no-cache` prüft alles neu. Mesh-Hash über eine
  kanonische Topologie (die Schleifen-Startpunkte der Flächen unterscheiden sich zwischen Blender-Prozessen).
* **Typ-FBX und LODs** als parallele Arbeitspakete der Render-Worker statt nacheinander im Hauptprozess.
* **Bögen** (Icons, Skin-Bogen, Gegenüberstellung, Kontaktbögen) in einem gemeinsamen Thread-Pool.
* **`scripts/check_output.sh`:** `verify_robot.py`, Viewer-Build und -Tests und Unity-Testpaket gleichzeitig.
* **Viewer:** kompiliert nur bei geänderten Quellen (beide Architekturen parallel, 16 → 9 s kalt, 0,2 s ohne
  Änderung), App-Icon nur bei geändertem Kopf-Bild; Round-Trip-Tests mit sechs Prozessen (Tests 14,5 → 8,4 s).
* Doku: README, AGENTS (Prüfablauf), Skill `rg-unity-testbed`, TECHNIK.md › 12.

## 1.13.0 „Spectral“ – 2026-10-13

Spezialmaterialien, 71 Skins (9 neue: Glass, Ghost, Aurora, Rainbow – Startset –, Prism, Ice, Void, Nebula,
Scribble), Look-Format 5 – dazu die offenen Punkte vor dem Spiel-Roboter: Detailstufen, Treffer-Kapseln,
Servo-Ereignisse, Masken für Unity, Emote-QA im Schnelllauf, ein schnelleres Unity-Testpaket und Skills.

* **Spezialmaterialien** (`specials`, Skin-Feld `special`): Glas/Kristall, Hologramm, Glitch, Schillern, Skizze,
  Regenbogen und Galaxie (animiert) – Laufzeit-Effekte über dem Skin mit zwei Farben, Stärke und Tempo; gleiche
  Formeln in der Blender-Vorschau (Skin-Karten) und im Viewer-Shader; Visier und Lichter bleiben. Je Material eine
  Beschreibung fürs spätere URP-Material (`urp`). Skin-Karten nennen „Spezial: …“.
* **Look-Format 5** (`special`) mit Migration aus 1–4 in Viewer und Unity; Fixtures `look_v4.json`,
  `look_v5_unknown_special.json`; fehlende Felder des Spezialmaterials bekommen ihren Standard.
* **Viewer:** Abschnitt „Spezialmaterial“ (Auswahl, Beschreibung, Farben, Stärke, Tempo); Glas/Hologramm durchsichtig.
* **Detailstufen:** `LOD/…_LOD1/2.fbx` für den Roboter und jeden Typ (≈ 6 000 / 2 500 Dreiecke, gleiches Rig).
* **Treffer-Kapseln** (`hitCapsules`): 15 Kapseln im Charakterraum, je 95–100 % Abdeckung ihres Bauteils; im
  Unity-Testpaket über die Bindposes an die Knochen gehängt (Run-Frame 10: 97 % der Vertices innen).
* **Servo-Ereignisse** (`clips.<Name>.servo`): Bewegungsansätze von Kopf, Rumpf, Armen und Beinen mit Stärke und Dauer.
* **Masken für Unity gepackt** (`Textures/Unity`, `unityTextures`): 10 statt 14 Texturen, ≤ 1024², Lackzonen als
  ID-Kanal – der Testbett-Shader nutzt sie.
* **Schnelllauf prüft Typ-Emotes** mit ihrem Typ (Gentleman, King) statt sie auszulassen.
* **Unity-Testpaket in einem Unity-Start:** die EditMode-Tests laufen im Pipeline-Prozess (`TestbedTestRun`), der
  Import parallel; ein Lauf ohne Änderungen 25–30 → 12 s. Neue Prüfungen: gepackte Masken, LOD, Kapseln.
* **Skills** `rg-unity-testbed` und `rg-unity-integration` (`.agents/skills`, `.claude/skills`), Wegweiser in AGENTS.
* Code-Review: LOD ohne die Custom-Normalen von LOD0 (sonst verschmierte Schattierung), Alpha in der Vorschau nur
  bei Glas/Hologramm verbunden, Unity-Prüfung „Spezialmaterial gelesen, als Palette gezeichnet“, Aufräumen im
  Testbett-Skript. Viewer: Spezialmaterial-Felder dürfen im Look fehlen; „kein Spezialmaterial“ wird wie im
  Generator als `{"id": "None"}` geschrieben.
* Doku: README, AGENTS, TECHNIK.md › 11, Fahrplan.

## 1.12.2 „Bolt-On“ – 2026-10-13

* **Behoben: Viewer ruckelte.** Die Abspielposition war ein veröffentlichter Wert des Hauptmodells und wurde jedes
  Bild gesetzt – SwiftUI baute dadurch 60-mal pro Sekunde die ganze Seitenleiste (62 Skins, Slots, Regler) neu auf.
  Die Position liegt jetzt in einem eigenen kleinen Objekt (`Playhead`), das nur die Abspielleiste beobachtet, und
  wird nur bei sichtbarer Änderung gesetzt.

## 1.12.1 „Bolt-On“ – 2026-10-13

* **Behoben: Viewer stürzte ab** (`Sidebar.badgeRow`), wenn die Liste der Abzeichen kürzer wurde – Skin mit weniger
  Abzeichen, Abzeichen auf „Keins“. SwiftUI liest die Bindung des Farbwählers danach noch einmal, der gemerkte Index
  lag außerhalb der Liste. Dasselbe Muster bei den Schichten (Regler auf 0) ist mit behoben: die Bindungen suchen
  den Eintrag beim Lesen (Schichten per ID, Abzeichen mit Bereichsprüfung).
* Der aktuelle Viewer liegt nach jedem Release unter `output/RobotViewer.app` im Haupt-Checkout.

## 1.12.0 „Bolt-On“ – 2026-10-13

Aufsätze an den Sockets, Nachschwingen, Typ-Emotes; 62 Skins (15 neue: Miner, Samurai, Steam, Signal, Wood, Celest –
Startset –, Astronaut, Scout, Spike, Observer, Clockwork, Chrono, Vertex, Obsidian, Arbor; Forest mit Moos, Spross und
grünem Stoff). Alle Aufsätze ohne Durchdringung in allen Clips.

* **14 Aufsätze:** Kopf – Grubenhelm mit Stirnlampe, Samurai-Helm (Kabuto), Stacheln, Ohren, Satellitenschüssel,
  Schornstein mit Rauch, Heiligenschein (schwebt bewusst, `FLOATING_PARTS`), Blattspross; neuer Slot **Ohren**
  (`AccEar`, am linken Ohr – die rechte Hand streift das rechte Ohr in Wave/Think mit unter 1 cm) – Linsenauge,
  Zahnräder, Zifferblatt; Gesicht – Linsenringe, Helmring (per Strahl an die Kopfschale geschmiegt); Rücken –
  Rucksack mit Taschen. Slots: 34 Accessoires.
* **10 neue Robotertypen** (Miner, Samurai, Steam, Signal, Celest, Sapling, Astronaut, Clockwork, Vertex, Spike), die
  zusammen jeden Aufsatz tragen: Die QA prüft jetzt 24 Typen gegen alle Clips, `verify_robot.py` die Abdeckung.
  Gefunden und behoben: Grubenhelm im Augen-Display beim Hochschauen, Rucksack-Rolle am Hinterkopf beim Ducken,
  Zifferblatt und Zahnräder an der rechten Hand.
* **Nachschwingen:** Federknochen `Cape` und `Antenna`; eine gedämpfte Feder je Clip, Fahrtwind in den Lauf-Clips,
  gebacken als Knochenkurven (keine Laufzeit-Physik). Umhang nur weg vom Körper, Loops geschlossen.
* **Typ-Emotes** (`robotTypes[].emote`): HatTip (Gentleman – der Hut gleitet zur Hand und reitet beim Verbeugen auf
  ihr) und FlagWave (King); 71 Clips. QA und Spiel nutzen ein Typ-Emote nur mit seinem Typ.
* **Abzeichen** Stern, Zahnrad, Blatt in `T_Robot_BadgeMask3`; der Viewer packt die zwei gewählten Abzeichen je Look in
  eine Textur (beliebig viele Masken, ein Sampler), Unity nutzt drei Masken.
* **Emitter als Daten** (`robot_config.json` › `emitters`, `springBones`): Schornsteinrauch, im Viewer als
  Partikelsystem.
* **Behoben (seit 1.11):** Einzelne Typen wurden zufällig im Classic-Look gerendert, wenn derselbe Worker vorher die
  Gegenüberstellung gerendert hatte (`set_look` erkannte deren Ersatz-Texturen nicht).
* Viewer-Test: ein v2-Look bekommt den neuen Slot mit „None“ (Test angepasst, alle übrigen Werte unverändert).
* Unity-Testpaket: Federknochen, Emitter-Sockets, Umhang/Antenne schwingen, HatTip hebt den Hut (23 Prüfungen).
* Doku: README, AGENTS (Aufsätze und Bewegung), TECHNIK.md › 10, Fahrplan.

## 1.11.0 „Weathering“ – 2026-10-13

Oberflächen-Schichten, Werkstoffe und Abnutzung als Daten; 47 Skins (9 neue: Rust, Moss, Inferno, Circuit, Magma,
Bamboo, Temple, Paper, Concrete; Arctic mit Frost, Sand mit Werkstoff, Abnutzung und Schal). Modell, QA, Clip-Daten
und Typ-Maße unverändert.

* **Schichten** Rost, Moos, Frost, Glutrisse, Tarnung: gebackene Felder (Rost in Ecken/Fugen, Moos oben/in Fugen,
  Frost oben/an Kanten, Glutrisse auf einem Rissnetz, Tarnung als Fleckenfeld), per Rangfolge auf die Lackfläche
  normiert – Stärke 0,5 bedeckt 49,9 % (`verify_robot.py` prüft das). Je Schicht Farbe und Oberfläche.
* **Werkstoffe** Holz, Stein (mit Gravurbändern), Sand, Papier, Beton, Messing, Platine als Muster der Panzerung mit
  Musterfarbe, eigener Rauheit/Metall und passender Grundfarbe.
* **Abnutzung** 0 sauber – 0,5 gebacken – 1 verbeult aus den Kratzer- und Schmutzfeldern (Abnutzung je Skin).
* Neue Masken `T_Robot_LayerMask*`, `T_Robot_MaterialMask*`; ImageMagick-Vorlagen Rissnetz und Tarnflecken.
* **Gegenüberstellung** `Previews/weathering.png`: Classic mit jeder Schicht in drei Stärken, jedem Werkstoff, drei
  Abnutzungsstufen; Skin-Karten nennen Werkstoff, Schichten und Abnutzung.
* **Look-Format 4** (`material`, `layers`, `wear`) mit Migration aus 1–3; Fixture `look_v3.json`.
* **Viewer:** Werkstoff, Schichten mit Reglern, Abnutzung. Alle Look-Werte stehen jetzt in einer Parameter-Textur
  (SceneKit verträgt nur wenige Shader-Argumente); Kratzer/Schmutz, Tarnung und das Werkstoff-Muster in freien Kanälen.
* **Unity-Testpaket:** Shader und Look-Format 4; `run_testbed.sh` scheitert jetzt bei Compiler-Fehlern (Unity hatte
  stillschweigend die alten Assemblies weiterbenutzt).
* Code-Review: Abnutzung vor Werkstoff und Schichten (sauber übermalte deren Muster), Normierung per Histogramm (kaltes
  Backen 206 → 84 s), Viewer filtert Werkstoff-Muster und Tarnfelder linear, hält nur eine Hilfstextur und stellt
  beim Abwählen eines Werkstoffs die vorherige Panzerfarbe wieder her.
* Doku: README, AGENTS, TECHNIK.md (Rangfolge-Normierung, Parameter-Textur, Compiler-Falle), Fahrplan.

## 1.10.1 „Paintjob“ – 2026-10-13

**Unity-Testpaket** (`unity/RobotTestbed`, `unity/run_testbed.sh`): eigenes URP-Projekt, das die Ausgabe importiert,
20 Prüfungen und 8 EditMode-Tests ausführt und alle Skins, Typen, Ausdrücke und einige Animationsframes rendert.
QA, Clip-Daten und Typ-Maße unverändert.

* **Behoben: Der Roboter schaute in Unity nach −Z** (das README versprach +Z; im Spiel wäre er rückwärts gelaufen).
  Unity übernimmt Blenders Vorderseite immer als −Z – eine andere Vorwärtsachse im FBX-Kopf oder „Transform
  anwenden“ änderte daran nichts. Der Export dreht jetzt das Armatur-Objekt um 180° um Z (nur für den Export);
  `verify_robot.py` rechnet die Drehung beim Reimport heraus. Das Testpaket prüft Blickrichtung und ClimbUp-Root-Motion.
* Vorlagen für den Einbau ins Spiel: URP-Shader `CubeEscape/RobotBody` und `CubeEscape/RobotScreen`, `RobotLook`
  (Format 3 mit Migration), `RobotAppearance`, `RobotFace`.
* README: Augen-Atlas 4 × 10, Achsen, Abschnitt „Unity-Testpaket“; AGENTS: Testpaket im Prüfablauf; TECHNIK.md:
  Unity-Lehren; Fahrplan: Unity-Testpaket erledigt.

## 1.10.0 „Paintjob“ – 2026-10-12

Skins werden Daten: 38 Skins (20 bestehende, Gold/Neon/Toxic/Ocean aufgewertet, 18 neue: Shadow, Hazard, Medic, Volt,
Candy, Industrial, Prototype, Retro, Orbit, Bolt, Lab, Nimbus, Arc, Lumin, Cobalt, Crimson, Chrome, Tank) aus
`data/skins.json`. Modell, QA-Bericht, Clip-Daten und Typ-Maße identisch mit 1.9.

* **Skin-Datenformat** `CubeEscapeRobotSkins` 1: Palette (linear oder sRGB-`#rrggbb`), `armor2` + `zones2`, Oberfläche,
  bis zu zwei Abzeichen, ein Leuchtlinien-Muster, optional Teile/Ausdruck/Display, Thema, Stufe, Startset, Konzeptbezug.
  Prüfung beim Laden mit Skin-Namen in der Meldung.
* **Masken statt Textursätze:** Abzeichen (Warnstreifen, Kreuz, Wolke, Nummer, Biohazard, Schriftzeichen,
  Rennstreifen, Zielmarke) als Decals an Ankerpunkten (Ohrscheibe, Brust, Schulter, Oberschenkel, Rücken, Hinterkopf)
  oder prozedural; Leuchtlinien (Leiterbahnen, Ringe, Kanten, Blitze, Gelenke); 8 Lackzonen für die Zweitfarbe aus
  dem Flächenattribut `paint_zone`. Neue Texturen `T_Robot_BadgeMask*`, `LineMask*`, `ZoneMask*` (RGBA, Kanal = ID).
  Skins werden weiter aus einmal gebackenen Ebenen per numpy zusammengesetzt (`decorate_skin()`).
* **Oberflächen je Skin** (auch in den Blender-Vorschauen); Candy und Chrom mit weniger Metall, damit sie im dunklen
  Spiel nicht schwarz wirken.
* **Gesichter:** Damage (rot), LowPower (gedimmt), Check, Warning (orange), Blink; Augen-Atlas 4 × 10; Färbung und
  Leuchtkraft als Daten (`expressionStyles`), Blinzeln als Laufzeitregel (`blink`).
* **Animierte Display-Motive** Heartbeat, Spinner, Talk als Flipbook (`T_Robot_DisplayFlipbook.png`,
  `displayAnimations`).
* **Skin-Bogen im Stil der Konzeptbilder** (`Previews/skin_sheet.png`): Dreiviertel-Karte mit Randlicht in der
  Leuchtfarbe und Bloom (Compositor-Node-Gruppe, Blender 5.2), Farbpunkte, Systeme, Startset-Stern;
  `Previews/silhouettes.png` aus dem Alphakanal.
* **UI-Icons** (`Icons/`, 113 Stück: Skins, Ausdrücke, Display-Motive, Abzeichen, Accessoires) mit
  Lesbarkeitsprüfung (Füllgrad und Lage, ohne Neural Engine).
* **Konzeptbögen ausgewertet:** `scripts/concept_palettes.py` → `data/concept_palettes.json` (42 Karten), Bögen in
  `docs/concepts/`.
* **Look-Format 3** (`skin`, `armor2`, `zones2`, `badges`, `lines`) mit Migration aus Version 1 und 2; neue
  Test-Fixtures `look_v2.json`, `look_v3_unknown.json`.
* **Viewer:** Zweitfarbe, Abzeichen, Leuchtlinien, Skin-Teile, animierte Displays, Gesichts-Stile, Blinzeln,
  A/B-Vergleich, GIF-/MP4-Export (auch `--gif`/`--video`). Daten-Texturen werden roh geladen (NSImage multiplizierte
  Masken mit Alpha vor – betraf auch Metall/Glätte seit 1.7); Masken und Uniforms gepackt (SceneKit zeichnet ab etwa
  23 Shader-Argumenten kommentarlos pink).
* Materialaufbau mit gemeinsamen Projektionen je Anker: 0,4 statt 2,2 s, bitgleiche Masken.
* `verify_robot.py`: Skin-Daten, Masken mit Pixeln, Zonen überlappungsfrei, Gesichter, Flipbook, Icons, Bögen.
* Doku: `docs/TECHNIK.md` (Techniken und Lehren, Übertrag auf den Cubegen-Modell-Editor), README, AGENTS,
  `docs/FAHRPLAN.md` (Entscheidungen vom 12.10., 1.10-Stand, neue Punkte für später).

## 1.9.2 „Afterburner“ – 2026-10-12

Prüfung der Neural Engine (Fahrplan-Vorschläge „visuelle Prüfung per Vision“ und „Vorlagen per Bildgenerator“):
**kein Mehrwert, kein Einsatz.** Renders sind über Prozesse hinweg bitnah gleich (RMSE 1e-5), ein exakter
Pixelvergleich genügt; Vision-Feature-Prints trennen Fehler nicht besser von legitimen Änderungen und liefern je
Gerät andere Abstände (Neural Engine 0,0026, CPU 0,0003 für dasselbe Paar). Tempo: 3,8 ms je Bild auf der Neural
Engine, irrelevant gegenüber dem Render. Messwerte in README › „Neural Engine – geprüft“. Modell und Ausgaben
unverändert.

* Fahrplan: zwei neue Aufgaben für Generator 1.10, bei denen die Neural Engine sinnvoll sein kann – Konzeptbögen
  auswerten (Schneiden, Namen per Texterkennung, Farbvorschläge; einmaliges Werkzeug) und Icon-Lesbarkeit prüfen
  (Saliency, nur Hinweis). Beide mit Zeitregel: Kosten sie zu viel Zeit, werden sie nicht umgesetzt.

## 1.9.1 „Afterburner“ – 2026-10-12

Nur Regeln und Agenten-Werkzeuge; Modell, Ausgaben und QA unverändert (abgestimmt mit Cube Escape 0.45.1 und
Cubegen 0.14.1).

* `CLAUDE.md` importiert `AGENTS.md` – Claude Code lädt die Regeln jetzt in jeder Sitzung.
* AGENTS.md › „Plugins und Hooks (Claude Code)“: welches Plugin wofür (Blender-MCP, quick-gate-python, code-review,
  github, engineering, design, hookify).
* Acht hookify-Regeln in `.claude/`: blockieren Handänderungen an `output/`, Änderungen an Cube Escape, nacktes
  `git stash` und Force-Push; verlangen eine Teststufe vor dem Sitzungsende nach Änderungen in `scripts/`; warnen bei
  Git-Veröffentlichung, Cache-Schema (`unwrap()`/Backen) und Viewer-Änderungen.

## 1.9.0 „Afterburner“ – 2026-10-12

Alle Stufen parallel, gemessen auf dem M5 (ruhige Maschine, warmer Cache): quick 10–11 s (vorher 25), standard 54–68 s
(133), full 211 s (655). QA-Bericht, Clip-Daten und Typ-Maße identisch mit 1.8.0, `verify_robot.py` 211/211.

* **Arbeitspakete statt fester Abschnitte:** QA (je Robotertyp und Clip-Gruppe, große Gruppen geteilt) und alle
  Vorschau-Renders (Ansichten, Skins, Ausdrücke und Typen in Blöcken, Animationsbögen je Clip, GIFs je Clip) stehen
  in einer Warteschlange. Worker-Prozesse und danach der Hauptprozess holen sich Pakete (größte zuerst); die
  Ergebnisse werden in fester Reihenfolge zusammengeführt. QA-Worker starten vor dem Backen, Render-Worker direkt
  danach: Sie nehmen die UVs aus dem Cache und laden die gebackenen Texturen des Hauptlaufs (`--use-textures`).
  Damit läuft auch die Schnellstufe (nur Classic) parallel. `--workers` (Standard Kerne − 1) ersetzt `--qa-workers`,
  `--render-workers` (Standard 3; die GPU ist ab 3–4 EEVEE-Prozessen ausgelastet).
* **Worker mit `-t 1` und `nice 10`:** ohne Thread-Grenze startet jeder Blender-Prozess einen Thread-Pool je Kern
  (QA 734 statt 419 Prozess-Sekunden); der Hauptprozess (Clip-Daten, Export) hat Vorrang.
* **Vorschau-Schnellmodus** für Skins, Typen, Animationsbögen und GIFs: deckende Augen statt additiv-transparenter
  und kein Raytracing. Gemessen: Ein `BLENDED`-Material erzwingt EEVEEs Forward-Pipeline mit einer zweiten
  Schatten-Aktualisierung je Sample (`ShadowModule::shadow_update_finished`); 0,46 → 0,16 s je Bild. Ansichten
  (inkl. Hero) und Ausdrücke bleiben exakt. Animationsbögen und GIFs mit 12 statt 24/16 Samples.
* **QA schneller, Ergebnisse bitgleich:** Hüllquader-Test für alle Paare eines Frames in einem numpy-Aufruf,
  Koordinaten als `float32` gelesen (natives Format), nur ein QA-Mesh je Prozess in der Szene.
* **ImageMagick mit `MAGICK_THREAD_LIMIT=1`:** parallele Aufrufe bremsten sich über OpenMP gegenseitig aus
  (Montage 3,8 → 1,6 s); Quelltexturen bitgleich. Bögen und Kontaktbögen entstehen parallel.
* `--no-cache` schreibt den UV-Cache trotzdem (die Render-Worker brauchen ihn).
* Bericht: Systemlast zu Beginn und Ende (`timings.load`, Warnung über der Kernzahl), Prozess-Sekunden je
  Paketart, Statistik je Worker. `scripts/profile_run.py` profiliert einen Lauf mit cProfile.
* Doku: `AGENTS.md`, `CHANGELOG.md`, `docs/FAHRPLAN.md` (Abbild des Claude Docs), README aktualisiert.

## 1.8.0 „Turbo“ – 2026-10-12

Teststufen `--tier quick|standard|full` mit Zeitbudget, Schrittzeiten im Bericht. Gemessen auf dem M5: quick 25 s,
standard 133 s, full 655 s (1.7.0: ≈ 40 min).

* Skins aus sechs Backs zusammengesetzt (Konstante + Gewichtskarte je Palettenfarbe) statt 40 Einzel-Backs;
  Gegenprobe im Volllauf (Abweichung 4e-7).
* Backen einer zusammengefügten Kopie statt 35 Einzelobjekt-Sitzungen: auf Metal 0,2–1 s statt 9–20 s je
  Durchgang, Backen 50 s statt 24 min.
* Cache für UVs (nicht deterministisches Insel-Packen, kanonische Ecken-Reihenfolge), AO und Bake-Ebenen;
  ImageMagick parallel und gecacht.
* QA parallel in Blender-Prozessen, Kollisionsbäume nur bei berührenden Hüllquadern; Ergebnisse identisch mit 1.7.0.
* Rauheit, Metall und Lichtmaske in einem Bake-Durchgang.

## 1.7.0 – 2026-10-12

Ausgangsstand aus dem Ordner `CubeEscape-Robot` übernommen (erste Version unter Git): modularer Roboter
(Bauteil-Varianten, 20 Accessoires, 14 Robotertypen), 20 Skins mit Farbmasken, 28 Augen-Ausdrücke und 16
Display-Motive, Rig mit Bein- und Arm-IK, 69 Animationen in 8 Gruppen mit Clip-Daten fürs Spiel, QA je Robotertyp,
FBX (Standard, modular, je Typ), Look-Format Version 2 mit Schema und der Robot Viewer (macOS).
