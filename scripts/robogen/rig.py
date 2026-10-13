"""Armature and mesh binding."""

from robogen.textures import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ----------------------------------------------------------------------------------------------
# Rig
# ----------------------------------------------------------------------------------------------


def build_armature(coll):
    arm_data = bpy.data.armatures.new("Robot_Rig")
    arm_data.display_type = "OCTAHEDRAL"
    arm = bpy.data.objects.new("Robot_Armature", arm_data)
    coll.objects.link(arm)
    arm.show_in_front = True
    bpy.context.view_layer.objects.active = arm
    for o in bpy.context.scene.objects:
        o.select_set(o == arm)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones

    def bone(name, head, tail, parent=None, connect=False, roll_up=None):
        b = eb.new(name)
        b.head = Vector(head)
        b.tail = Vector(tail)
        if roll_up is not None:
            b.align_roll(Vector(roll_up))
        if parent:
            b.parent = eb[parent]
            b.use_connect = connect
        b.use_deform = True
        return b

    bone("Root", J["root"], J["root"] + Vector((0, 0.18, 0)), roll_up=(0, 0, 1))
    bone("Pelvis", J["pelvis"], J["spine"], "Root", roll_up=(0, -1, 0))
    bone("Spine", J["spine"], J["neck"], "Pelvis", connect=True, roll_up=(0, -1, 0))
    bone("Head", J["neck"], J["head_top"], "Spine", connect=True, roll_up=(0, -1, 0))
    bone("Eyes", J["eyes"], J["eyes"] + Vector((0, -0.08, 0)), "Head", roll_up=(0, 0, 1))
    # accessory sockets (deforming, so accessories can be skinned rigidly like every other part)
    # sockets and spring bones sit where the reshaped head and torso put their parts (PROPORTIONS)
    bone("Socket_Head", shaped("Head", (0, 0, 1.30)), shaped("Head", (0, 0, 1.40)), "Head", roll_up=(0, -1, 0))
    bone("Socket_Neck", shaped("Spine", (0, -0.17, 0.872)), shaped("Spine", (0, -0.25, 0.872)), "Spine",
         roll_up=(0, 0, 1))
    bone("Socket_Back", shaped("Spine", (0, 0.22, 0.77)), shaped("Spine", (0, 0.30, 0.77)), "Spine", roll_up=(0, 0, 1))
    # 1.12 spring bones (SPRINGS): baked secondary motion of the cape and the antenna rod
    bone("Cape", shaped("Spine", (0, 0.235, 0.830)), shaped("Spine", (0, 0.235, 0.600)), "Socket_Back",
         roll_up=(0, 1, 0))
    bone("Antenna", shaped("Head", (0, 0.0, 1.325)), shaped("Head", (0, 0.0, 1.480)), "Socket_Head",
         roll_up=(0, -1, 0))
    for side in ("L", "R"):
        bone("Shoulder_" + side, J["clav_" + side], J["shoulder_" + side], "Spine", roll_up=(0, 0, 1))
        bone("UpperArm_" + side, J["shoulder_" + side], J["elbow_" + side], "Shoulder_" + side, True, (0, -1, 0))
        bone("LowerArm_" + side, J["elbow_" + side], J["wrist_" + side], "UpperArm_" + side, True, (0, -1, 0))
        bone("Hand_" + side, J["wrist_" + side], J["knuckle_" + side], "LowerArm_" + side, True, (0, -1, 0))
        bone("Fingers_" + side, J["knuckle_" + side], J["knuckle_" + side] + J["finger_dir_" + side] * 0.05,
             "Hand_" + side, True, (0, -1, 0))
        bone("Thumb_" + side, J["thumb_" + side], J["thumb_" + side] + J["thumb_dir_" + side] * 0.045,
             "Hand_" + side, False, (0, 0, 1))
        # tool socket: grip point inside the curled fingers, pointing along the hand (no weights, exported for props)
        s = 1 if side == "L" else -1
        grip = J["knuckle_" + side] + Vector((-s * 0.030, -0.005, 0.0))
        bone("Prop_" + side, grip, grip + J["dir_fore_" + side] * 0.10, "Hand_" + side, False, (0, -1, 0))
        bone("UpperLeg_" + side, J["hip_" + side], J["knee_" + side], "Pelvis", roll_up=(0, -1, 0))
        bone("LowerLeg_" + side, J["knee_" + side], J["ankle_" + side], "UpperLeg_" + side, True, (0, -1, 0))
        bone("Foot_" + side, J["ankle_" + side], J["toe_" + side], "LowerLeg_" + side, True, (0, 0, 1))
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm


def bind_mesh(ob, arm):
    ob.parent = arm
    ob.matrix_parent_inverse = Matrix.Identity(4)
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    mod.use_vertex_groups = True
    mod.use_deform_preserve_volume = False


__all__ = [_n for _n in dir() if not _n.startswith("__")]
