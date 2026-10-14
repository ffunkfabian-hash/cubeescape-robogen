"""Work packages, worker processes, output caches and finishing previews."""

from robogen.export import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ----------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------


# ---------------------------------- work packages and worker processes ----------------------------------
# After the model is built, the QA and all preview renders are work packages in a queue. Worker processes
# (Blender, each rebuilding the model from the same code) and, once its own steps are done, the main process take
# them first come, first served. QA workers start before the bake (they only need geometry and animation), render
# workers right after it (they take the UVs from the cache and load the baked textures). Results are merged in a
# fixed order, so the report does not depend on who ran what.

STILL_JOBS = ("views", "skins", "expressions", "types", "cards", "icons", "compare")
EXACT_JOBS = ("views", "expressions")       # the deliverable look; everything else uses the fast preview look


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def make_jobs(type_names, actions_available, args, n_proc):
    """QA: one package per robot type and clip group (big groups split so that every process gets work even if only
    the Classic robot is checked). Renders: views, skins, expressions and types in chunks, one package per clip for
    the animation sheets and per GIF. Each lane is sorted by estimated cost, biggest first."""
    jobs = []
    all_types = ["Classic"] + type_names
    total = sum(a.frames + 1 for a in ANIMS) * len(all_types)
    limit = max(120, -(-total // (2 * n_proc)))
    owner = type_emotes()
    # a type emote is always checked with its owner type, also when that type is not in this run (quick: Classic)
    for clip, t in owner.items():
        a = next(x for x in ANIMS if x.name == clip)
        if t not in all_types:
            jobs.append({"id": "qa:%s:emote:%s" % (t, clip), "lane": "qa", "kind": "qa", "type": t,
                         "clips": [clip], "cost": a.frames + 1})
    for t in all_types:
        for g in GROUPS:
            part, size, k = [], 0, 0
            for a in [a for a in ANIMS if a.group == g and owner.get(a.name, t) == t] + [None]:
                if part and (a is None or size + a.frames + 1 > limit):
                    jobs.append({"id": "qa:%s:%s:%d" % (t, g, k), "lane": "qa", "kind": "qa", "type": t,
                                 "clips": [c.name for c in part], "cost": size})
                    part, size, k = [], 0, k + 1
                if a is not None:
                    part.append(a)
                    size += a.frames + 1
    if not args.no_render:
        def render(kind, key, cost, **kw):
            jobs.append(dict(id="%s:%s" % (kind, key), lane="render", kind=kind, cost=cost, **kw))
        render("views", "all", 7 * 64 * 3)
        for i, names in enumerate(chunks(list(SKINS), 5)):
            render("skins", i, len(names) * 2 * 48, names=names)
        for i, names in enumerate(chunks(list(make_textures.EXPRESSIONS), 14)):
            render("expressions", i, len(names) * 32 * 3, names=names)
        for i, names in enumerate(chunks(list(ROBOT_TYPES), 4)):
            render("types", i, len(names) * 2 * 48, names=names)
        card_items = [["skin", n] for n in SKINS] + [["type", t] for t in ROBOT_TYPES]
        for i, items in enumerate(chunks(card_items, 9)):
            render("cards", i, len(items) * 48, items=items)
        render("icons", "accessories", 20 * 32)
        render("compare", "weathering", len(compare_skins()) * 32)
        if not args.no_anim_sheets:
            for a in ANIMS:
                render("sheet", a.name, 2 * 6 * 12, clip=a.name)
            for name, step in GIF_CLIPS:
                if name in actions_available:
                    frames = next(a.frames for a in ANIMS if a.name == name)
                    render("gif", name, -(-frames // step) * 12, clip=name, step=step)
    # type FBX and their detail levels: packages of the render lane (those workers have the baked materials)
    for i, names in enumerate(chunks(list(ROBOT_TYPES), 3)):
        jobs.append({"id": "fbx:%d" % i, "lane": "render", "kind": "fbx", "types": names, "lods": args.lods,
                     "cost": len(names) * (300 if args.lods else 100)})
    jobs.sort(key=lambda j: -j["cost"])
    return jobs


class JobQueue:
    """A folder: jobs.json lists the packages; a process claims package n by creating claim_<n> exclusively and
    writes res_<n>.json when it is done."""

    def __init__(self, path):
        self.path = path
        with open(os.path.join(path, "jobs.json"), encoding="utf-8") as fh:
            self.jobs = json.load(fh)

    @staticmethod
    def create(path, jobs):
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path)
        with open(os.path.join(path, "jobs.json"), "w", encoding="utf-8") as fh:
            json.dump(jobs, fh)
        return JobQueue(path)

    def claim(self, i):
        try:
            fd = os.open(os.path.join(self.path, "claim_%d" % i), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True

    def work(self, ctx, lanes, who):
        """Runs unclaimed packages of the given lanes (in lane order) until none is left; returns (count, seconds)."""
        count, busy = 0, 0.0
        for lane in lanes:
            for i, job in enumerate(self.jobs):
                if job["lane"] != lane or not self.claim(i):
                    continue
                t0 = time.time()
                result = run_job(ctx, job)
                dt = time.time() - t0
                count, busy = count + 1, busy + dt
                LOG.debug("  [%s] %s %.1fs", who, job["id"], dt)
                tmp = os.path.join(self.path, "res_%d.tmp" % i)
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"result": result, "seconds": round(dt, 2), "by": who}, fh)
                os.replace(tmp, os.path.join(self.path, "res_%d.json" % i))
        return count, busy

    def results(self):
        out = []
        for i, job in enumerate(self.jobs):
            path = os.path.join(self.path, "res_%d.json" % i)
            if not os.path.exists(path):
                raise RuntimeError("work package %s has no result" % job["id"])
            with open(path, encoding="utf-8") as fh:
                out.append((job, json.load(fh)))
        return out


# QA cache (1.13.1): a clip's QA result only depends on the robot type's mesh, the rig, the clip's keys and the QA code.
# Unchanged inputs = reused result (cache/qa, one small JSON per type and clip). The key holds the source code of the
# QA functions, so a changed check invalidates the cache by itself; --no-cache ignores it.
# 1.17 Slimline: outputs whose inputs did not change are not made again - the quick tier spent 8 s on FBX it had
# already written, 5 s measuring unchanged clips and 2 s on the viewer data. Keys are content digests (meshes in
# canonical order with UVs and materials, rig, clips, the code that writes them); a missing or resized file is rebuilt.
CLIP_CODE_PARTS = ("measure_clips", "contact_ranges", "servo_events", "char_space", "group_faces", "evaluated_coords")
EXPORT_CODE_PARTS = ("export_fbx", "export_lods", "make_lod", "export_forms", "combine_objects", "bind_mesh",
                     "form_objects", "set_look")




def export_digest(ob):
    """mesh_digest plus what an export writes besides the shape: UVs and materials per face, in canonical order."""
    me = ob.data
    h = hashlib.sha1(mesh_digest(ob).encode())
    uv = me.uv_layers[0].data if me.uv_layers else None
    faces = []
    for p in me.polygons:
        corners = sorted((me.loops[li].vertex_index, tuple(round(c, 6) for c in uv[li].uv) if uv else ())
                         for li in p.loop_indices)
        faces.append((tuple(corners), p.material_index))
    h.update(repr(sorted(faces)).encode())
    mats = []
    for m in me.materials:
        imgs = sorted(n.image.name for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image) if m else []
        mats.append((m.name if m else None, imgs))
    h.update(repr((mats, ob.name, ob.get("form", ""), ob.get("slot", ""), ob.get("variant", ""))).encode())
    return h.hexdigest()


class OutputCache:
    """cache/outputs.json: per output a key and the files it wrote with their sizes (+ a small result, e.g. the
    triangle counts for the report). fresh() is true when the key matches and every file is still there unchanged."""

    def __init__(self, folder):
        self.path = os.path.join(folder, "outputs.json") if folder else None
        self.entries = {}
        if self.path and os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.entries = json.load(fh)
        self.hits = []

    def fresh(self, name, key):
        e = self.entries.get(name)
        if not self.path or not e or e["key"] != key:
            return None
        for f, size in e["files"].items():
            if not os.path.exists(f) or os.path.getsize(f) != size:
                return None
        self.hits.append(name)
        return e

    def store(self, name, key, files, result=None):
        if not self.path:
            return
        found = []
        for f in files:
            if os.path.isdir(f):
                found += [os.path.join(d, n) for d, _, ns in os.walk(f) for n in ns]
            elif os.path.exists(f):
                found.append(f)
        self.entries[name] = {"key": key, "files": {f: os.path.getsize(f) for f in sorted(found)}, "result": result}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        write_json_atomic(self.path, self.entries)


BAND_RUNS = 10
LOAD_QUIET, LOAD_LIGHT = 0.3, 0.8        # load average at the start per core
BAND_TOLERANCE = {"quiet": 1.3, "light": 2.6}


def timing_band(folder, tier, state, total, load_start):
    """Verdict of a run time and the history update (cache/timings.json)."""
    cores = os.cpu_count() or 1
    level = "quiet" if load_start < LOAD_QUIET * cores else "light" if load_start < LOAD_LIGHT * cores else "heavy"
    path = os.path.join(folder, "timings.json") if folder else None
    hist = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            hist = json.load(fh)
    runs = hist.get("%s/%s" % (tier, state), [])
    band = {"tier": tier, "caches": state, "load": level, "reference_runs": len(runs)}
    if level == "heavy":
        band.update(verdict="not judged (heavy load)")
    elif len(runs) < 3:
        band.update(verdict="no reference yet")
    else:
        med = float(np.median(runs))
        limit = med * BAND_TOLERANCE[level]
        band.update(median=round(med, 1), limit=round(limit, 1), verdict="ok" if total <= limit else "slower")
    if path and level == "quiet":       # slower runs too: a lasting change becomes the reference after ~6 runs
        hist["%s/%s" % (tier, state)] = (runs + [round(total, 1)])[-BAND_RUNS:]
        os.makedirs(folder, exist_ok=True)
        write_json_atomic(path, hist)
    return band


def cached_clip_data(ob, arm_ob, actions, folder):
    """measure_clips with a cache per clip (cache/clips): only clips without a stored result are measured."""
    if not folder:
        return measure_clips(ob, arm_ob, actions)
    os.makedirs(folder, exist_ok=True)
    base = code_digest(CLIP_CODE_PARTS, HAND_CONTACT, SERVO_ON, SERVO_RISE, SERVO_FALL, SERVO_FULL, FPS,
                       sorted(SERVO_GROUPS.items()), mesh_digest(ob), rig_digest(arm_ob))[:20]
    found, paths = {}, {}
    for a in ANIMS:
        # the keys plus what measure_clips reads from the clip itself (length, loop, gait, props)
        clip = repr((action_digest(actions[a.name]), a.frames, a.loop, a.gait, a.group, CLIP_PROPS.get(a.name)))
        paths[a.name] = os.path.join(folder, "%s_%s_%s.json" % (base, a.name,
                                                                hashlib.sha1(clip.encode()).hexdigest()[:16]))
        if os.path.exists(paths[a.name]):
            with open(paths[a.name], encoding="utf-8") as fh:
                found[a.name] = json.load(fh)
            os.utime(paths[a.name])
    missing = [a.name for a in ANIMS if a.name not in found]
    res = measure_clips(ob, arm_ob, actions, missing) if missing else {}
    for n, d in res.items():
        write_json_atomic(paths[n], d)
    RUN_STATE["clips_measured"] = len(res)
    if found:
        LOG.info("  clip data: %d clips from the cache, %d measured", len(found), len(res))
    found.update(res)
    return {a.name: found[a.name] for a in ANIMS}


QA_CODE_PARTS = ("run_qa", "qa_problem", "group_faces", "evaluated_coords", "Topology", "GroupTrees")


def qa_code_digest():
    h = hashlib.sha1()
    for name in QA_CODE_PARTS:
        h.update(code_source(name).encode())
    for rule in (ACCEPTED_OVERLAPS, ACCEPTED_DETACH, HIDE_HELD_ITEM, ACCEPTED_GROUND.items()):
        h.update(repr(sorted(rule)).encode())
    return h.hexdigest()








QA_CACHE_DAYS = 7


def prune_qa_cache(folder):
    """Removes cached QA results nobody used for QA_CACHE_DAYS (every model change starts a new generation)."""
    if not os.path.isdir(folder):
        return 0
    limit = time.time() - QA_CACHE_DAYS * 86400
    old = [f for f in os.scandir(folder) if f.is_file() and f.stat().st_mtime < limit]
    for f in old:
        os.remove(f.path)
    return len(old)




class JobContext:
    """What a package needs from the built model; QA meshes and the preview scene are made on first use."""

    def __init__(self, part_objs, coll, preview_coll, arm_ob, actions, robot_mesh, preview_dir, tex_dir,
                 qa_cache=None, export_dir=None):
        self.part_objs, self.coll, self.preview_coll = part_objs, coll, preview_coll
        self.arm_ob, self.actions, self.robot_mesh = arm_ob, actions, robot_mesh
        self.preview_dir, self.tex_dir = preview_dir, tex_dir
        self.qa_meshes = {}
        self.qa_cache = qa_cache            # folder or None
        self.qa_keys = {}                   # type -> digest of QA code, rig and type mesh
        self.action_keys = {}               # clip -> digest of its keys
        self.qa_hits = 0
        self.export_dir = export_dir        # the main output folder (type FBX packages)

    def qa_key(self, tname):
        if tname not in self.qa_keys:
            if "code" not in self.qa_keys:
                self.qa_keys["code"] = qa_code_digest() + rig_digest(self.arm_ob)
            ob = self.qa_mesh(tname)
            self.qa_keys[tname] = hashlib.sha1((self.qa_keys["code"] + mesh_digest(ob)).encode()).hexdigest()[:20]
        return self.qa_keys[tname]

    def cached_qa(self, tname, clips):
        """QA of clips with the cache: only the clips without a stored result are run."""
        if not self.qa_cache:
            return run_qa(self.qa_mesh(tname), self.arm_ob, self.actions, tname, quiet=True, clips=clips)
        os.makedirs(self.qa_cache, exist_ok=True)
        key = self.qa_key(tname)
        rest_path = os.path.join(self.qa_cache, "%s_rest.json" % key)
        paths, found = {}, {}
        for c in clips:
            if c not in self.action_keys:
                self.action_keys[c] = action_digest(self.actions[c])[:16]
            paths[c] = os.path.join(self.qa_cache, "%s_%s_%s.json" % (key, c, self.action_keys[c]))
            if os.path.exists(paths[c]):
                with open(paths[c], encoding="utf-8") as fh:
                    found[c] = json.load(fh)
                os.utime(paths[c])          # used: survives prune_qa_cache
        missing = [c for c in clips if c not in found]
        if not missing and os.path.exists(rest_path):
            with open(rest_path, encoding="utf-8") as fh:
                res = json.load(fh)
        else:
            res = run_qa(self.qa_mesh(tname), self.arm_ob, self.actions, tname, quiet=True, clips=missing)
            write_json_atomic(rest_path, {"rest_min_z": res["rest_min_z"], "rest_height": res["rest_height"]})
            for c in missing:
                write_json_atomic(paths[c], res["actions"][c])
        self.qa_hits += len(found)
        actions = dict(found)
        actions.update(res.get("actions", {}))
        return {"rest_min_z": res["rest_min_z"], "rest_height": res["rest_height"], "actions": actions}

    def camera(self):
        cam = bpy.data.objects.get("Preview_Camera") or setup_render_scene(self.preview_coll)
        bpy.context.scene.camera = cam
        return cam

    def qa_mesh(self, tname):
        """One QA mesh at a time: every mesh in the view layer is evaluated on each frame change."""
        ob = self.qa_meshes.get(tname)
        if ob is None:
            for other in self.qa_meshes.values():
                bpy.data.objects.remove(other, do_unlink=True)
            self.qa_meshes.clear()
            if tname.startswith("Form_"):         # a body form in the default look (QA_FORMS)
                objs = form_objects(self.part_objs, None, {}, tname[len("Form_"):])
            else:
                cfg = default_config()
                cfg.update(ROBOT_TYPES[tname]["variants"])
                objs = [self.part_objs[g] for g in config_groups(cfg)]
            ob = combine_objects(objs, "QA_" + tname, self.coll)
            bind_mesh(ob, self.arm_ob)
            self.qa_meshes[tname] = ob
        return ob

    def cleanup(self):
        """Back to the state the .blend is saved in."""
        for ob in self.qa_meshes.values():
            bpy.data.objects.remove(ob, do_unlink=True)
        self.qa_meshes.clear()
        set_fast_eyes(False)
        bpy.context.scene.eevee.use_raytracing = True
        show_tool(False)


def export_forms(folder, part_objs, robot_mesh, coll, arm_ob, tex_dir):
    """Forms/CubeEscape_Robot_<Form>.fbx (default look in that form, same rig, no takes - the clips of the main FBX
    play on it) and, for mesh forms, Forms/CubeEscape_Robot_Modular_<Form>.fbx. Returns triangle counts."""
    os.makedirs(folder, exist_ok=True)
    out = {}
    for form, d in FORMS.items():
        if d["kind"] == "shader":
            continue
        tmp = combine_objects(form_objects(part_objs, robot_mesh, {}, form), "Robot_Mesh_" + form, coll)
        bind_mesh(tmp, arm_ob)
        set_look(tmp, tex_dir)
        export_fbx(os.path.join(folder, "CubeEscape_Robot_%s.fbx" % form), [tmp], arm_ob, animations=False)
        out[form] = {"triangles": tri_count(tmp.data)}
        bpy.data.objects.remove(tmp, do_unlink=True)
        if d["kind"] == "mesh":
            export_fbx(os.path.join(folder, "CubeEscape_Robot_Modular_%s.fbx" % form), list(FORM_OBJS[form].values()),
                       arm_ob, animations=False)
    LOG.info("  forms: %s", ", ".join("%s %d triangles" % (f, v["triangles"]) for f, v in out.items()))
    return out


def export_type_fbx(ctx, names, lods):
    """Types/CubeEscape_Robot_<Type>.fbx (+ LOD) for some robot types; returns their triangle and material counts."""
    type_dir = os.path.join(ctx.export_dir, "Types")
    os.makedirs(type_dir, exist_ok=True)
    out = {}
    for tname in names:
        cfg = default_config()
        cfg.update(ROBOT_TYPES[tname]["variants"])
        tmp = combine_objects([ctx.part_objs[g] for g in config_groups(cfg)], "Robot_Mesh_" + tname, ctx.coll)
        bind_mesh(tmp, ctx.arm_ob)
        set_look(tmp, ctx.tex_dir)          # render packages may have left another skin on the shared materials
        path = os.path.join(type_dir, "CubeEscape_Robot_%s.fbx" % tname)
        export_fbx(path, [tmp], ctx.arm_ob, animations=False)
        out[tname] = {"triangles": tri_count(tmp.data), "materials": len(tmp.data.materials),
                      "lods": export_lods(path, tmp, ctx.coll, ctx.arm_ob) if lods else None}
        bpy.data.objects.remove(tmp, do_unlink=True)
    return out


def run_job(ctx, job):
    kind = job["kind"]
    if kind == "qa":
        return ctx.cached_qa(job["type"], job["clips"])
    if kind == "fbx":
        return export_type_fbx(ctx, job["types"], job["lods"])
    cam = ctx.camera()
    preview_quality(kind in EXACT_JOBS)
    show_tool(kind == "sheet" and job["clip"] in TOOL_CLIPS)
    if kind in STILL_JOBS:
        rest_pose(ctx.arm_ob)
    if kind == "views":
        return render_views(cam, ctx.preview_dir)
    if kind == "skins":
        return render_skins(cam, ctx.robot_mesh, ctx.part_objs, ctx.preview_dir, ctx.tex_dir, job["names"])
    if kind == "expressions":
        return render_expressions(cam, ctx.robot_mesh, ctx.preview_dir, ctx.tex_dir, job["names"])
    if kind == "types":
        return render_types(cam, ctx.part_objs, ctx.robot_mesh, ctx.preview_dir, ctx.tex_dir, job["names"])
    if kind == "cards":
        return render_cards(cam, ctx.part_objs, ctx.robot_mesh, ctx.preview_dir, ctx.tex_dir, job["items"])
    if kind == "icons":
        rest_pose(ctx.arm_ob)      # the antenna rides on a spring bone: no leftover clip pose of an earlier package
        return render_accessory_icons(cam, ctx.part_objs, ctx.preview_dir, ctx.tex_dir)
    if kind == "compare":
        tiles = render_compare(cam, ctx.robot_mesh, ctx.preview_dir, os.path.join(ctx.preview_dir, ".compare"))
        set_look(ctx.robot_mesh, ctx.tex_dir)
        return tiles
    if kind == "sheet":
        return render_anim_rows(cam, ctx.arm_ob, ctx.actions, ctx.preview_dir, job["clip"])
    if kind == "gif":
        return render_gif(cam, ctx.arm_ob, ctx.actions, ctx.preview_dir, job["clip"], job["step"])
    raise ValueError("unknown work package %s" % job["id"])


def start_workers(first, n, lanes, args, out, queue, textures=None):
    """n Blender processes that rebuild the model (no bake: QA workers skip textures, render workers load the baked
    ones and the cached UVs) and then work on the queue."""
    procs = []
    for i in range(first, first + n):
        wdir = os.path.join(out, "logs", "worker_%d" % i)
        shutil.rmtree(wdir, ignore_errors=True)
        os.makedirs(wdir)
        threads = args.worker_threads if lanes[0] == "qa" else args.render_threads
        cmd = [bpy.app.binary_path, "-b", "--factory-startup", "-noaudio", "-t", str(threads), "--python-exit-code", "1",
               "--python", GENERATOR, "--", "--tier", args.tier, "--out", wdir, "--src-dir", out,
               "--worker-queue", queue.path, "--worker-lanes", ",".join(lanes), "--worker-id", str(i)]
        cmd += ["--use-textures", textures, "--uv-cache", os.path.join(out, "cache")] if textures else ["--skip-bake"]
        if args.no_cache:
            cmd += ["--no-cache"]           # the workers would otherwise reuse cache/qa and cache/render
        if args.groups:
            cmd += ["--groups", args.groups]
        # niced: the main process's own steps (clip data, export) are the critical path while the workers run
        procs.append((i, wdir, subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                preexec_fn=(lambda: os.nice(10)) if hasattr(os, "nice") else None)))
    if n:
        LOG.info("  started %d %s worker(s)", n, "/".join(lanes))
    return procs


def wait_workers(procs):
    stats = {}
    for i, wdir, proc in procs:
        code = proc.wait()
        log = os.path.join(wdir, "logs", "generate_robot.log")
        if code != 0:
            raise RuntimeError("worker %d failed (exit %s), log: %s" % (i, code, log))
        with open(os.path.join(wdir, "worker.json"), encoding="utf-8") as fh:
            stats["worker_%d" % i] = json.load(fh)
        if not os.environ.get("ROBOT_KEEP_WORKER_LOGS"):        # debugging: keep logs/worker_<n>
            shutil.rmtree(wdir, ignore_errors=True)
    return stats


def merge_qa(results, type_names):
    """QA package results -> {type: {rest_min_z, rest_height, actions in clip order}} for Classic + type_names."""
    parts = collections.defaultdict(list)
    for job, res in results:
        if job["kind"] == "qa":
            parts[job["type"]].append(res["result"])
    qa = {}
    full = ["Classic"] + type_names
    owner = type_emotes()
    for t in full + sorted(set(parts) - set(full)):     # extra types: only their emotes were checked
        rest = {(r["rest_min_z"], r["rest_height"]) for r in parts[t]}
        if len(rest) != 1:
            raise RuntimeError("QA packages of %s disagree on the rest pose: %s" % (t, rest))
        actions = {}
        for r in parts[t]:
            actions.update(r["actions"])
        wanted = [a.name for a in ANIMS if owner.get(a.name, t) == t] if t in full else \
            [c for c, o in owner.items() if o == t]
        missing = [c for c in wanted if c not in actions]
        if missing:
            raise RuntimeError("QA of %s is missing clips %s" % (t, missing))
        min_z, height = rest.pop()
        qa[t] = {"rest_min_z": min_z, "rest_height": height,
                 "actions": {a.name: actions[a.name] for a in ANIMS if a.name in actions}}
    return qa


def finish_previews(results, preview_dir, report, tex_dir):
    """Sheets from the render packages, always in the same order (skins, expressions, types, contact sheets)."""
    by_kind = collections.defaultdict(list)
    for job, res in results:
        by_kind[job["kind"]].append((job, res["result"]))
    if by_kind["views"]:
        report["previews"] = by_kind["views"][0][1]
    tiles = collections.defaultdict(dict)
    for kind in ("skins", "types"):
        for _, res in by_kind[kind]:
            for view, items in res.items():
                tiles[(kind, view)].update(dict(items))
    for _, res in by_kind["expressions"]:
        tiles[("expressions", "front")].update(dict(res))
    sheets = []
    for (kind, view), names in ((("skins", "front"), list(SKINS)), (("skins", "iso"), list(SKINS)),
                                (("expressions", "front"), list(make_textures.EXPRESSIONS)),
                                (("types", "front"), list(ROBOT_TYPES)), (("types", "iso"), list(ROBOT_TYPES))):
        if tiles[(kind, view)]:
            name = {"skins": "skins", "types": "types", "expressions": "expressions"}[kind]
            sheet = os.path.join(preview_dir, "%s%s.png" % (name, "" if view == "front" else "_iso"))
            sheets.append(([(n, tiles[(kind, view)][n]) for n in names], sheet, 18 if kind == "expressions" else 22))
    # every sheet is independent ImageMagick work (MAGICK_THREAD_LIMIT=1): one pool runs them side by side
    tasks = [(montage_sheet, t) for t in sheets]
    icons = None
    if by_kind["cards"]:
        cards = {}
        for _, res in by_kind["cards"]:
            cards.update(res)
        acc = {}
        for _, res in by_kind["icons"]:
            acc.update(res)
        def icons_then_sheet():         # the skin sheet deletes the card renders the icons are cut from
            res = build_icons(cards, acc, os.path.dirname(preview_dir), tex_dir)
            build_skin_sheet(cards, preview_dir)
            return res
        icons = (icons_then_sheet, ())
        tasks.append(icons)
    for _, res in by_kind["compare"]:          # the weathering comparison: 5 layers x 3 amounts, materials, wear
        tasks.append((montage_sheet, ([tuple(t) for t in res], os.path.join(preview_dir, "weathering.png"), 18)))
    if by_kind["sheet"]:
        tasks.append((build_contact_sheets, ({job["clip"]: rows for job, rows in by_kind["sheet"]}, preview_dir)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, len(tasks)))) as pool:
        futures = [pool.submit(fn, *a) for fn, a in tasks]
        done = [f.result() for f in futures]            # re-raises the first error
    if icons is not None:
        report["icons"], report["icon_hints"] = done[tasks.index(icons)]
    shutil.rmtree(os.path.join(preview_dir, ".compare"), ignore_errors=True)
    frames_dir = os.path.join(preview_dir, "anim_frames")
    if os.path.isdir(frames_dir):
        shutil.rmtree(frames_dir)


def textures_in(tex_dir):
    return {fn: os.path.join(tex_dir, fn) for fn in os.listdir(tex_dir) if fn.endswith(".png")}


__all__ = [_n for _n in dir() if not _n.startswith("__")]
