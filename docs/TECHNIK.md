# Techniken des Roboter-Generators – Übertrag auf Cubegen

Stand: Generator 1.20.6 „Powdercoat“. Diese Datei sammelt, **wie** der Generator arbeitet und was sich dabei bewährt
oder als Falle erwiesen hat. Ziel: Der Modell-Editor von Cubegen soll genauso leistungsfähig werden. Jede Technik
nennt die Fundstelle im Generator und was Cubegen davon übernehmen sollte. Pflegen bei jeder Generator-Version
(neue Technik → neuer Abschnitt, geänderte Messwerte → hier ändern).

## Auf einen Blick

| Technik | Fundstelle | Wirkung | Für Cubegen |
|---|---|---|---|
| Skin = Daten | `data/skins.json`, `load_skins()` | 62 Skins ohne Code, jeder weitere kostet Minuten | Looks/Paletten der Modelle als Datei statt Code |
| Einmal backen, linear zusammensetzen | `bake_layers()`, `compose_skin()` | 62 Skins für den Preis von 6 Backs | Farbvarianten nie einzeln backen |
| Masken statt Textursätze | `pattern_masks()`, Zonen-Attribute | Abzeichen, Leuchtlinien, Zweitfarbe zur Laufzeit | Varianten im Spiel per Shader statt Textur je Variante |
| Lackzonen aus Bauteil-Namen | `paint_zone()`, `PAINT_ZONES` | Zweitfarbe exakt an Teilgrenzen | Teile benennen, Zonen als Flächenattribut |
| Abzeichen an Ankerpunkten | `badge_anchors()`, `BADGE_PLACES` | Decals sitzen auf jedem Bauteil-Typ | Decal-Bibliothek mit Ankern je Modellfamilie |
| Knoten teilen | `pattern_masks()` | Materialaufbau 2,2 → 0,4 s | Gemeinsame Teilgraphen statt Kopien |
| Vorschau wie Konzeptbilder | `render_cards()`, `card_compositor()` | Karten in Dreiviertel-Ansicht mit Bloom und Randlicht | Präsentationsrender für Sets und Kataloge |
| Konzeptbogen auswerten | `scripts/concept_palettes.py` | 42 Paletten in einer Sitzung, ohne Neural Engine | Referenzbilder per festem Raster auslesen |
| UI-Icons + Lesbarkeit | `build_icons()`, `icon_check()` | 113 Icons, Prüfung < 1 s | Icons je Modell/Variante für die Werkbank |
| Viewer: Daten-Texturen roh laden | `RobotScene.pixels()` | Masken mit Alphakanal bleiben intakt | Jede Laufzeit-Vorschau mit RGBA-Masken |
| SceneKit: ≤ 23 Shader-Argumente | `makeBodyMaterial()` | sonst stilles Pink | Uniforms packen (Vektoren, Bitmasken) |
| Berichtsvergleich als Gate | `robot_report.json` (`qa`, `clips`, `type_bounds`) | Modelländerungen fallen sofort auf | Golden-Bericht je Teststufe |
| Aufsätze über Typen prüfen | `ROBOT_TYPES`, `verify_bolton()` | 14 neue Aufsätze in allen 71 Clips geprüft | Jede Variante in mindestens einer geprüften Kombination |
| Rand an Schale anschmiegen | `conform_front()` | Helmring folgt der gerundeten Kopfschale | Anbauteile per Strahl auf die Trägerfläche legen |
| Federknochen gebacken | `SPRINGS`, `bake_springs()` | Umhang/Antenne schwingen ohne Laufzeit-Physik | Nachschwingen als Kurven, nicht als Physik |
| Socket reitet auf der Hand | `ATTACHMENTS`, `bake_attachment()` | Hut lüften trotz zu kurzer Arme | Requisiten per Matrix-Kette an Knochen hängen |
| Abzeichen je Look packen | `RobotScene.badgeTexture()` | beliebig viele Abzeichen, ein Sampler | Nur die gewählten Kanäle zur Laufzeit packen |
| Emitter als Daten | `EMITTERS`, `robot_config.json` › `emitters` | Schornsteinrauch im Viewer, Vorlage fürs Spiel | Partikelpunkte mit Socket statt Partikel im Modell |
| Spezialmaterial als Laufzeit-Effekt | `SPECIALS`, `preview_special_nodes()`, Viewer-Shader | 7 Effekte ohne neue Texturen, gleiche Formeln in Vorschau und Viewer | Effekte als Daten mit Beschreibung fürs Zielmaterial |
| Masken für Unity packen | `unity_textures()`, `UNITY_TEXTURES` | 14 → 10 Texturen, ¼ Pixel, Zonen als ID | Laufzeit-Masken packen, Belegung als Daten |
| LOD per Collapse | `make_lod()`, `export_lods()` | 6 000 / 2 500 Dreiecke, gleiche Knochen | Detailstufen automatisch je Modell |
| Treffer-Kapseln gemessen | `hit_capsules()` | 15 Kapseln, 95–100 % Abdeckung | Kollisionsformen aus der Geometrie messen |
| Servo-Ereignisse | `servo_events()` | Klang-Ereignisse aus Gelenkgeschwindigkeit | Ereignisse aus Bewegung ableiten statt setzen |
| Ein Unity-Start | `TestbedTestRun` | Testbett 25–30 → 12 s | Tests im selben Batch-Prozess wie der Import |
| QA-Ergebnis-Cache | `JobContext.cached_qa()` | Standardlauf 121 → 51 s | Prüfergebnisse nach Eingaben + Prüfcode cachen |
| Prüfkette parallel | `scripts/check_output.sh` | Prüfungen nach dem Lauf ≈ 55 → 35 s | Unabhängige Prüfer gleichzeitig starten |
| Render-Cache per Szenen-Abdruck | `render_still()`, `scene_digest()` | 405 von 405 Vorschaubildern aus dem Cache; eine Skin-Farbe rendert nur ihre 3 Bilder neu | Bilder nach dem cachen, was die Kamera sieht – nicht nach dem Code |
| Ausgaben-Cache | `OutputCache`, `export_digest()` | quick warm 22 → 9–12 s | Exporte nur schreiben, wenn sich ihr Inhalt geändert hat |
| Kanonische Reihenfolge vor Decimate | `canonical_bm()` | Facetten-Form in jedem Prozess gleich | Vor jeder reihenfolgeabhängigen Operation sortieren |
| Voxel-Rechtecke | `emit_voxel_quads()`, `voxel_appearance()` | ein Drittel weniger Dreiecke, gleiches Bild | Gleich aussehende Nachbarflächen zusammenfassen |
| Zeitband statt festem Budget | `timing_band()` | Laufzeit unter Last nicht mehr falsch „zu langsam“ | Zeiten gegen ruhige Referenzläufe mit Lasttoleranz bewerten |
| Gesetzte Kontakte im Zieltempo | `planted_stride()`, `run_foot()`, `verify_longstride()` | Lauf-Clips bei 5/8/4/2,4 m/s ohne Rutschen | Schrittlänge aus Tempo × Kontaktzeit, nicht nach Gefühl |
| Rolle über die Hülle aller Teile | `roll_hull()`, `lifted()` | ein Clip rollt für jeden Typ, nichts sinkt in den Boden | Bodenkontakt aus der Geometrie aller Varianten ableiten |
| Standard-Oberfläche an der Spielwelt messen | `FINISH_PARAMS`, `verify_powdercoat()` | weißer Skin bleibt in der dunklen Station weiß | Standardwerte im Zielumfeld prüfen, alte Werte als neue ID anhängen |
| Ein Cache für alle Checkouts | `scripts/robocache.py` | erster Volllauf im neuen Worktree 628 s → 87 s, alle 1 649 Bilder aus dem Cache | Cache außerhalb der Arbeitskopie, Einträge nach Inhalt, atomar, nach Größe aufräumen |
| Prüfplan aus dem Diff | `scripts/testplan.py` | nur die nötigen Stufen statt „alles, sicherheitshalber“ | Zuordnung Datei → Prüfweg als Tabelle mit Tests |
| Ruhige Maschine | `scripts/quiet.py` | schwere Schritte pausieren andere Sitzungen selbst | Last messen, bevor gemessen wird |
| Ein schwerer Lauf zur Zeit | `scripts/heavy_lock.py` | Volllauf, Prüfungen und Unity-Testbett warten aufeinander, auch über Repos | Rechnerweite Sperre mit Halterzeile statt Raten aus der Prozessliste |
| Release als ein Befehl | `scripts/release.py` | Fast-Forward, Tag, Release-Branch und atomarer Push mit Prüfungen | Erst prüfen, dann ändern; Probelauf als Standard |

## 1. Skin = Daten

- Ein Skin ist ein Datensatz in `data/skins.json` (Format `CubeEscapeRobotSkins`, Version 1): Palette (`armor`,
  `armor2`, `mech`, `metal`, `eye`, `screen`, `accent`, `visor`, `fabric`), Oberfläche (`finish`), bis zu zwei
  Abzeichen, ein Leuchtlinien-Muster, Zweitfarben-Zonen (`zones2`), optional Bauteile, Ausdruck, Display und Metadaten
  (`theme`, `stage`, `startSet`, `concept`).
