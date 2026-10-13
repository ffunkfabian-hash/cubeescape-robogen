"""FBX export and LODs."""

from robogen.preview import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ----------------------------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------------------------


# Unity converts the declared FBX axes consistently: Blender's -Y (the robot's front) always ends up as Unity -Z, whatever
# axis_forward says (measured in the Unity testbed, 1.10.1). For the README promise "the robot looks +Z" the armature
# object is turned 180 degrees about Z for the export only; bones, animation and clip data stay as they are.
FBX_EXPORT_TURN = math.pi


# Detail levels (1.13): decimated copies for distance and crowds, exported next to each FBX (same rig, no takes).
LOD_TRIANGLES = (6000, 2500)


def make_lod(ob, target, name, coll, arm_ob):
    """Decimated copy of a bound mesh (collapse keeps UVs, materials and the rigid weights of every part)."""
    lod = ob.copy()
    lod.data = ob.data.copy()
    lod.modifiers.clear()
    lod.name = lod.data.name = name
    coll.objects.link(lod)
    ratio = min(1.0, target / max(1, tri_count(lod.data)))
    mod = lod.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type, mod.ratio, mod.use_collapse_triangulate = "COLLAPSE", ratio, True
    for o in bpy.context.scene.objects:
        o.select_set(o == lod)
    bpy.context.view_layer.objects.active = lod
    with bpy.context.temp_override(object=lod, active_object=lod, selected_objects=[lod]):
        if lod.data.has_custom_normals:          # the weighted normals of LOD0 would not fit the collapsed faces
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(38))      # the custom normals are gone
    bind_mesh(lod, arm_ob)
    return lod


def export_lods(path, ob, coll, arm_ob):
    """<path>_LOD1.fbx / _LOD2.fbx in a LOD folder next to path; returns the triangle counts."""
    folder = os.path.join(os.path.dirname(path), "LOD")
    os.makedirs(folder, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    tris = {"LOD0": tri_count(ob.data)}
    for i, target in enumerate(LOD_TRIANGLES, 1):
        lod = make_lod(ob, target, "%s_LOD%d" % (ob.name, i), coll, arm_ob)
        tris["LOD%d" % i] = tri_count(lod.data)
        export_fbx(os.path.join(folder, "%s_LOD%d.fbx" % (base, i)), [lod], arm_ob, animations=False)
        bpy.data.objects.remove(lod, do_unlink=True)
    return tris


def export_fbx(path, objs, arm_ob, animations=True):
    hidden = [o for o in objs if o.hide_get()]
    for o in hidden:
        o.hide_set(False)              # hidden objects cannot be selected for use_selection
    for o in bpy.context.scene.objects:
        o.select_set(o in objs or o == arm_ob)
    bpy.context.view_layer.objects.active = arm_ob
    ad = arm_ob.animation_data
    ad.action = None
    # every NLA track holds one action; exported as its own take named after the strip ("Walk", not "Rig|Walk")
    for track in ad.nla_tracks:
        track.mute = False
    for pb in arm_ob.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
    saved_rotation = (arm_ob.rotation_mode, tuple(arm_ob.rotation_euler))
    arm_ob.rotation_mode = "XYZ"
    arm_ob.rotation_euler = (0.0, 0.0, FBX_EXPORT_TURN)      # the parented meshes turn with it
    bpy.context.view_layer.update()
    try:
        bpy.ops.export_scene.fbx(
            filepath=path, use_selection=True, object_types={"ARMATURE", "MESH"},
            apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL", global_scale=1.0,
            axis_forward="-Z", axis_up="Y", use_space_transform=True, bake_space_transform=False,
            mesh_smooth_type="OFF", use_mesh_modifiers=True, use_triangles=True, use_tspace=True,
            add_leaf_bones=False, primary_bone_axis="Y", secondary_bone_axis="X",
            use_armature_deform_only=True, armature_nodetype="NULL",
            bake_anim=animations, bake_anim_use_all_bones=True, bake_anim_use_nla_strips=True,
            bake_anim_use_all_actions=False, bake_anim_force_startend_keying=True, bake_anim_step=1.0,
            bake_anim_simplify_factor=0.0, path_mode="STRIP", embed_textures=False, use_custom_props=False,
        )
    finally:          # a failed export must not leave the robot turned (renders, .blend)
        arm_ob.rotation_mode, arm_ob.rotation_euler = saved_rotation[0], saved_rotation[1]
        bpy.context.view_layer.update()
    for track in ad.nla_tracks:
        track.mute = True
    for o in hidden:
        o.hide_set(True)
    assign_action(arm_ob, bpy.data.actions["Idle"])
    LOG.info("  wrote %s (%.1f KB)", path, os.path.getsize(path) / 1024)


__all__ = [_n for _n in dir() if not _n.startswith("__")]
