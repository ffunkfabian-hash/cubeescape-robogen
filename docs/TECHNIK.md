# Techniken des Roboter-Generators – Übertrag auf Cubegen

Stand: Generator 1.13.3 „Spectral“. Diese Datei sammelt, **wie** der Generator arbeitet und was sich dabei bewährt
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