- Farben als lineare Listen (exakte Altwerte) **oder** als sRGB-`#rrggbb` (bequem aus Konzeptbildern); der Loader
  rechnet um. Jeder Fehler nennt Datei und Skin (`load_skins()`), Bezüge auf Bauteile/Ausdrücke prüft
  `validate_skins()`, sobald das Modell sie kennt.
- IDs sind ewig: Skins, Abzeichen, Muster, Zonen und Oberflächen werden nie umbenannt, Listen nur am Ende erweitert
  (Kanal = Position).
- Das Spieler-Format (`*.robotlook.json`) ist versioniert; jede Version hat eine Migration (v1 → v2 → v3) und eine
  Test-Fixture (`viewer/tests/look_v*.json`), alte Fixtures werden nie geändert.

**Für Cubegen:** Modell-Looks (Materialpaletten, Patina, Abzeichen) als versionierte Datendatei mit Loader-Prüfung
statt als Python-Konstanten; jede Formatänderung mit Migration und Fixture.

## 2. Einmal backen, linear zusammensetzen

- Jede Körperfarbe hängt linear von der Palette ab: `Basis = C + Σ w_k · Palette_k`. Ein Back mit schwarzer Palette
  liefert `C`, je drei Palettenfarben ein Back (eine pro RGB-Kanal) liefert die Gewichtskarten `w_k`. Danach entsteht
  jeder Skin per numpy (`compose_skin()`), 38 Skins in etwa 5 s bei 1024² (1.10).
- Gegenprobe im Volllauf (`--check-compose`): Der Standard-Skin wird zusätzlich direkt gebacken, Abweichung 4e-7,
  Abbruch ab 2e-3. Bricht ein Material die Linearität (z. B. Palettenfarbe in einer Potenz), fällt das sofort auf.
- Ein Back je zusammengefügter Kopie (`joined_copy()`): Cycles öffnet je ausgewähltem Objekt eine eigene Sitzung,
  auf Metal kostet jede eine Geräte-Synchronisation (35 Teile: 9–20 s je Durchgang statt 0,2–1 s).
- Cache-Schlüssel am Inhalt: Geometrie in kanonischer Ecken-Reihenfolge (Blenders Insel-Packen und Flächenfolge sind
  nicht deterministisch), Node-Signatur der Materialien ohne Palettenwerte, Quelltexturen, Einstellungen. Was der
  Schlüssel nicht sieht, bekommt eine Schemaversion (`BAKE_SCHEMA`, `UV_SCHEMA`).

**Für Cubegen:** Material- und Farbvarianten eines Modells nie einzeln backen; Gewichtskarten einmal backen und
zusammensetzen. Gilt für Patina-Stufen ebenso, solange sie linear mischen.

## 3. Masken statt Textursätze

Statt eines Textursatzes je Skin liefert der Generator Masken (alle 2048², RGBA, Kanal = ID-Position):

| Textur | Kanäle | Herkunft |
|---|---|---|
| `T_Robot_ColorMask` / `LightMask` | Panzerung, Mechanik, Metall / Lichter | Material je Fläche |
| `T_Robot_BadgeMask`, `…2`, `…3` | 11 Abzeichen | Decals an Ankerpunkten, prozedurale Streifen |
| `T_Robot_LineMask`, `…2` | Leiterbahnen, Ringe, Kanten, Blitze / Gelenke | ImageMagick-Kacheln, Normalen, Flächenattribut |
| `T_Robot_ZoneMask`, `…2` | 8 Lackzonen | Flächenattribut `paint_zone` |

- **Gebacken wird die Maske, nicht die Farbe:** Masken-Kanäle sind Emissions-Backs von Skalarfeldern (drei je Back);
  beim Backen mit der Farbmaske multipliziert (Abzeichen/Zonen: Panzerung + Metall, Linien: Panzerung).
- **Zusammensetzen mit Helligkeitsverhältnis:** Ein Abzeichen oder die Zweitfarbe ersetzt die Farbe, behält aber
  Schmutz, Kratzer und AO: `neu = Farbe · clamp(lum(Basis) / lum(Bezug), 0.2, 1.6)`; Bezug = Panzerung oder Metall
  laut Farbmaske. Dieselbe Formel steht in numpy (`decorate_skin()`), im Viewer-Shader und kommt so nach Unity.
- **Leuchtlinien** färben die Albedo (`linePaint` 0,85 · 0,6) und addieren Emission; Bloom macht daraus Licht.
- **Lackzonen aus Bauteil-Namen:** Beim Zusammenfügen bekommt jede Fläche ein Int-Attribut `paint_zone` (Name ohne
  `_L/_R` → Zone) und `glow_part` (Ringteile). Ein Attribute-Node liest es, ein Vergleich macht daraus die Maske. Exakt
  an Teilgrenzen, ohne Koordinaten-Raten, und es bleibt beim Join erhalten.
- **Abzeichen an Ankerpunkten:** Ein Anker ist eine Projektionsebene mit Rechteck und Seitenbedingung (Schulter
  seitlich, Ohrscheibe, Brust unten, Oberschenkel, Rücken, Hinterkopf). Seitenebenen nutzen `y · sign(x)`, damit Schrift
  auf beiden Seiten lesbar bleibt. Jedes Abzeichen wählt seine Anker (`BADGE_PLACES`).
- **Prozedurale Muster:** Warnstreifen = `fract((x+y+z)/0.06) < 0.5` in Regionen; Ringe = Bänder in z; Kanten =
  `max(|n.x|,|n.y|,|n.z|) < 0.8` (Mitte jeder gerundeten Kante); Leiterbahnen/Blitze = kachelbare ImageMagick-Texturen,
  box-projiziert (beim Zeichnen um ±Kachelgröße wiederholt, damit sie nahtlos kacheln).

**Für Cubegen:** Modellsets mit Laufzeit-Varianten (Fraktionsfarben, Warnmarkierungen, Leuchtstreifen, Schadenszonen)
als Maskenkanäle liefern; Zonen über benannte Teile und Flächenattribute; Decal-Anker je Modellfamilie definieren.

### Schichten und Werkstoffe (1.11)

- **Felder statt fertiger Bilder:** Jede Schicht ist ein gebackenes Skalarfeld aus Geometrie und Rauschen (Normale
  nach oben, Ambient Occlusion für Fugen, Kanten über die Normalen, ImageMagick-Rissnetz und -Flecken). Die Stärke ist
  eine Schwelle auf dem Feld – ein Feld liefert alle Stufen von 0 bis 1.
- **Rangfolge-Normierung:** Nach dem Backen wird jedes Feld innerhalb der Lackfläche durch seinen Rang ersetzt
  (`equalise()`); danach bedeckt Stärke a genau den Anteil a (gemessen 49,9 % bei 0,5). Ohne das wären die Regler je
  Schicht völlig unterschiedlich empfindlich.
- **Werkstoffe** sind Muster (1 = Panzerfarbe, 0 = Musterfarbe) aus Blender-Prozeduraltexturen (Wave für Holz,
  Voronoi für Stein/Beton, Rauschen für Sand/Papier, ImageMagick-Kacheln für Messing/Platine) plus eine passende
  Grundfarbe – auf weißer Panzerung sieht sonst kein Werkstoff wie sich selbst aus.
- **Abnutzung** als Interpolation um den gebackenen Stand: Kratzer- und Schmutzfeld getrennt backen, dann zur
  Lackfarbe zurück (sauber) oder zu blankem Metall und Dreck hin (verbeult). Sie kommt **vor** Werkstoff und Schichten
  – sonst übermalt „sauber“ deren Muster mit flacher Lackfarbe (Befund im Code-Review).
- Die Normierung braucht keine volle Sortierung: Histogramm mit 65 536 Klassen, Mittelrang je Klasse.
- Im Viewer liegt die Zonen-Nummer mit stetigen Feldern in einer Textur: die Nummer mit `nearest`, die Felder mit
  linearer Filterung lesen (zweimal sampeln), sonst werden Holzmaserung und Tarnflecken pixelig.

**Für Cubegen:** Patina, Rost und Schmutz der Modellsets als normierte Felder mit Regler statt fester Stufen.

## 4. Leistung beim Aufbau

- **Jeder Shader-Knoten kostet in Blender etwa 1 ms** (eindeutige Namen, wachsender Baum). Die Abzeichen erzeugten
  zuerst rund 600 Knoten; gemeinsame Projektionen je Anker senkten den Materialaufbau von 2,2 auf 0,4 s – bei
  bitgleichen Masken.
- Profilieren statt raten: `scripts/profile_run.py` (cProfile, `--workers 0`).
- Zeiten nur auf ruhiger Maschine vergleichen; parallele Läufe anderer Agenten verfälschen um das 1,5- bis 5-Fache
  (`timings.load` im Bericht).

## 5. Vorschau wie die Konzeptbilder

- **Skin-Karten** (`render_cards()`): perspektivische Dreiviertel-Kamera (Objektiv 58 mm, 2,45 m Abstand), Randlicht
  in der Leuchtfarbe des Skins, Welt = Kartenfarbe, Bloom. Montage per ImageMagick: Render, vier Farbpunkte
  (Panzerung, Zweitfarbe, Lichter, Augen), Systeme, „NN // NAME“, Thema, Startset-Stern → `Previews/skin_sheet.png`.
