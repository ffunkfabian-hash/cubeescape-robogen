# Changelog

Versionen des Roboter-Generators (zählen getrennt von Cube Escape und Cubegen). Jede Version hat einen Tag
`v<Version>` und einen Release-Branch `release/<Version>`.

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
