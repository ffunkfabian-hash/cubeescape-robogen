# Changelog

Versionen des Roboter-Generators (zählen getrennt von Cube Escape und Cubegen). Jede Version hat einen Tag
`v<Version>` und einen Release-Branch `release/<Version>`.

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