- **Bloom in Blender 5.2:** Compositor als Node-Gruppe (`bpy.data.node_groups.new(..., "CompositorNodeTree")`,
  Ausgang über `interface.new_socket`, `scene.compositing_node_group`), Glare-Knoten Typ „Bloom“. Schwelle **über**
  der normalen Beleuchtung (2,2), sonst strahlt die ganze Figur; Stärke 0,3, Größe 0,3.
- **Silhouetten** aus dem Alphakanal transparenter Frontrender (`-alpha extract -threshold`).
- Alle Bögen mit `-depth 8` schreiben (16 Bit kosteten 19 MB für einen Bogen).

**Für Cubegen:** Set- und Katalogübersichten im Stil der Konzeptbilder; Bloom nur auf Emission.

## 6. Konzeptbögen auswerten

- `scripts/concept_palettes.py`: festes Kartenraster je Bogen (gemessen am Prüfbild mit Markierungen), Median aus
  5 × 5 Pixeln je Farbpunkt → `data/concept_palettes.json`. Namen abgetippt (schneller und sicherer als Texterkennung).
- Lehre: Die generierten Bögen liegen **nicht** auf einem exakten Raster (Spaltenabstand schwankt um ±8 px). Eine
  „automatische Feinsuche“ nach gleichmäßigen Flächen landete in Lücken zwischen den Punkten – feste, am Prüfbild
  vermessene Positionen waren zuverlässiger.
- Neural Engine nicht nötig (Zeitregel des Fahrplans eingehalten).
- Das Abzeichen „Schriftzeichen“ (侍) braucht die macOS-Schrift Hiragino; wie Metal und der Viewer bindet das den
  Generator an macOS. Für Cubegen (plattformunabhängig) Schriften mitliefern oder Zeichen als Pfade zeichnen.

## 7. UI-Icons und Lesbarkeit

- `build_icons()`: Skins aus den Kartenrendern (Kopf und Brust), Ausdrücke/Display/Abzeichen direkt aus den Atlanten
  (eingefärbt), Accessoires einzeln transparent gerendert und auf ihre Größe zugeschnitten → `Icons/<Art>/<ID>.png`.
- Lesbarkeit ohne Saliency-Modell (`icon_check()`): Objekt füllt 30–96 % der längeren Seite und sitzt mittig; nur
  Hinweis im Bericht. Deterministisch, unter 1 s für alle Icons.

**Für Cubegen:** Icons je Modell, Variante und Set für die Werkbank-Palette; dieselbe Prüfung.

## 8. Laufzeit-Vorschau (SceneKit-Viewer) – Fallen

- **NSImage multipliziert RGB mit Alpha vor.** Steht im Alphakanal eine eigene Maske, sind die anderen Kanäle dort
  gelöscht (betraf auch Metall/Glätte seit 1.7). Daten-Texturen deshalb roh laden: `CGImageSource` → Bytes →
  `MTLTexture` (`RobotScene.pixels()`, `packed()`).
- **SceneKit zeichnet einen Shader-Modifier ab etwa 23 eigenen Argumenten kommentarlos pink.** Keine Fehlermeldung
  (auch nicht im System-Log); Fehlersuche durch Weglassen. Abhilfe: Uniforms packen (vier Bezugshelligkeiten in einem
  `float4`, Zonen als Bitmaske in `finish.w`) und Masken beim Laden zusammenlegen (Zonen → eine Zonen-Nummer,
  `filter::nearest`).
- **Parameter-Textur statt Argumenten (1.11):** alle Look-Werte als 32 × 1 `rgba32Float`-Textur
  (`writeParams`), im Shader `#define P(i) params.read(uint2(i, 0))`. So passen Schichten, Werkstoffe und Abnutzung
  dazu, ohne das Argument-Limit zu berühren; 8 Texturen plus Parameter-Textur liefen ohne Pink.
- Den Shader-Text allein prüfen: `MTLDevice.makeLibrary(source:)` in einem kleinen Swift-Programm (die Metal-Toolchain
  fehlt ohne Xcode-Download).
- Animierte Display-Motive als Flipbook (Zeile je Motiv, `fps` in den Daten), Blinzeln als Laufzeitregel (`blink`),
  Ausdrucks-Stile (Färbung, Leuchtkraft) als Daten – nichts davon ist eine Clip-Änderung.
- A/B-Vergleich als zweite Szene mit gleichem Clip und gleicher Wiedergabe; Export als GIF (ImageIO) und MP4
  (AVAssetWriter, H.264, gerade Kantenlängen) offscreen Bild für Bild.

**Für Cubegen:** Gilt für jede macOS-Vorschau (App 0.16) mit Masken oder vielen Parametern.

## 9. Unity-Testpaket – Lehren

- **Ein eigenes Testprojekt** statt des Spiels: `unity/RobotTestbed` (URP, linear, Forward) importiert die Ausgabe
  per Batch-Modus, baut Materialien/Prefabs/Animator per Code und prüft. Fand im ersten Lauf einen Fehler, den keine
  Blender-Prüfung sehen konnte: Der Roboter schaute nach −Z.
- **Blickrichtung:** Unity übernimmt Blenders −Y immer als −Z; Vorwärtsachse im FBX-Kopf und „Transform anwenden“
  ändern daran nichts (Unity rechnet die Kopfangaben konsistent um). Abhilfe: das Armatur-Objekt nur für den Export
  um 180° drehen; die Reimport-Prüfung muss die Drehung herausrechnen (auch in den Takes, die sie als Schlüssel
  tragen). Immer per Fußspitzen-Messung und Root-Motion-Endpunkt in Unity prüfen.
- Die FBX behält die Pose der zuletzt exportierten Animation – Höhen in der Pose von Idle-Frame 0 messen.
- **Import:** Daten-Texturen (Masken, Metall/Glätte) `sRGB aus`, `Alpha is Transparency aus`, unkomprimiert;
  Atlanten ohne NPOT-Skalierung (2048 × 2560) und ohne Mipmaps; Loop-Flags und Bake-Into-Pose per AssetPostprocessor
  aus den Clip-Daten; der Root-Motion-Knoten ist ein Pfad (`Armature/Root`).
- **Laufzeit:** Farben linear per `MaterialPropertyBlock.SetVector` (SetColor rechnet im linearen Farbraum noch einmal
  um); Konfigurationen als `TextAsset` im Prefab speichern, nicht als geparstes Objekt (überlebt das Speichern nicht).
- **Batch-Screenshots** in eine eigene RenderTexture (Batch-Bildschirm 640 × 480), zweimal rendern.
- **Compiler-Fehler sind kein Abbruch:** Kompiliert ein Skript nicht, läuft Unity im Batch-Modus mit den letzten
  guten Assemblies weiter – Prüfungen und Tests sind dann grün, aber alt. Logs nach `error CS` durchsuchen und den
  Lauf scheitern lassen (`run_testbed.sh`).

**Für Cubegen:** dasselbe Muster für Modellsets – ein kleines Unity-Testprojekt je Generator, das Import, Kollision,
Materialien und Screenshots automatisch prüft.

## 10. Aufsätze, Federknochen, Typ-Emotes (1.12)

- **QA-Abdeckung über Typen:** Die QA prüft Robotertypen (feste Kombinationen) gegen alle Clips. Jeder neue Aufsatz
  muss deshalb in mindestens einem Typ stecken; `verify_robot.py` prüft das (`ATTACHMENTS_1_12`). So fanden sich in
  der ersten Runde: Grubenhelm im Augen-Display (die Augen wandern beim Hochschauen nach oben → Helm 2,7 cm höher),
  Rucksack-Rolle am Hinterkopf beim Ducken (nach unten verlegt), Zifferblatt/Zahnräder an der winkenden und denkenden
  Hand (Hand streift das rechte Ohr mit < 1 cm → Ohr-Aufsätze nur links).
- **Abstände messen statt raten:** kleines Skript, das je Frame den kleinsten Abstand zwischen Hand-Vertices und
  einem Aufsatz misst (BVH `find_nearest`). Der Vergleichswert ist das Grundmodell: Klassische Ohrscheibe 9,5 mm in
  Wave – mehr Platz gibt es dort nicht.
- **Anschmiegen per Strahl (`conform_front`):** Ein Ring um das Visier wird flach um y = 0 gebaut und dann Vertex
  für Vertex auf die Vorderseite der Kopfschale gelegt (Strahl entlang +Y gegen deren BVH, fester Abstand). Ein
  skalierter Torus hätte an den gerundeten Seiten 5 cm frei geschwebt.
- **Absichtlich schwebende Teile** (Heiligenschein) stehen in `FLOATING_PARTS`; sonst meldet die Verbindungsprüfung
  sie – starr am Socket folgen sie trotzdem jeder Bewegung.
- **Federknochen gebacken:** Punktmasse an der Knochenspitze folgt der (zur Schwerkraft geneigten) Ruhelage als
  gedämpfte Feder (Frequenz, Dämpfung je Knochen), Fahrtwind je Lauf-Clip. Die Elternmatrix je Frame kommt aus einer
  eigenen Vorwärtskinematik über die gelösten Posen (`Solver.pose_matrix`, Blenders Formel
  `Eltern · Ruhe-relativ · Basis`) – kein `frame_set`, das alle Meshes neu auswertet. Loops: vier Zyklen einschwingen,
  den letzten behalten, die Restabweichung linear über den Zyklus verteilen. Begrenzung: nur weg vom Körper (Umhang
  0–70°, seitlich ±7°, sonst heben sich die oberen Ecken in den Rucksack – DanceSpin, Vault).
