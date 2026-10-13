"""The robot generator's steps (1.17 Slimline: split from generate_robot.py, behaviour unchanged).

The modules form one shared namespace in this order; each starts with `from robogen.<previous> import *` and ends
with an `__all__` of everything it holds, so later modules see every name of the earlier ones (also `_private`
helpers). A module may only use names of itself and earlier modules - a name from a later module is unknown at
import time. Mutable state is shared as containers (lists, dicts); a plain value set at run time is written into
its own module (main(): robogen.preview.PREVIEW_SAMPLE_SCALE).

    core       imports, test tiers, arguments, logging, steps, scene helpers, cache digests
    layout     joints, proportions (reshape), slots, variants, robot types, part registry
    materials  procedural materials, skins, decals, eyes, display
    parts      geometry helpers (boxes, prisms, cylinders, tubes ...)
    body       the robot's geometry (core body, slot variants, accessories, form parts)
    assembly   groups, connectivity, normals, UV unwrap
    textures   baking, skin composition, baked material
    rig        armature and binding
    forms      faceted, voxel, perforated body forms
    motion     pose helpers, solver, gait helpers
    clips      the clips, categories, per-clip game data
    qa         clip QA and clip measurements
    preview    renders, render cache, sheets, cards, icons, GIFs
    export     FBX and LODs
    pipeline   work packages, workers, output caches
    config     robot_config.json, viewer config, looks, report

Code digests of the caches (code_source) find a function in whichever module defines it, so moving a function
between modules keeps every cache valid; changing its source invalidates it as before.
"""
