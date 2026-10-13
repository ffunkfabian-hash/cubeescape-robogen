---
name: rg-unity-testbed
description: Roboter-Generator – Unity-Testpaket (unity/RobotTestbed, unity/run_testbed.sh) ausführen, Befunde lesen, neue Prüfungen oder EditMode-Tests ergänzen, Shader/Laufzeitvorlagen (RobotBody, RobotLook, RobotAppearance) ändern und Läufe beschleunigen. Laden bei jeder Änderung an Export, Clips, Texturen, Masken, Looks oder den Unity-Vorlagen sowie bei "Unity", "Testbett", "testbed", "run_testbed", "im Unity prüfen", "Unity schneller".
---

# rg-unity-testbed – Generator-Ausgabe in Unity prüfen

Ein eigenes, sonst leeres URP-Projekt prüft jede Generator-Ausgabe in Unity. **Nie das Spiel Cube Escape** – dieses
Repo verändert es nicht (`AGENTS.md`). Langform: `README.md` › Unity-Testpaket, Lehren: `docs/TECHNIK.md` › 9.

## Ausführen

```bash
unity/run_testbed.sh output/_standard     # Ausgabe einer Stufe; ohne Argument: output (Volllauf)
```

- Unity-Version und Pfad: `unity/RobotTestbed/ProjectSettings/ProjectVersion.txt`, sonst `UNITY_EDITOR=…`.
- **Ein** Unity-Start (seit 1.13): `TestbedPipeline.Run` kopiert nur geänderte Dateien nach `Assets/RobotImport`,
  importiert (parallel, Out-of-Process), baut Materialien, Prefabs, Animator, prüft, rendert und führt danach die
  EditMode-Tests im selben Prozess aus (`TestbedTestRun`, synchron). Ergebnis: `<out>/UnityTestbed/report.json`,
  `tests.xml`, `pipeline.log`, `shots/`, Bögen `unity_*.png`.
- Der Lauf scheitert bei `error CS` im Log – Unity rechnet im Batch sonst mit den **alten** Assemblies weiter.
- Läuft schon ein Unity auf dem Projekt (anderer Agent), bricht das Skript ab; nicht parallel starten.

## Neuer Worktree = schneller erster Lauf

`unity/RobotTestbed/Library` ist nicht versioniert. Im neuen Worktree vorher kopieren, sonst importiert Unity
minutenlang alles neu:

```bash
rsync -a <alter-worktree>/unity/RobotTestbed/Library/ unity/RobotTestbed/Library/
rsync -a <alter-worktree>/unity/RobotTestbed/Assets/RobotImport/ unity/RobotTestbed/Assets/RobotImport/   # optional
```

## Was wo liegt

| Datei | Aufgabe |
|---|---|
| `Editor/TestbedPipeline.cs` | Ablauf, Kopie, Materialien, Prefabs, alle `Check(...)` |
| `Editor/TestbedImport.cs` | AssetPostprocessor: FBX-Rig, Clips (Loop, Bake Into Pose aus `robot_config.json`), Texturen (Daten linear) |
| `Editor/TestbedShots.cs` | Screenshots in eine eigene RenderTexture |
| `Editor/TestbedTestRun.cs` | EditMode-Tests im Pipeline-Prozess, schreibt `tests.xml` |
| `Runtime/RobotLook.cs`, `RobotConfig.cs`, `Json.cs` | Look-Format mit Migration (wie `viewer/Sources/Look.swift`), Konfiguration |
| `Runtime/RobotAppearance.cs`, `RobotFace.cs` | MaterialPropertyBlocks, Augen/Display – Vorlagen fürs Spiel |
| `Shaders/RobotBody.shader`, `RobotScreen.shader` | URP-Shader; Formeln gleich `decorate_skin()` und Viewer |
| `Tests/Editor/RobotTestbedTests.cs` | EditMode-Tests; Fixtures aus `viewer/tests/` |

## Prüfung ergänzen

1. In `RunChecks` eine `static void CheckX(Prefabs p)` aufrufen; jede Aussage über `Check("bereich: was", ok, detail)`.
   Namen beginnen mit dem Bereich (`fbx:`, `clips:`, `pose:`, `lod:`, `capsules:` …); `detail` nennt Messwerte.
2. Schwellen gegen einen stabilen Bezug messen (z. B. Idle-Frame 0), nicht knapp über dem heutigen Wert.
3. Prefabs nie verändern: `PrefabUtility.InstantiatePrefab`, Instanz in `finally` mit `DestroyImmediate` löschen.
4. Neue Datei aus der Ausgabe? In `CopyOutput` (und bei Texturen in `Textures`) eintragen; sonst fehlt sie.
5. Neue `.cs`-Datei: Unity erzeugt beim ersten Lauf die `.meta` – mit committen (GUID).
6. Look-Format geändert: Fixture in `viewer/tests/` (nie alte ändern), Test in `RobotTestbedTests.cs`,
   dieselbe Migration in `RobotLook.cs` und `Look.swift`.

## Fallen (alle schon einmal passiert)

- **Blickrichtung:** Unity übernimmt Blenders Vorderseite als −Z, egal was im FBX-Kopf steht; der Export dreht die
  Armatur (`FBX_EXPORT_TURN`). Prüfung `fbx: robot looks +Z` nie abschwächen.
- Die FBX behält die Pose des zuletzt exportierten Takes → Höhen in Idle-Frame 0 messen, Kapseln über die
  **Bindposes** (`sharedMesh.bindposes`) in den Knochenraum rechnen.
- Farben linear per `SetVector` (nicht `SetColor`); Daten-Texturen `sRGB aus`, `Alpha is Transparency aus`.
- Konfiguration als `TextAsset` im Prefab halten, nicht geparst serialisieren.
- Zonen-ID (`T_Robot_U_Aux.g`) nur mit Punktfilter und Mip 0 lesen (`sampler_point_repeat`, `SAMPLE_TEXTURE2D_LOD`).
- Batch-Screenshots: eigene RenderTexture, zweimal rendern (die erste Aufnahme ist leer).

## Schneller machen

- Messen: `pipeline: … s` am Ende; Aufteilung im `pipeline.log` (`Asset Pipeline Refresh … Total`).
- Der Unity-Start (~10–15 s) ist der größte Block; deshalb läuft alles in **einem** Start. Keine zweite
  Unity-Instanz für Tests einführen.
- Unveränderte Dateien werden nicht kopiert (Größe + Zeit) und behalten ihren Import; die Library kopieren (oben).
- Parallele Last anderer Agenten (Unity-Shader-Compiler, Blender) verdoppelt Zeiten – `uptime` vorher ansehen.
- Größere Hebel, noch nicht umgesetzt: ein dauerhaft laufender Batch-Editor, der auf Aufträge wartet (spart den
  Start komplett, kostet dauerhaft RAM), oder Screenshots nur im Volllauf.

## Fertig, wenn

`run_testbed.sh` endet mit allen Prüfungen und Tests grün, kein `error CS`, Bögen angesehen (`unity_types.png`,
`unity_skins.png`). Ergebnis (Anzahl Prüfungen/Tests, Sekunden) in CHANGELOG und Abschlussbericht.