- **Requisit an der Hand (`bake_attachment`):** Socket-Matrix im Greif-Frame relativ zur Hand merken, danach je Frame
  `Hand · Griff` und daraus die lokale Basis des Sockets zurückrechnen. Der Hut gleitet erst zur Hand (Pose-Kanal
  `hat`: Neigung, Seitenversatz, Hub), weil die kurzen Arme den Zylinder nie erreichen.
- **Typ-Emotes gehören zum Typ:** HatTip mit Krone, Kopfhörer oder Stacheln wäre Unsinn und kollidiert; die QA prüft
  ein Typ-Emote nur mit seinem Typ (`type_emotes()`), das Spiel spielt es nur mit dessen Aufsatz.
- **Abzeichen dynamisch packen (Viewer):** Statt aller Abzeichen-Masken mit One-Hot-Wählern zu sampeln, packt der
  Viewer die zwei gewählten Kanäle bei jeder Änderung in eine Textur (R, G). Neue Abzeichen-Masken kosten so keinen
  Sampler; Unity nutzt bis zur Spielintegration noch drei Masken mit Wählern.
- **Emitter als Daten:** Rauch ist kein Mesh, sondern ein Punkt mit Socket, Position und Richtung im Charakterraum.
  Der Viewer setzt ein `SCNParticleSystem` darauf (vormultipliziertes Weiß als Sprite, sonst Ringe; Vorlauf 2 s, damit
  Standbilder schon eine Fahne zeigen).
- **Falle Vorschau-Zustand:** Ein Render-Paket, das Texturen tauscht (Gegenüberstellung), muss seinen Zustand
  zurücksetzen, den das nächste erwartet – `set_look` erkannte die Ersatz-Texturen nicht am Namen, und einzelne Typen
  wurden zufällig im Classic-Look gerendert (seit 1.11, je nach Paketreihenfolge im Worker).

**Für Cubegen:** Anbauteile als Varianten mit Socket, Prüfkombinationen, die jede Variante abdecken; nachschwingende
Teile (Kabel, Fahnen, Antennen) gebacken; Requisiten über Matrix-Ketten; Partikelpunkte als Daten.

## 11. Spezialmaterialien, Unity-Packen, LOD, Kapseln, Servo (1.13)

- **Spezialmaterial = Laufzeit-Effekt über dem Skin.** Nichts wird gebacken; der Skin liefert die Grundfarbe, der
  Effekt mischt sie. Formeln (h = Höhe, x = Seite in m, f = (1 − |n·v|)³, s = Stärke, t = Zeit × Tempo):
  Glas `mix(c, A, 0,6 s)`, Alpha `0,35 + 0,6 f`, Rand `B · f · 0,25`; Hologramm Farbe `A · 0,15`, Leuchten
  `A (0,35 + 1,6 f) · Scan`, `Scan = 0,65 + 0,35 · step(0,5, frac(40 h − 0,5 t))`, Alpha `(0,25 + 0,6 f) · Scan`;
  Glitch Band `floor(12 h) + 13 floor(6 t)`, Zufall > 0,78 → A, > 0,9 → B; Schillern Farbton `frac(1,2 f + 0,35 h)`,
  Sättigung 0,55; Skizze Kontur `smoothstep(0,25, 0,5, 1 − |n·v|)`, Schraffur `step(0,7, frac(45 (x + h)))` wo
  `1 − 1,6 · Helligkeit` > 0; Regenbogen `frac(0,8 h + 0,3 x + 0,15 t)`; Galaxie Wertrauschen (3 Oktaven, Maßstab 3)
  zwischen A und B, Sterne auf einem 120/m-Zellraster. Visier und Lichter (nichts in der Farbmaske) bleiben.
- **Vorschau und Viewer gleich halten:** Blender bekommt je Effekt einen Knotenzweig, Wertknoten `SpecialW_<id>`
  schalten ihn (0/1); der Viewer rechnet dieselben Formeln im Metal-Shader (Modus = Position in `specials`). Glas und
  Hologramm brauchen in SceneKit `transparencyMode = .dualLayer` und eine Transparenz knapp unter 1, sonst bleibt das
  Material im opaken Durchgang; Alpha kommt aus `_surface.diffuse.a` / `_surface.transparent`.
- **Beschreibung fürs Zielmaterial als Daten:** jeder Eintrag in `specials` trägt `urp` – so muss die spätere
  Spielversion nichts aus dem Code lesen.
- **Masken für Unity packen (`unity_textures`):** wie der Viewer beim Laden: Farbmaske + Lichter, Metall + Abnutzung +
  Schmutz + Glätte, Hilfstextur (Gelenk-Leuchtmuster, Zonen-ID, Tarnung), dann Schichten, Linien, Werkstoffe,
  Abzeichen je RGBA – höchstens 1024² (2×2-Mittel). Die Zonen-ID entsteht aus den Zonenmasken (Maximum > 0,5) und wird
  im Shader mit Punktfilter und Mip 0 gelesen; `verify_robot.py` vergleicht sie mit den Masken (> 98 % gleich).
- **LOD per Decimate „Collapse“:** Inseln bleiben getrennt, also bleiben starre Teile an ihrem Knochen; danach
  „Smooth by Angle“ (38°), weil die gewichteten Normalen verloren gehen. Dreiecksbudget je Stufe, Export ohne Takes.
- **Treffer-Kapseln messen:** je Knochen Achse aus dem Rig, Radius = 93. Perzentil des Abstands zur Achse, Enden
  0,3 r innerhalb des 2./98. Perzentils entlang der Achse (kastige Teile haben ihre Ecken an den Enden – mit voller
  Kürzung um r deckten Hand und Unterschenkel nur 55 %). Im Spiel einmal über die Bindposes in den Knochenraum.
- **Servo-Ereignisse aus Bewegung:** Summe der Winkelgeschwindigkeit je Gelenkgruppe; Ansatz, wenn sie über 90°/s und
  über das 1,8-Fache des letzten Tiefpunkts steigt, Ende unter 60 % der Spitze – so bekommt jeder Schritt und jeder
  Winkschwung ein eigenes Ereignis (ein fester Ein/Aus-Schwellwert lieferte bei gleichmäßiger Bewegung nur eines).
- **Unity-Testbett in einem Start:** die EditMode-Tests laufen per `TestRunnerApi` synchron im Pipeline-Prozess
  (`TestbedTestRun`); der zweite Unity-Start kostete mehr als die eigentliche Arbeit (Lauf ohne Änderungen 25–30 →
  12 s). Import parallel (`EditorSettings.refreshImportMode = OutOfProcessPerQueue`).

**Für Cubegen:** Effektmaterialien als Daten mit Zielbeschreibung; Laufzeit-Masken packen; LOD und Kollisionsformen
automatisch messen und prüfen; Unity-Prüfungen in einem Prozess bündeln.

## 12. Testzeit (1.13.1)

- **Ergebnisse cachen statt neu prüfen:** Ein QA-Ergebnis hängt nur am Mesh des Typs, am Rig, an den Schlüsseln des
  Clips und am Prüfcode. Der Schlüssel enthält alle vier, den Prüfcode als Quelltext der QA-Funktionen
  (`inspect.getsource`) samt Ausnahmelisten – ändert jemand die Prüfung, ist der Cache von selbst ungültig.
- **Falle: nicht jede Datenreihenfolge ist stabil.** Der erste Cache traf nie: Koordinaten und Gruppen waren in jedem
  Blender-Prozess bitgleich, aber der Startpunkt der Eckpunktschleife mancher Flächen nicht. Den Hash daher über eine
  kanonische Form bilden (je Fläche die sortierten Eckpunkte), nicht über die rohe Speicherreihenfolge.
- **Arbeit dorthin, wo die Voraussetzungen schon liegen:** Typ-FBX brauchen die gebackenen Materialien – also Pakete
  der Render-Worker, nicht der QA-Worker (die bauen ohne Texturen). Vorher den Look auf den Standard setzen; Render-
  Pakete hinterlassen sonst einen anderen Skin in den geteilten Materialien.
- **Gemeinsame Pools mit echten Abhängigkeiten:** alle ImageMagick-Bögen in einem Thread-Pool – außer Icons und
  Skin-Bogen, die bleiben eine Aufgabe, weil der Bogen danach die Kartenbilder löscht, aus denen die Icons stammen.
- **Prüfer gleichzeitig:** Verify, Viewer und Unity lesen die Ausgabe nur; nebeneinander bestimmt der langsamste die
  Zeit. Der Viewer kompiliert nur bei geändertem Quelltext (Hash), beide Architekturen parallel; Round-Trip-Tests mit
  sechs Prozessen. Mehrere Unity-Instanzen lohnen nicht: ein Projekt ist für eine Instanz gesperrt, und der Start
  dauert länger als die Arbeit.

