# Changelog

Versionen des Roboter-Generators (zählen getrennt von Cube Escape und Cubegen). Jede Version hat einen Tag
`v<Version>` und einen Release-Branch `release/<Version>`.

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
