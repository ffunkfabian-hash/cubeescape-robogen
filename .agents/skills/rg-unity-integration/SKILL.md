---
name: rg-unity-integration
description: Roboter-Generator – was das Spiel Cube Escape aus der Generator-Ausgabe übernimmt und wie (FBX, Typen, LOD, gepackte Unity-Masken, RobotBody-Shader, Look-Format mit Migration, Clip-Daten mit Schritten/Ereignissen/Servo, Treffer-Kapseln, Emitter, Federknochen, Typ-Emotes, Spezialmaterialien). Laden beim Planen oder Umsetzen der Spielversionen mit dem Roboter (Spieler-Roboter, Werkstatt, Spektrum, NPC-Roboter, Roboter-Gefahren) und bei Fragen wie "wie kommt der Roboter ins Spiel", "welche Daten braucht Unity", "robot_config.json".
---

# rg-unity-integration – vom Generator ins Spiel

Dieses Repo ändert Cube Escape nie. Der Skill beschreibt die **Übergabe**; umgesetzt wird im Spiel-Repo mit dessen
Skills (`ce-player-portals`, `ce-editor-assets`, `ce-world-story`, `ce-testing`). Vorlagen, die schon in Unity laufen:
`unity/RobotTestbed` (siehe `rg-unity-testbed`). Langform: `README.md` › Unity-Integration, Clip-Daten.

## Was übergeben wird (Generator-Ausgabe `output/`)

| Datei | Inhalt | Spiel nutzt es für |
|---|---|---|
| `CubeEscape_Robot.fbx` | Standard-Look, Rig, alle Takes | Animator-Clips (alle Typen teilen das Rig) |
| `Types/CubeEscape_Robot_<Typ>.fbx` | je Robotertyp 1 Mesh, ohne Takes | feste Looks, NPCs |
| `LOD/…_LOD1/2.fbx`, `Types/LOD/…` | ≈ 6 000 / 2 500 Dreiecke, gleiches Rig | `LODGroup`, Menge/Entfernung |
| `CubeEscape_Robot_Modular.fbx` | alle Teile einzeln | frei kombinierbare Looks (Werkstatt) |
| `Textures/T_Robot_BaseColor/Emission/Normal/Occlusion.png` | Classic-Skin | Grundtexturen |
| `Textures/Unity/T_Robot_U_*.png` | Masken gepackt, ≤ 1024² | `RobotBody`-Shader (Belegung: `unityTextures`) |
| `robot_config.json` | Slots, Typen, Skins, Abzeichen …, `clips`, `hitCapsules`, `emitters`, `springBones`, `specials` | alles Datengetriebene |
| `Looks/*.robotlook.json`, `robot_look.schema.json` | Look-Format (Version: `lookFormat`) | Spielstand, Werkstatt |

## Regeln für die Übernahme

- **Import:** Generic Rig, Root `Root`; Bake Into Pose außer Drehungen/ClimbUp (Liste: `TestbedImport.RootRotation/
  RootPosition`); Loop aus `clips.<Name>.loop`. Masken linear, kein Alpha als Transparenz.
- **Material:** Im Spiel zeichnet die Shader-Familie `CubeEscape/Surface` mit dem Rezept ROBOT (Cube Escape 0.57,
  `Docs/MATERIALS.md` dort); `RobotBody` im Testbett bleibt die Bildreferenz. Werte als **Materialinstanz je Roboter**
  (nicht je Look), nur bei einer Look-Änderung gesetzt; das Glühen (`SetPower`) setzt eine Zahl ohne Allokation.
  Kein `MaterialPropertyBlock` – er nimmt Renderer aus SRP Batcher und GPU Resident Drawer. Farben linear `SetVector`.
- **Look im Spielstand:** Format und Migration 1:1 aus `RobotLook.cs` (gleiche Fixtures wie `viewer/tests/`);
  unbekannte IDs → Standard mit Warnung, neuere Version ablehnen. Spiel-Migration zusätzlich nach Spielregeln
  (`SaveData.CurrentVersion`, neue Fixture).
- **Clip-Daten** (`clips.<Name>`): `naturalSpeed` (Animator-Tempo = Geschwindigkeit / naturalSpeed), `footsteps`,
  `events`, `servo` ([Frame, Gruppe, Stärke 0–1, Dauer]) → `GameEvents`, Klang macht `GameFeedback`;
  `hideHeldItem` blendet Hand-Gegenstände aus; `expression`/`display`/`emission` steuern Gesicht und Leuchten.
  Keine Allokation pro Bild (Spielregel 33): Ereignisse beim Laden in Arrays umsetzen.
- **Spieltempo (1.19):** Jog/StrafeLeft/StrafeRight/RunBack/CarryWalk 5 m/s, SprintFull 8, Wade 4, CrawlFast 2,4 m/s
  laufen bei Tempo 1 rutschfrei; die Kiste (`prop.type` `crate`) hängt am Root (Hände greifen feste Punkte), das
  Ventilrad (`wheel`) steht vor dem Roboter. LandRoll: Kopf- und Rückenaufsätze dürfen den Boden streifen.
- **Oberflächen (1.20):** `finishes` aus `robot_config.json` lesen, nie im Spiel festschreiben. „Standard“ ist matt
  (×1,9 / −0,25, passend zur Station), „Lack“ der frühere Glanz-Standard; ein Werkstoff bringt bei „Standard“ seine
  eigene Oberfläche. Ein im Spiel erzwungenes „Matt“ ist seit 1.20 nicht mehr nötig (und eine Stufe matter).
- **Treffer-Kapseln** (`hitCapsules`): Punkte im Charakterraum der Ruhepose → einmal per Bindpose in den
  Knochenraum, dann als `CapsuleCollider` am Knochen (Vorlage: `TestbedPipeline.CheckCapsules`).
- **Emitter** (`emitters`): Partikelsystem am genannten Socket, nur sichtbar mit dem Aufsatz (`slot`/`variant`).
- **Federknochen** (`springBones`): sind in die Clips gebacken – keine Laufzeit-Physik nötig.
- **Typ-Emotes** (`robotTypes[].emote`): nur mit diesem Typ bzw. dessen Kopf-/Hand-Aufsatz abspielen.
- **Spezialmaterialien** (`specials`): je Eintrag `urp` = Beschreibung des späteren URP-Materials; bis dahin zeigt
  der Skin seine Palette (Fallback). Glas/Hologramm brauchen Transparenz (eigene Queue, Tiefe zuerst).
- **Koordinaten:** alles im Charakterraum `[rechts, oben, vorn]` = Unity-Local-Space; der Roboter schaut nach +Z.

## Reihenfolge (Fahrplan, Spielversionen)

Spieler-Roboter (FBX, Animator, Shader, Clip-Ereignisse, Kapseln) → Werkstatt (Look im Spielstand, modulare Teile)
→ Spektrum (Spezialmaterialien, Formvarianten) → NPC-Roboter → Roboter-Gefahren. Führend: Claude Doc
„Fahrplan Roboter-Generator“ (`docs/FAHRPLAN.md` ist das Abbild).

## Vor der Übergabe prüfen

1. Volllauf + `verify_robot.py` grün, `unity/run_testbed.sh` grün (`rg-unity-testbed`).
2. Vorlagen im Testbett entsprechen dem, was das Spiel übernehmen soll (Shader, `RobotLook`, `RobotAppearance`).
3. Änderungen an Formaten stehen im CHANGELOG (Look-Version, Clip-Felder, neue Konfigurationsschlüssel).