- **Render-Cache über den Szenen-Abdruck (1.13.3):** Der Schlüssel eines Bildes ist, was der Renderer sieht: sichtbare
  Objekte (Mesh, Matrix, Pose des Rigs), ihre Materialien als Knotengraph mit allen Werten, Bilddateien nach Inhalt,
  aktive Kamera, Lichter, Welt, Compositor, Render-Einstellungen. So trifft der Cache auch nach Code-Änderungen, die
  das Bild nicht berühren, und eine geänderte Skin-Farbe rendert nur deren drei Bilder neu. Gefundene Fallen:
  - Alle eingebetteten Materialbäume heißen „Shader Nodetree“ – ein Schutz gegen Doppelzählung über den Namen
    übersprang alle Materialien außer dem ersten und gab vier verschiedenen Bildern denselben Schlüssel (falsche
    Treffer). Bäume nach Identität (`as_pointer()`) unterscheiden.
  - `matrix_world` frisch bewegter Lichter und Kameras ist veraltet, bis der Depsgraph läuft
    (`view_layer.update()` vor dem Abdruck).
  - Nur wirksame Werte aufnehmen: eine Ortho-Kamera behält die Brennweite des vorigen Perspektiv-Renders, die
    Bildnummer bleibt vom letzten Paket stehen, der Hauptprozess rendert mit 10 Threads, die Worker mit 2 – nichts
    davon ändert das Bild, alles änderte den Schlüssel (welcher Prozess ein Bild rendert, entscheidet sich je Lauf).
  - Posen über ihre Eingaben schlüsseln (Aktion + Bildnummer), nicht über die ausgewerteten Matrizen: die
    unterscheiden sich zwischen Prozessen um ~1e-7 und kippen ab und zu eine Rundung.
  - Werte von Ausgängen gehören dazu, auch wenn sie verknüpft sind: Ein Value-/RGB-Knoten (`FinishRough`,
    `TintColor` …) hält seine Einstellung im Ausgang. Nur unverknüpfte Sockel aufzunehmen ließ in 1.20 die neue
    Standard-Oberfläche unbemerkt – der warme Cache lieferte alle Vorschauen im alten Glanz (falsche Treffer).
    Nur Value-/RGB-Knoten: die Ausgänge anderer Knoten tragen Reste, die je Prozess schwanken. Und Werte, die gerade
    nicht wirken (Spezialfarben ohne Spezialmaterial), auf einen festen Wert zurücksetzen, sonst Fehltreffer.
  - Messbar machen statt raten: nach den Korrekturen trifft der zweite Volllauf 1 460 von 1 461 Bildern, der dritte alle.
  - Fehlersuche: `ROBOT_KEEP_WORKER_LOGS=1` behält die Worker-Logs (Treffer/Fehltreffer je Bild),
    `ROBOT_RENDER_DIGEST_DUMP=<Teil des Dateinamens>` schreibt die Bestandteile des Abdrucks – zwei Läufe vergleichen.

**Für Cubegen:** Generator- und Prüfergebnisse nach ihren Eingaben cachen (inklusive Prüfcode), Bilder nach dem
Szenen-Abdruck, Hashes über kanonische Daten, Engine-Prüfungen gebündelt in einem Prozess, unabhängige Prüfer parallel.

## 13. Prüfen

- Teststufen `quick` / `standard` / `full` mit Zeitbudget; Worker-Pakete für QA und Renders.
- **Bericht als Gate:** `qa`, `clips`, `type_bounds`, Dreiecke, Bones vor und nach der Änderung vergleichen – ohne
  gewollte Modelländerung identisch (1.10: alles identisch mit 1.9, obwohl Materialien, Masken und Renders neu sind).
- `verify_robot.py` prüft unabhängig (seit 1.12 über 240 Prüfungen, davon 1.10: Skin-Daten, Masken mit Pixeln, Zonen
  überlappungsfrei, Gesichter, Flipbook, Icons, Bögen); `viewer/test_viewer.sh` prüft Migrationen, unbekannte IDs,
  Round Trip und Export.

## 14. Checkliste für den Cubegen-Modell-Editor

- [ ] Looks/Paletten als versionierte Datendatei mit Loader-Prüfung und Migration
- [ ] Farbvarianten aus einmal gebackenen Gewichtskarten zusammensetzen, Gegenprobe im Volllauf
- [ ] Maskenkanäle (Zonen, Abzeichen, Leuchtlinien) statt Textursatz je Variante; Formel mit Helligkeitsverhältnis
- [ ] Formvarianten (facettiert, Voxel, Lochung) als Ableitung mit gleichem Atlas und gleicher QA
- [ ] Lackzonen über benannte Teile → Flächenattribut → Attribute-Node
- [ ] Decal-Anker je Modellfamilie, Decal-Bibliothek per ImageMagick
- [ ] Gemeinsame Teilgraphen in Materialien (Knotenzahl messen)
- [ ] Präsentationskarten mit Dreiviertel-Kamera, Randlicht, Bloom nur auf Emission; Silhouetten aus Alpha
- [ ] Icons je Modell/Variante mit Lesbarkeitsprüfung
- [ ] Vorschau-App: Daten-Texturen roh laden, Uniforms packen, A/B-Vergleich, GIF/MP4-Export
- [ ] Bericht-Gate: Geometrie-/QA-Kennzahlen vor und nach jeder Änderung vergleichen
- [ ] Unity-Testprojekt je Generator: Import, Blickrichtung, Materialien, Screenshots automatisch (Muster: `unity/`)
- [ ] Verwitterung als normierte Felder mit Regler (Rost, Moos, Frost, Schmutz) und Werkstoff-Muster mit Grundfarbe
- [ ] Anbauteile: jede Variante in einer geprüften Kombination, Anschmiegen per Strahl, Emitter als Daten
- [ ] Nachschwingen gebacken (Feder je Knochen, Loops geschlossen), Requisiten per Matrix-Kette
- [ ] Effektmaterialien (Glas, Hologramm …) als Laufzeit-Effekt mit Zielbeschreibung; Masken für die Engine packen
- [ ] LOD-Stufen und Kollisionsformen automatisch erzeugen und in der Engine prüfen
- [ ] Prüfergebnisse cachen (Eingaben + Prüfcode im Schlüssel), Bilder nach Szenen-Abdruck, Prüfer parallel, Engine-Prüfung in einem Prozess

## 15. Proportionen und Körperformen (1.14)

- **Umformen statt neu modellieren:** Die Teile entstehen im alten Maß, danach formt eine Matrix je Knochen sie um
  (`PROPORTIONS`: Achsen-Skalierung um einen Drehpunkt oder „radial“ = dicker um die Knochenachse). Gelenke bleiben,
  also bleiben alle Clips, IK-Ziele und Bodenkontakte gültig; was am Kopf und Rumpf hängt (Sockel, Federknochen,
  Emitter, Augen-UVs), wandert mit derselben Matrix. Varianten können eine eigene Matrix haben (`SHAPE_VARIANTS`).
- **Breiter geht nur, wo die Clips Platz lassen:** Überall, wo Arme am Kopf vorbeigehen, kostet Breite Kollisionen
  (Schulterpanzer, Hand am Kinn, Leiter). Höhe ist billig, Tiefe am Rumpf trifft die Unterarme, Tiefe am Oberschenkel
  die Ferse. Die QA aller Typen nach jedem Schritt zeigt, welcher Faktor zu viel ist.
- **Falle Euler-Winkel:** Bei XYZ-Euler dreht der Abspreiz-Kanal einen nach vorn gestreckten Arm um seine eigene
  Achse; der Ellbogen bewegt sich nicht. Nach außen führt dann der dritte Kanal.
- **Formen als Geometrie mit gleichem Rig und gleichem Atlas:** Eine Form-Kopie jedes Teil-Objekts behält Vertex-
  Gruppen (gleiche Reihenfolge!), Materialien und UVs – so passt jeder Skin, jede Maske und jeder Clip ohne neue
  Texturen. Die UV-Ebene muss wie im Original heißen (`UVMap`): Augen- und Normal-Map-Knoten greifen über den Namen zu,
  sonst bleiben die Augen schwarz.
- **Voxel ohne Kollisionsfolgen:** Zellen nur zählen, wenn ihr Mittelpunkt eine halbe Zelle tief im Teil liegt –
  dann liegt jeder Würfel innerhalb der Grundform, und die Kollisionsfreiheit der Grundform überträgt sich. Bei einem
  Viertel ragten Brust- und Kugelwürfel bis 7 mm heraus und trafen sich in 67 von 69 Clips. Würfelhaufen sind nicht
  drehinvariant wie eine Kugel: Gelenkkugeln bleiben glatt, ebenso Finger und dünne Teile (sonst ragt ein ganzer
  Würfel heraus). Jede Würfelseite nimmt UV und Material des nächsten Originalpunkts (baryzentrisch, bei entarteten
  Dreiecken die nächste Ecke) – ein Texel je Seite ergibt den Pixel-Look.
- **Lochung als Formel statt Textur:** Raster im UV-Raum, Abstand aus der Texeldichte (Fläche ÷ UV-Fläche, nur
  Atlasflächen – Augen und Display haben feste UVs über eine ganze Atlaszelle und verfälschen sonst die Dichte um das
  Neunfache). Gleiche Formel in Blender (Mathe-Knoten), SceneKit (`discard_fragment`) und Unity (`clip`), Innenseiten
  sichtbar (beidseitig rendern).
- **Sichtbarkeit testen:** Snapshots sind deterministisch (derselbe Look: 0 Pixel Unterschied); ein Formtest vergleicht
  Skin mit und ohne Form – schon 55 Pixel (Kabel) sind ein sicherer Befund.

**Für Cubegen:** Formvarianten als Ableitung (Facetten, Voxel, Lochung) eines gemeinsamen Modells mit gemeinsamem Atlas;
Voxel konservativ (innen) rastern, Gelenke/Kleinteile glatt lassen; Variantengeometrie mit derselben QA wie das Original.

## 16. Atlas packen (1.15)

- **Belegung messen:** Fläche aller UV-Polygone der Atlas-Materialien (ohne feste UVs wie Augen und Display) –
  Smart UV Project allein belegte 18 %, obwohl die Inseln das ganze Quadrat überspannen.
- **Nachpacken:** `pack_islands(shape_method="CONCAVE", rotate=True, rotate_method="ANY", scale=True)` nach dem
  Abwickeln. Bei Tausenden kleiner Inseln bestimmt der Inselabstand die Belegung (0,003 → 0,002: 41 → 51 %), der
  Abwicklungswinkel kaum.
- **Ränder an den Abstand koppeln:** Bakes in mehreren Durchgängen (Varianten) dürfen höchstens den halben Abstand
  dilatieren, sonst überschreiben sie Nachbarinseln anderer Durchgänge; ein einziger Durchgang über alle Teile darf
  großzügig dilatieren (er füllt nur freie Pixel).

**Für Cubegen:** Atlas-Belegung als Kennzahl im Bericht; nach Smart UV Project konkav nachpacken; Bake-Ränder aus
dem Inselabstand ableiten.

## 17. Schlank und reproduzierbar (1.17)

- **Ausgaben nach Inhalt cachen:** FBX, Viewer-Daten und Clip-Daten hängen an einem Abdruck aus Meshes (kanonische
  Reihenfolge, UVs je Ecke, Materialien, Bilder), Rig, Aktionen und dem Quelltext der schreibenden Funktionen. Ist er
  gleich und liegen die Dateien in der erwarteten Größe vor, wird nichts geschrieben. Der Abdruck muss alles sehen,
  was die Datei bestimmt – darum stehen die Funktionsnamen in einer Liste (`EXPORT_CODE_PARTS`, `CLIP_CODE_PARTS`).
- **Falle: Decimate hängt an der Element-Reihenfolge.** Die Facetten-Form war in Haupt- und Worker-Prozess
  verschieden, obwohl die Eingabe gleich aussah: Blender legte Ecken und Flächen je Prozess anders ab, „Collapse“
  arbeitet sie der Reihe nach ab. Vor jeder solchen Operation eine kanonische Kopie bauen (Ecken nach Position und
  Gewichten sortiert, Flächen nach ihren Ecken) – danach ist das Ergebnis bitgleich.
- **Falle: neue BMesh-Elemente haben keinen gültigen Index.** Nach dem Neuaufbau trugen alle Flächen denselben
  `index`; Inselsuche und Dreieckstabelle über `face.index` fassten ganze Objekte zusammen (beide Füße als eine
  Insel mit der Gruppe `Foot_R`). In der Ruhepose unsichtbar, erst die QA der Clips fand es. Nach dem Aufbau
  `index_update()` und `ensure_lookup_table()`.
- **Innen-Test robust:** drei leicht schräge Strahlen mit Mehrheitsentscheid statt eines achsparallelen, der bei
  rasterförmig gebauten Teilen genau Kanten und Ecken streifen kann.
- **Vereinfachen ohne Herausragen:** Decimate „Collapse“ setzt zusammengefasste Punkte an das Quadrik-Optimum, auch
  ein paar Millimeter außerhalb des Teils. Punkte, die mehr als 2 mm herausragen, auf diesen Abstand zur Oberfläche
  ihres Teils (gleiche Knochengruppe) zurückziehen (ganz auf die Fläche gezogen, verlieren Gelenke ihren kleinen
  Überlapp: die Hand löste sich vom Unterarm) – wie der Voxel-Einzug hält das die kollisionsfreien Clips der Grundform gültig. Auch die
  Bezugsfläche dafür kanonisch aufbauen: Liegt ein Punkt genau über einer Kante, entscheidet sonst die
  Flächenreihenfolge des Prozesses, welche Normale „außen“ bestimmt.
- **Gleich Aussehendes zusammenfassen:** Voxel-Seiten gleicher Gruppe, gleichen Materials und gleicher Richtung
  werden zu Rechtecken verschmolzen, wenn die Texel aller Karten (Farbe, Masken, Werkstoff, gestuft) gleich sind –
  das Bild bleibt gleich, die Dreiecke sinken um ein Drittel (17 136 → 11 348).
- **Lieferstand von Arbeitsdateien trennen:** Die 75 gemischten Skin-Karten brauchen nur die Vorschauen; sie liegen
  im Cache und werden nur bei geänderter Palette neu geschrieben. `Textures/` enthält nur, was ein Spiel lädt.
- **Zeiten im Band bewerten:** Last zu Beginn je Kern bestimmt die Toleranz (ruhig 30 %, leicht × 2,6, schwer gar
  nicht), Referenz sind nur ruhige Läufe derselben Stufe und Cache-Lage. Ein festes Budget meldete unter Last ständig
  „zu langsam“ und auf ruhiger Maschine echte Verlangsamungen zu spät.
- **Große Skripte aufteilen, ohne Verhalten zu ändern:** Module mit einem gemeinsamen Namensraum in fester
  Reihenfolge; eine Analyse der freien Namen je Modul (`symtable`) zeigt vorher jeden Verweis auf ein späteres Modul.
  Prüfstein sind die Caches: Hängt der Code-Anteil der Schlüssel am Quelltext der Funktionen, müssen nach dem Umbau
  alle Caches treffen – dann ist nichts verändert.

**Für Cubegen:** Exporte nach Inhalt cachen; vor Decimate und ähnlichen Operationen kanonisch sortieren; Innen-Tests
mit schrägen Strahlen; Voxel-Flächen verschmelzen; Laufzeiten im Lastband bewerten.

## 18. Clips im Spieltempo (1.19)

- **Schrittlänge folgt aus dem Tempo:** Ein gesetzter Fuß bewegt sich im In-place-Clip mit der Bodengeschwindigkeit
  rückwärts. Länge = Tempo × Kontaktzeit (`planted_stride(speed, frames)`); Neigung und Gierwinkel bleiben während des
  Kontakts konstant, sonst wandert der gemessene Mittelpunkt gegen den Kontaktpunkt. Mit dem kurzen Bein des Roboters
  (0,31 m bis zum Knöchel) heißt 5 m/s: 2,5 Frames Kontakt bei 0,42 m Schritt, 8 m/s: 1,5 Frames.
- **Mindestens zwei Frame-Schritte je Kontakt:** `measure_clips` mittelt über aufeinanderfolgende Kontakt-Frames und
  braucht mehr als drei Werte; bei 8 m/s hat ein Doppelschritt nur einen Frame-Schritt je Fuß, deshalb trägt
  SprintFull zwei Doppelschritte in einer Schleife. Die Prüfung misst jeden einzelnen Frame-Schritt (±3 %), nicht nur
  den Median.
- **Kleine Beine falten schnell:** Bei tiefem Becken kommt die Ferse im Schwung an den Oberschenkel. Hilfen in dieser
  Reihenfolge: Zehen früh im Schwung anheben (die Ferse sinkt), das Becken wenig wippen (der tiefste Punkt fiel mit
  dem durchschwingenden Bein zusammen), Becken leicht nach hinten kippen, den Fuß früh nach vorn bringen.
- **Seitlich laufen ohne Kreuzen:** Hüfte 70° in die Laufrichtung drehen, Beine auf zwei Spuren in Laufrichtung, Brust
  und Kopf zurückdrehen. Ein kürzerer Kontakt (2,2 statt 2,5 Frames) gibt einen kürzeren Schritt und lässt das Becken
  höher.
- **Rollen über die Hülle:** Der Körper wird je Frame angehoben, bis die tiefste Ecke aller Teile (alle Varianten,
  Formen) 2 mm über dem Boden liegt – gelöst mit dem echten Solver (`pose_matrix`) über die Ruhepose-Punkte je Knochen.
  Gesetzte Füße bleiben Weltpunkte, angezogene Füße reiten auf dem Becken; reicht das Bein nicht mehr, verlässt der
  Fuß den Boden (Ziel auf die Beinlänge kürzen statt IK-Klemmen). Große Aufsätze würden den Standard-Roboter weit
  anheben: sie sind aus der Hülle genommen und als benannte Bodenausnahme erlaubt.
- **Getragenes als Daten:** Die Hände halten Kiste und Rad an festen Punkten des Charakterraums; das Spiel hängt die
  Kiste an den Root, die Daten (`prop`) beschreiben sie, die Prüfung hält sie frei vom Körper.

**Für Cubegen:** Bewegte Teile (Räder, Ketten, Füße) mit Kontaktgeschwindigkeit = Fahrtempo animieren und jeden
Frame-Schritt prüfen; Bodenkontakt für Varianten aus der Hülle aller Varianten ableiten.

## 19. Standard-Oberfläche an der Spielwelt messen (1.20)

- **Gemessen im Spiel, nicht in der Vorschau:** Die Vorschauen leuchten den Roboter hell aus; die Station in Cube
  Escape ist dunkel und matt (Metallic 0, Smoothness 0,4–0,5). Die alte Standard-Oberfläche (Panzerung im Mittel
  Metallic 0,42, Smoothness 0,68 aus `T_Robot_U_MetalWear`) spiegelte dort die dunkle Umgebung, ein weißer Skin wirkte
  grau. Matt (Metallic 0,17, Smoothness 0,40) passt zur Welt.
- **Den Standard ändern, nicht die Masken:** Die gepackten Masken tragen Metall und Glätte für alle Oberflächen; eine
  Änderung dort hätte Glanz, Candy und Chrom mitgezogen. Die Oberfläche ist ein Faktor zur Laufzeit – also bekommt
  „Standard“ die Werte von „Matt“, und die Masken bleiben.
- **Alte Werte als neue ID anhängen:** Der frühere Glanz-Standard heißt „Lack“ und steht am Ende der Liste; keine ID
  wird umbenannt, gespeicherte Looks bleiben gültig (sie zeigen mit „Standard“ jetzt den matten Standard). „Matt“
  rückt eine Stufe weiter, damit beide IDs unterscheidbar bleiben. `verify_powdercoat()` rechnet die wirksamen Werte
  der Panzerung aus den Masken nach.

**Für Cubegen:** Standardmaterialien der Modelle im Zielumfeld (dunkle, matte Station) prüfen, nicht nur im hellen
Vorschaulicht; einen geänderten Standard nie still umdeuten, sondern den alten Wert als neue, wählbare ID behalten.

## 20. Geteilter Cache, Prüfplan, ruhige Maschine (1.20.2)

- **Der Cache gehört nicht in die Arbeitskopie:** Bis 1.20.1 lag er in `<out>/cache`; jeder neue Worktree begann
  leer und renderte rund 1 650 Vorschaubilder neu (alter Generator, frischer Worktree: `quick` 54 s, `standard`
  328 s, `full` 628 s). Jetzt liegt alles Inhaltsadressierte in `~/Library/Caches/RobotGen/cache`; in der Ausgabe
  bleibt nur, was zu ihr gehört (`outputs.json`, relativ). Auch die Exporte sind Einträge (`outputs/<Name>_<Schlüssel>`):
  ein neuer Checkout kopiert die FBX, statt sie neu zu schreiben. Kopien sind APFS-Klone (`clonefile`) – kein
  zusätzlicher Platz, sofort.
- **Gleichzeitig sicher:** Schreiben nur über Temp-Datei und Umbenennen (Ordner-Einträge werden fertig gebaut und
  dann umbenannt); kein Schritt löscht einen Eintrag, weil es einen neueren gibt (`unwrap()` behielt nur die neueste
  UV-Datei – im geteilten Cache hätte das den Cache eines anderen Checkouts geleert). Aufräumen nur nach Größe:
  am längsten unbenutzte Einträge zuerst, ein Treffer berührt den Eintrag, nichts aus den letzten 2 Stunden.
- **Falle: Pfade im Schlüssel.** Die Bake-Signatur enthielt den absoluten Pfad jeder Quelltextur – in einem anderen
  Checkout nie ein Treffer. Der Schlüssel nimmt jetzt nur den Dateinamen (der Inhalt steht ohnehin darin). Regel:
  Ein Schlüssel darf nichts enthalten, was sich zwischen zwei Arbeitskopien unterscheidet.
- **Falle: „gleich“ heißt bytegleich.** Ein neues Backen weicht leicht ab (Cycles; die Normal-Map zweier kalter Läufe
  unterscheidet sich in 14 500 Pixeln), ImageMagick schreibt Zeitstempel in die PNGs. Der Render-Abdruck hasht
  Bilder nach Inhalt – also hätte jeder Neu-Bake alle Vorschauen ungültig gemacht. Darum übernimmt die Migration die
  Quelltexturen, die statischen Texturen und die Skins des Haupt-Checkouts bytegleich, und der alte, pfadhaltige
  Bake-Schlüssel wird für übernommene Ausgaben nachgebaut (`legacy_sources.json`). Ergebnis: der erste Volllauf
  einer frischen Ausgabe nimmt alle 1 649 Bilder aus dem Cache. Auch `make_textures.py` bleibt dafür unverändert –
  sein Hash ist der Schlüssel der Quelltexturen.
- **Folge des nicht deterministischen Backens:** Die Voxel-Form liest die gebackenen Karten; zwei kalte Bakes geben
  leicht andere Voxel-Meshes (Bericht `forms`, QA von `Form_Voxel` in der dritten Nachkommastelle). Der geteilte Cache
  macht das seltener (es wird nur noch einmal je Modelländerung gebacken), behebt es aber nicht.
- **Prüfen nach Diff statt nach Gefühl:** `scripts/testplan.py` ordnet jede geänderte Datei einem Bereich zu (Tabelle
  `RULES`) und jedem Bereich einen Weg und Zusatzprüfungen (`AREAS`): Doku nichts, Daten der schnelle Weg, Modell-
  und Cache-Code der volle Ablauf, Viewer und Unity ihre eigenen Prüfungen. `--run` führt aus, vergleicht die
  Berichte und prüft die Cache-Treffer.
- **Last vor dem Messen prüfen:** `scripts/quiet.py` zählt Unity, Blender und Generatorläufe anderer Agenten-Sitzungen
  (nicht die eigenen, nicht die des Nutzers) und die Last – Last zählt nur, wenn Befehle anderer Sitzungen die CPU
  nutzen (sonst stammt sie vom eigenen vorigen Schritt). Ist die Maschine belegt, startet sich der Volllauf über
  `pause_sessions.py` neu und pausiert die anderen genau so lange. **Falle:** zwei Läufe, die sich gegenseitig
  pausieren, halten sich für immer an – darum nie pausieren, solange eine andere Pause läuft (Sperre, Prozess, Status).

- **Erst die Sperre, dann die Pause (1.20.3):** Cube Escape (0.59) und Cubegen (0.25) stellen schwere Läufe an
  einer rechnerweiten Sperre an (`~/Library/Caches/CubeEscape/heavy-run.lock`, exklusives `flock`, Halterzeile als
  JSON); der Generator tut es mit `full`, `check_output.sh` und `run_testbed.sh` (`scripts/heavy_lock.py`). Wer die
  Sperre hält, sieht die anderen schweren Läufe nur noch wartend – `quiet.py` zählt dann nur Prozesse, die wirklich
  rechnen (über 5 % CPU), und pausiert nur für Last, die nicht ansteht. **Fallen:** Ein vererbter Dateideskriptor
  hielte die Sperre über das Ende des Laufs hinaus (ein Hintergrundkind wie ein Worker) – darum `O_CLOEXEC`; ein
  schwerer Schritt im schweren Schritt würde auf seinen eigenen Elternprozess warten – darum `CE_HEAVY_HELD=1` für
  die Kinder des Halters; ein Signal für den Wartenden geht an den Befehl weiter, sonst wäre die Sperre frei, während
  der Befehl noch läuft. Seit 1.20.5 läuft der Befehl in einer eigenen Prozessgruppe (`start_new_session`): Signale gehen
  an die ganze Gruppe, und die Sperre bleibt, bis die Gruppe leer ist – vorher war sie frei, sobald der direkte
  Kindprozess endete, auch wenn darunter noch Blender lief (etwa nach dem Tod von `check_output.sh`). Seit 1.20.6 ist
  `full` immer ein Kind (`quiet.run`), auch auf ruhiger Maschine: Vorher lief der Volllauf dort im Blender weiter, der die
  Sperre selbst hielt – ein TERM beendete ihn sofort (Standardaktion), die Sperre war frei, während seine Worker noch
  rechneten. Kosten: ein zusätzlicher Blender-Start (≈ 0,6 s). **Regel:** Wer die Sperre hält, startet den schweren
  Schritt über `run_forwarding` und wartet nur – nie im Halter selbst rechnen.

**Für Cubegen:** Caches in einen Ordner außerhalb der Arbeitskopie legen, Schlüssel ohne Pfade, Kopien als Klone,
atomar schreiben, nach Größe und letzter Nutzung aufräumen; bei nicht deterministischen Schritten (Backen, Bild-Werkzeuge
mit Zeitstempeln) die Ergebnisse bytegleich weiterreichen statt neu zu erzeugen.

## 21. Betriebsdetails aus AGENTS.md (1.20.4)

`AGENTS.md` hält nur die Regeln, die jede Aufgabe braucht; die Hintergründe dazu stehen hier.

- **Cloud-Sitzungen (claude.ai/code):** Der SessionStart-Hook `.claude/hooks/session-start.sh` (lokal tut er nichts)
  installiert Blender 5.2 LTS als Python-Modul von PyPI (`bpy`; download.blender.org ist gesperrt) hinter dem Befehl
  `blender` (`.claude/hooks/blender_cli.py`, auch für die Worker), ImageMagick 6 hinter einem kleinen `magick` und
  Arial/Hiragino als Liberation Sans/IPA Gothic. Grenzen: headless auf der CPU; Texturen und Zeiten sind nicht mit dem
  Mac vergleichbar (anderes ImageMagick, andere Schriften, wenige Kerne) – `qa`/`clips`/`type_bounds` nur gegen einen
  Cloud-Lauf vergleichen. Viewer (Swift), Unity-Testpaket und Blender-MCP gibt es nur lokal; der Lieferstand (`full`)
  entsteht dort.
- **Zeitband:** Jeder Lauf schreibt Schrittzeiten, Systemlast und Worker-Statistik nach `<out>/logs/robot_report.json`
  und bewertet die Zeit (`timings.band`, Referenzen in `<out>/cache/timings.json`) gegen den Median ruhiger Läufe
  derselben Stufe und Cache-Lage, mit Toleranz je Last (ruhig 30 %, leicht × 2,6, schwer = nicht bewertet). Nur ruhige
  Läufe werden Referenz. Ein „SLOWER“ auf ruhiger Maschine ist ein Befund; unter Last nichts schließen, sondern ruhig
  nachmessen. Das Budget (`TIERS`) bleibt Zielwert.
- **Pause durch `full` und `check_output.sh` (`scripts/quiet.py`, seit 1.20.2):** Laufen Unity, Blender oder der Generator
  anderer Sitzungen oder liegt die Last über 0,3 je Kern, während Befehle anderer Sitzungen die CPU nutzen, starten sich
  die Läufe über `pause_sessions.py` (Cube Escape, `.agents/skills/ce-testing/scripts/`) neu; das Werkzeug hält nur die
  Befehle anderer Claude-/Codex-Sitzungen an (SIGSTOP), setzt sie danach immer fort (SIGCONT) und lässt die Sitzungen
  selbst, den Unity-Editor und das Blender des Nutzers unberührt. Der Bericht nennt es (`robot_report.json` › `quiet`,
  letzte Zeile von `check_output.sh`). Pausiert schon eine andere Sitzung, laufen sie normal (zwei Pausen hielten sich
  gegenseitig für immer an). `ROBOGEN_NO_PAUSE=1` schaltet es ab; fehlt das Werkzeug, laufen sie mit Warnung normal. Ein
  pausierter Befehl kann in die Zeitgrenze seines Werkzeugs laufen – dann führt die andere Sitzung ihn erneut aus.
- **Schwerer-Lauf-Sperre (seit 1.20.3, gemeinsam mit Cube Escape 0.59 und Cubegen 0.25):** exklusives `flock` auf
  `~/Library/Caches/CubeEscape/heavy-run.lock` (`scripts/heavy_lock.py`); der Halter schreibt eine JSON-Zeile
  `pid`/`what`/`checkout`/`since`, Wartende nennen sie. **Erst die Sperre, dann die Pause:** andere schwere Läufe warten
  dann an der Sperre und rechnen nicht; `quiet.py` pausiert nur noch, wenn danach Last übrig bleibt, die nicht an der
  Sperre wartet. Kinder des Halters bekommen `CE_HEAVY_HELD=1` und warten nicht erneut (`run_testbed.sh` in
  `check_output.sh`); `CE_HEAVY_LOCK=0` überspringt die Sperre; der Dateideskriptor wird nie vererbt (`O_CLOEXEC`).
  `python3 scripts/heavy_lock.py status` zeigt den Halter; der Bericht nennt die Sperre. Den Vertrag nur zusammen mit
  den beiden anderen Repos ändern.
- **Prüfablauf, Schritt 4 (`quick` zweimal):** Beim zweiten Lauf müssen UV-Cache und Bake-Cache treffen (Log „UV cache
  hit“, „bake cache hit“); dank des geteilten Caches meist schon beim ersten Lauf eines neuen Worktrees. QA-Ergebnisse
  kommen aus `qa/`, solange Mesh, Rig, Clip und QA-Code gleich sind („QA cache: N clip results reused“); nach
  Änderungen an der QA selbst reicht das (der Code steckt im Schlüssel), `--no-cache` prüft alles neu. Vorschaubilder
  kommen aus `render/`, solange die Szene beim Rendern gleich aussieht („render cache: N images reused“); Fehlersuche mit
  `ROBOT_KEEP_WORKER_LOGS=1` und `ROBOT_RENDER_DIGEST_DUMP` (§ 12). Ohne Änderung an Modell, Rig, Clips und Export-Code
  schreibt der zweite Lauf keine FBX und keine Viewer-Daten („unchanged outputs kept: main, modular, forms, viewer“)
  und misst keine Clips („85 clips from the cache“); Fehlersuche mit `ROBOT_OUTPUT_DIGEST_DUMP=<Datei>`.
- **Release (`scripts/release.py`, seit 1.20.3):** Prüft vor jeder Änderung: beide Checkouts sauber, Haupt-Checkout auf
  `main`, nur Fast-Forward (der Branch enthält `main` und `origin/main`, sonst erst `git merge main` im Branch), Tag und
  `release/<Version>` noch nicht vergeben, `CHANGELOG.md` hat `## <Version>`, keine Datei über 100 MB, keine
  Zugangsdaten (Zeile mit `release-check: allow` ist bewusst ausgenommen). Mit `--apply`: `merge --ff-only` im
  Haupt-Checkout, annotierter Tag `v<Version>` (Text `-m`/`-F`, sonst der neueste Commit-Betreff mit der Version),
  Branch `release/<Version>`, **ein** atomarer Push von `main`, Tag und Release-Branch ohne `--force`, Links im Bericht.
  Bricht ein Schritt ab, setzt derselbe Befehl fort.
- **Cache im Detail (zu den Cache-Regeln, § 20):** Unterordner je Art: `uv`, `ao`, `bake`, `textures`, `skins`, `source`,
  `render`, `qa`, `clips`, `outputs` (FBX, Formen, Viewer-Daten); Haupt-Checkout, Worktrees und Nachtläufe teilen ihn,
  er überlebt gelöschte Worktrees (Linux: `$XDG_CACHE_HOME/RobotGen/cache`). In `<out>/cache` bleibt nur
  Checkout-Zustand: `outputs.json` (Pfade relativ zur Ausgabe), `timings.json` und die Arbeitskopien der Vorschau-Skins
  (`skin_textures`; in `Textures/` liegt nur der Standard-Skin). `robocache.prune` läuft am Ende eines Laufs
  (`ROBOGEN_CACHE_LIMIT_GB`, Standard 6): am längsten nicht benutzte Einträge zuerst, nichts aus den letzten 2 Stunden,
  ein Treffer berührt den Eintrag. Schlüssel: Geometrie in kanonischer Ecken-Reihenfolge (Blenders Flächenreihenfolge und
  Insel-Packen sind nicht deterministisch), Materialien als Node-Signatur, dazu Quelltexturen und Einstellungen; Bilder
  nach Inhalt oder Dateiname, nie mit Ordner. Beim ersten Lauf übernimmt `robocache.prepare` alte Caches (`<out>/cache`
  bis 1.20.1): die eigene Ausgabe verschiebt er, die Ausgaben des Haupt-Checkouts (`output`, `_quick`, `_standard`) klont
  er einmal (`migrated.json`) samt Quelltexturen, statischen Texturen, Skins und Exporten – bytegleich; alte,
  pfadhaltige Bake-Schlüssel erkennt `bake_textures` über `legacy_sources.json` (§ 20). Code im Schlüssel (`QA_CODE_PARTS`,
  `CLIP_CODE_PARTS`, `EXPORT_CODE_PARTS`) wird per Funktionsname über alle `robogen`-Module gesucht (`code_source`).
  ImageMagick-Texturen hängen am Skript-Hash von `make_textures.py` und an der ImageMagick-Version.
- **Plugins im Roboter-Generator (Claude Code; gemeinsam mit Cube Escape und Cubegen):**

  | Plugin | Einsatz |
  |---|---|
  | Blender-MCP | laufendes Blender des Nutzers als Prüfstand: `CubeEscape_Robot.blend` und Exporte laden, nachmessen, Ansichten rendern – in eigener Szene, nichts speichern |
  | `quick-gate-python` | Ruff (und pytest, wo es reine Python-Teile gibt) für `scripts/`; Pyright kennt `bpy` nicht. Verbindlich bleiben die Teststufen |
  | `code-review` | Slash-Befehl `code-review` vor jedem Release auf `main` |
  | `github` | Pull Requests, Issues, Release-Branches und Tags auf GitHub (oder `gh`) |
  | `engineering` | `debug`, `testing-strategy`, `documentation` |
  | `design` | `design-critique` für Vorschauen und den Viewer |
  | `hookify` | Repo-eigene Regeln als `.claude/hookify.<name>.local.md` (eingecheckt) |
  | `ce-shared` | gemeinsames Plugin (privates GitHub-Repo ffunkfabian-hash/ce-agent-tools, eingetragen in `.claude/settings.json`): gemeinsame Regeln und die Werkzeug-Profile der Subagents (`.claude/agent-guard.json`; Befehle und Schreibpfade von `rg-test-runner` und `rg-doc-keeper`) |
  | `unity`, `unity-perf` | erst, wenn der Roboter in Cube Escape eingebaut wird – dann gelten die Regeln dort |

  Die Hook-Regeln aus `AGENTS.md` (Plugin `ce-shared` und hookify) stehen in `.claude/hookify.*.local.md` und im
  Plugin-Repo (mit Tests).
