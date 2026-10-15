"""Cube Escape - robot character generator for Blender 5.2 LTS.

Builds the stylized low-poly robot from the Cube Escape key art completely from code:
geometry, procedural materials, UVs, baked textures, rig, ten animations, QA checks,
preview renders, .blend and a Unity-ready .fbx.

Usage (Blender CLI):
    Blender -b --factory-startup --python-exit-code 1 --python generate_robot.py -- \
        [--tier quick|standard|full] [--out /path/to/output] [--stage model|full] [--tex-size 2048] [--no-render]

Test tiers (see TIERS): quick = small textures, QA of the standard robot only, no renders (output/_quick);
standard = medium textures, full QA, still renders (output/_standard); full = deliverables (output). Every run
reports its step times in logs/robot_report.json and warns when the tier's time budget is exceeded.

QA and preview renders are work packages run by worker processes (--workers, --render-workers) and then by the
main process itself; see make_jobs() and JobQueue.

Conventions (shared with the Cube Escape Blender pipeline):
    * 1 Blender unit = 1 m, robot stands on z = 0, origin between the feet.
    * The robot looks along -Y (Blender "Front" view). The FBX is exported with
      forward -Z / up Y, so in Unity it looks along +Z.
    * The robot's left side is +X. Bones are suffixed _L / _R.

Code layout (1.17): this file is the entry point and main(); the steps live in the package robogen/ (see
robogen/__init__.py for the modules and their order).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import robogen.preview  # noqa: E402
import quiet  # noqa: E402 - heavy runs pause other sessions (1.20.2)
from robogen.config import *  # noqa: E402,F401,F403 - every step of the generator


def quiet_full_run(args):
    """1.20.2: a full run on a busy machine runs again through pause_sessions.py (quiet.py) - this process only waits.
    1.20.3: first it queues on the machine-wide heavy-run lock (heavy_lock.py, shared with Cube Escape and Cubegen) and
    holds it for the whole run - lock first, then pause. 1.20.6: the run is a child also on a quiet machine (quiet.run):
    before, it went on in this process, which held the lock itself - a TERM ended Blender at once and freed the lock while
    its workers still ran. Now TERM/HUP/INT go on to the child's whole process group (run_forwarding) and the lock is
    free once that group is empty. Returns the exit code of that run, or None to go on here."""
    if args.tier != "full" or args.worker_queue or os.environ.get("ROBOGEN_QUIET_ACTIVE"):
        return None
    return quiet.run([bpy.app.binary_path] + sys.argv[1:], what="full (%s)" % os.path.abspath(args.out))


def main():
    args = parse_args()
    code = quiet_full_run(args)
    if code is not None:
        return code
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    setup_logging(out)
    t_start = time.time()
    robogen.preview.PREVIEW_SAMPLE_SCALE = args.preview_samples     # read by preview_samples() in its module
    worker = bool(args.worker_queue)
    LOG.info("Cube Escape robot generator %s | Blender %s | stage=%s | tier=%s%s", VERSION, bpy.app.version_string,
             args.stage, args.tier, " (worker %s: %s)" % (args.worker_id, args.worker_lanes) if worker else "")
    report = {"generator": VERSION, "blender": bpy.app.version_string, "stage": args.stage, "tier": args.tier}
    load_start = os.getloadavg()[0]     # times are only comparable on an otherwise idle machine
    preview_dir = os.path.join(out, "Previews") if not worker else os.path.join(args.src_dir, "Previews")
    tex_dir = args.use_textures or os.path.join(out, "Textures")
    # 1.20.2: content-addressed entries live in the shared cache of all checkouts (robocache, ROBOGEN_CACHE); only
    # checkout-bound state stays in <out>/cache (outputs.json, the working copies of the preview skins)
    cache_dir = robocache.root()
    if not worker:
        robocache.prepare(out, SCRIPTS_DIR, cache_dir, log=LOG.info)
        LOG.info("  shared cache: %s%s", cache_dir, " (not read: --no-cache)" if args.no_cache else "")
        if os.environ.get("ROBOGEN_QUIET_INFO"):
            report["quiet"] = os.environ["ROBOGEN_QUIET_INFO"]
            LOG.info("  quiet: %s", report["quiet"])
    os.makedirs(preview_dir, exist_ok=True)

    with Step("scene"):
        reset_scene()
        coll = get_collection("Robot")
        preview_coll = get_collection("Preview")
    with Step("ImageMagick source textures"):
        src_paths = source_textures(os.path.join(args.src_dir or out, "SourceTextures"), LOG.info,
                                    cache=not args.no_cache or worker,
                                    shared=None if args.no_cache or worker else robocache.kind_dir("source", cache_dir))
        load_source_textures(src_paths)
        report["source_textures"] = sorted(os.path.basename(p) for p in src_paths.values())
    with Step("materials"):
        mats = build_materials()
    with Step("geometry"):
        define_joints()
        build_robot()
        apply_proportions()
        floating = check_part_connectivity(report)
        parts_coll = get_collection("Robot_Parts")
        part_objs = assemble_groups(mats, parts_coll)
        for o in part_objs.values():
            apply_weighted_normals(o)
        validate_skins()            # needs the slots and variants the geometry registered
        report["slots"] = {sl: list(vs) for sl, vs in SLOTS.items()}
        report["part_objects"] = {k: tri_count(o.data) for k, o in part_objs.items()}
        LOG.info("  %d parts in %d objects; slots: %s", report["parts"], len(part_objs),
                 ", ".join("%s=%s" % (k, "/".join(v)) for k, v in SLOTS.items()))
    check_categories()              # on the full clip list, before a --groups filter
    if args.groups:
        keep = set(args.groups.split(",")) | {"Basis"}
        ANIMS[:] = [a for a in ANIMS if a.group in keep]
        LOG.warning("animation filter: %s (%d clips)", sorted(keep), len(ANIMS))
    with Step("uv") if not (worker and args.skip_bake) else _Skip():
        if worker and args.skip_bake:
            raise _SkipBlock()          # the QA does not look at UVs
        if worker:
            if not unwrap(list(part_objs.values()), args.uv_cache, require=True):
                raise RuntimeError("render worker: no cached UVs in %s" % args.uv_cache)
        else:
            unwrap(list(part_objs.values()), robocache.kind_dir("uv", cache_dir), read=not args.no_cache)

    if part_objs["Body"].data.uv_layers:
        PERFORATION["cells"] = perforation_cells(part_objs["Body"])
    queue, procs = None, []
    if not args.no_cache:
        RENDER_CACHE["dir"] = robocache.kind_dir("render", cache_dir)
    if not worker and args.stage == "full":
        only = set(filter(None, args.qa_types.split(",")))
        type_names = [t for t in list(ROBOT_TYPES) + QA_FORMS if t != "Classic" and (not only or t in only)]
        jobs = make_jobs(type_names, {a.name for a in ANIMS}, args, args.workers + 1)
        queue = JobQueue.create(os.path.join(out, "logs", "jobs"), jobs)
        n_qa_jobs = sum(j["lane"] == "qa" for j in jobs)
        # type FBX alone do not justify render workers (quick: the main process exports them, they are small)
        has_render = any(j["lane"] == "render" and j["kind"] != "fbx" for j in jobs)
        n_render = min(args.render_workers, args.workers if args.workers < 2 else args.workers - 1) if has_render else 0
        # about 600 QA frames (2-3 s) per worker at least: a worker costs 2 s to start and slows the main process
        qa_frames = sum(j["cost"] for j in jobs if j["lane"] == "qa")
        n_qa = min(args.workers - n_render, n_qa_jobs, max(1, qa_frames // 600))
        report["workers"] = {"qa": n_qa, "render": n_render, "packages": len(jobs)}
        with Step("start QA workers"):
            procs += start_workers(0, n_qa, ["qa"], args, out, queue)

    if args.skip_bake and not args.use_textures and not worker:
        LOG.warning("skip-bake: procedural materials only (debug run)")
    with Step("bake textures") if not args.skip_bake else _Skip():
        if args.skip_bake:
            raise _SkipBlock()
        if args.use_textures:
            tex_paths = textures_in(args.use_textures)
        else:
            tex_paths = bake_textures(list(part_objs.values()), mats, args.tex_size, args.ao_samples, tex_dir,
                                      None if args.no_cache else cache_dir, args.check_compose, args.bake_device,
                                      None if args.no_render else os.path.join(preview_dir, ".compare"))
        final = [build_baked_material(tex_paths), build_baked_material(tex_paths, "M_RobotFabric",
                                                                        SKINS[DEFAULT_SKIN]["fabric"]),
                 mats["EyeScreen"], mats["Display"]]
        for o in part_objs.values():
            remap_materials(o, final)
        report["textures"] = sorted(os.path.basename(p) for p in tex_paths.values())
    if queue is not None and report["workers"]["render"]:
        with Step("start render workers"):
            procs += start_workers(len(procs), report["workers"]["render"], ["render", "qa"], args, out, queue,
                                   textures=tex_dir)
    with Step("default mesh"):
        robot_mesh = combine_objects([part_objs[g] for g in config_groups(default_config())], "Robot_Mesh", coll)
        report["triangles"] = tri_count(robot_mesh.data)
        report["vertices"] = len(robot_mesh.data.vertices)
        report["materials"] = [m.name for m in robot_mesh.data.materials]
        LOG.log(logging.DEBUG if worker else logging.INFO, "  Robot_Mesh (default look): %d triangles, %d vertices, "
                "materials %s", report["triangles"], report["vertices"], report["materials"])
        for o in part_objs.values():
            o.hide_render = True
            o.hide_set(True)
    if args.stage == "model":
        if not args.no_render:
            with Step("preview renders (procedural materials)"):
                cam = setup_render_scene(preview_coll)
                render_views(cam, preview_dir, prefix="model_")
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "CubeEscape_Robot_model.blend"))
        write_report(out, report)
        return 1 if floating else 0
    with Step("rig"):
        arm_ob = build_armature(coll)
        for o in [robot_mesh] + list(part_objs.values()):
            bind_mesh(o, arm_ob)
    with Step("forms"):
        # the voxel merge reads the baked maps: only where they are complete (not in QA workers before the bake)
        make_forms(part_objs, get_collection("Robot_Forms"), arm_ob,
                   tex_dir if (args.use_textures or not args.skip_bake) else None)
        add_preview_tool(arm_ob, preview_coll)
        report["bones"] = [b.name for b in arm_ob.data.bones]
    with Step("animations"):
        actions = build_animations(arm_ob, quiet=worker)
        report["animations"] = {a.name: {"frames": a.frames, "loop": a.loop, "description": a.desc, "group": a.group,
                                         "unity": a.unity} for a in ANIMS}
    ctx = JobContext(part_objs, coll, preview_coll, arm_ob, actions, robot_mesh, preview_dir, tex_dir,
                     None if args.no_cache else robocache.kind_dir("qa", cache_dir), args.src_dir or out)
    if worker:
        ready = time.time() - t_start
        LOG.info("worker ready after %.1f s", ready)
        count, busy = JobQueue(args.worker_queue).work(ctx, args.worker_lanes.split(","), "w%s" % args.worker_id)
        with open(os.path.join(out, "worker.json"), "w", encoding="utf-8") as fh:
            json.dump({"lanes": args.worker_lanes, "ready": round(ready, 1), "packages": count, "busy": round(busy, 1),
                       "total": round(time.time() - t_start, 1), "qa_cached": ctx.qa_hits,
                       "render_cached": RENDER_CACHE["hits"], "rendered": RENDER_CACHE["renders"]}, fh)
        LOG.info("worker done: %d packages in %.1f s", count, busy)
        return 0
    write_animation_table(out)
    with Step("clip data"):
        clips = cached_clip_data(robot_mesh, arm_ob, actions,
                                 None if args.no_cache else robocache.kind_dir("clips", cache_dir))
        for a in ANIMS:
            clips[a.name] = dict(clip_meta(a), **clips[a.name])
            clips[a.name]["prop"] = prop_info(a.name, clips[a.name].get("hands"))
        report["clips"] = clips
        write_animation_table(out, clips)
        bounds = {}
        for tname, spec in ROBOT_TYPES.items():
            cfg = default_config()
            cfg.update(spec["variants"])
            bounds[tname] = type_bounds(part_objs, cfg)
        report["type_bounds"] = bounds
        for n in ("Walk", "Run", "RunFast", "Sprint", "CrouchWalk", "Push", "MudWalk", "Crawl"):
            if n in clips:
                LOG.info("  %-10s natural speed %.2f m/s, footsteps %s", n, clips[n]["naturalSpeed"],
                         [f for f, _ in clips[n]["footsteps"]])
        for n in ("Idle", "Crouch", "CrouchDeep", "CrouchWalk", "SitIdle", "CrawlIdle", "Crawl"):
            if n in clips:
                LOG.info("  %-10s height %.3f..%.3f m", n, clips[n]["height"]["min"], clips[n]["height"]["max"])
        LOG.info("  width/core width: %s", ", ".join("%s %.2f/%.2f" % (k, v["width"], v["coreWidth"])
                                                     for k, v in bounds.items()))
    outputs = OutputCache(None if args.no_cache else os.path.join(out, "cache"), out,
                          None if args.no_cache else robocache.kind_dir("outputs", cache_dir))
    with Step("export fbx"):
        # modular and form FBX have no takes: only the main FBX depends on the clips and the LOD setting
        code = code_digest(EXPORT_CODE_PARTS, FBX_EXPORT_TURN, bpy.app.version_string, rig_digest(arm_ob))
        main_fbx = os.path.join(out, "CubeEscape_Robot.fbx")
        key = hashlib.sha1(repr((code, export_digest(robot_mesh), LOD_TRIANGLES, args.lods,
                                 [(a.name, action_digest(actions[a.name])) for a in ANIMS])).encode()).hexdigest()
        hit = outputs.fresh("main", key)
        report["lods"] = {}
        if hit:
            report["lods"].update(hit["result"] or {})
        else:
            export_fbx(main_fbx, [robot_mesh], arm_ob)
            if args.lods:
                report["lods"]["Robot_Mesh"] = export_lods(main_fbx, robot_mesh, coll, arm_ob)
            outputs.store("main", key, [main_fbx] + ([os.path.join(out, "LOD", "CubeEscape_Robot_LOD%d.fbx" % i)
                                                      for i in (1, 2)] if args.lods else []), report["lods"])
        part_keys = [export_digest(o) for o in part_objs.values()]
        modular = os.path.join(out, "CubeEscape_Robot_Modular.fbx")
        key = hashlib.sha1((code + "".join(part_keys)).encode()).hexdigest()
        if not outputs.fresh("modular", key):
            export_fbx(modular, list(part_objs.values()), arm_ob, animations=False)
            outputs.store("modular", key, [modular])
        form_dir = os.path.join(out, "Forms")
        key = hashlib.sha1((code + "".join(part_keys) + "".join(export_digest(o) for objs in FORM_OBJS.values()
                                                                for o in objs.values())).encode()).hexdigest()
        if os.environ.get("ROBOT_OUTPUT_DIGEST_DUMP"):      # debugging: which object's digest changes between runs
            write_json_atomic(os.environ["ROBOT_OUTPUT_DIGEST_DUMP"],
                              {o.name: export_digest(o) for o in list(part_objs.values()) +
                               [o for objs in FORM_OBJS.values() for o in objs.values()]})
        hit = outputs.fresh("forms", key)
        if hit:
            report["forms"] = hit["result"]
        else:
            report["forms"] = export_forms(form_dir, part_objs, robot_mesh, coll, arm_ob, tex_dir)
            outputs.store("forms", key, [form_dir], report["forms"])
        # the robot types are exported by "fbx" work packages in parallel (export_type_fbx)
        capsules = hit_capsules(robot_mesh, arm_ob)
        report["hit_capsules"] = {c["bone"]: c["coverage"] for c in capsules}
        LOG.info("  hit capsules: %s", ", ".join("%s %.0f%%" % (c["bone"], c["coverage"] * 100) for c in capsules))
        write_config(out, clips, bounds, capsules)
        write_looks(out)
    with Step("viewer data"):
        vcfg = viewer_config()
        vcfg["clips"] = clips
        vcfg["typeBounds"] = bounds
        form_objs = [o for objs in FORM_OBJS.values() for o in objs.values()]
        vdir = os.path.join(out, "ViewerData")
        key = hashlib.sha1(repr((inspect.getsource(export_viewer), json.dumps(vcfg, sort_keys=True, default=str),
                                 [export_digest(o) for o in list(part_objs.values()) + form_objs],
                                 [(a.name, a.frames, action_digest(actions[a.name])) for a in ANIMS],
                                 rig_digest(arm_ob), [(n, _file_digest(os.path.join(tex_dir, n)))
                                                      for n in export_viewer.VIEWER_TEXTURES])).encode()).hexdigest()
        if not outputs.fresh("viewer", key):
            export_viewer.export(vdir, list(part_objs.values()) + form_objs, arm_ob, ANIMS, actions, tex_dir, vcfg,
                                 LOG)
            outputs.store("viewer", key, [vdir])
        if outputs.hits:
            LOG.info("  unchanged outputs kept: %s (cache/outputs.json; --no-cache writes everything)%s",
                     ", ".join(outputs.hits), "; copied from the shared cache: " + ", ".join(outputs.shared_hits)
                     if outputs.shared_hits else "")
    with Step("work packages (main process)"):
        ctx.camera()                # the .blend keeps its preview scene
        count, busy = queue.work(ctx, ["render", "qa"], "main")
        LOG.info("  main process: %d packages in %.1f s", count, busy)
    with Step("wait for workers"):
        worker_stats = wait_workers(procs)
        worker_stats["main"] = {"packages": count, "busy": round(busy, 1), "qa_cached": ctx.qa_hits,
                                "render_cached": RENDER_CACHE["hits"], "rendered": RENDER_CACHE["renders"]}
        results = queue.results()
    exported = {}
    for job, res in results:
        if job["kind"] == "fbx":
            exported.update(res["result"])
    missing = [t for t in ROBOT_TYPES if t not in exported]
    if missing:
        raise RuntimeError("type FBX missing: %s" % missing)
    report["type_fbx"] = {}
    for tname in ROBOT_TYPES:               # report in type order, whichever process exported it
        info = dict(exported[tname])
        lods = info.pop("lods", None)
        if lods:
            report["lods"][tname] = lods
        report["type_fbx"][tname] = info
    with Step("qa results"):
        report["qa"] = merge_qa(results, type_names)
        for name, entry in report["qa"]["Classic"]["actions"].items():
            log_qa_entry("Classic", name, entry)
        for tname in [t for t in report["qa"] if t != "Classic"]:      # quick: also the owners of type emotes
            qa_t = report["qa"][tname]
            bad = [n for n, e in qa_t["actions"].items() if qa_problem(e)]
            LOG.log(logging.WARNING if bad else logging.INFO, "  QA[%s] %d clips, problems: %s", tname,
                    len(qa_t["actions"]), bad or "-")
    if not args.no_render:
        with Step("preview sheets"):
            finish_previews(results, preview_dir, report, tex_dir)
    ctx.cleanup()
    shutil.rmtree(queue.path, ignore_errors=True)
    with Step("save blend"):
        assign_action(arm_ob, actions["Idle"])
        bpy.context.scene.frame_set(0)
        preview_coll.hide_render = False
        bpy.context.scene.frame_end = 120
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "CubeEscape_Robot.blend"))
    total = time.time() - t_start
    kinds = collections.defaultdict(float)
    for job, res in results:
        kinds[job["kind"]] += res["seconds"]
    load = (round(load_start, 1), round(os.getloadavg()[0], 1))
    report["timings"] = {"steps": dict(STEP_TIMES), "total": round(total, 1), "budget": args.budget,
                         "load": {"start": load[0], "end": load[1], "cores": os.cpu_count()},
                         "packages": {k: round(v, 1) for k, v in kinds.items()}, "workers": worker_stats,
                         "qa_cached": sum(w.get("qa_cached", 0) for w in worker_stats.values()),
                         "render_cached": sum(w.get("render_cached", 0) for w in worker_stats.values()),
                         "rendered": sum(w.get("rendered", 0) for w in worker_stats.values())}
    warm = RUN_STATE["bake_hit"] and not report["timings"]["rendered"] and not RUN_STATE["clips_measured"]
    band = timing_band(None if args.no_cache else os.path.join(out, "cache"), args.tier, "warm" if warm else "cold",
                       total, load[0])
    report["timings"]["band"] = band
    pruned = robocache.prune(cache_dir)
    report["cache"] = {"root": cache_dir, "bytes": pruned["total"], "pruned": pruned["removed"]}
    write_report(out, report)
    slow = sorted(STEP_TIMES.items(), key=lambda kv: -kv[1])[:4]
    ref = ("band up to %.0f s (median %.0f s of %d quiet runs)" % (band["limit"], band["median"], band["reference_runs"])
           if "limit" in band else "%d reference runs" % band["reference_runs"])
    LOG.log(logging.WARNING if band["verdict"] == "slower" else logging.INFO,
            "time: %.0f s (tier %s, %s caches, %s load; target %d s) - %s: %s - slowest: %s", total, args.tier,
            band["caches"], band["load"], args.budget, ref, band["verdict"].upper(),
            ", ".join("%s %.0f s" % kv for kv in slow))
    busy = load[0] > os.cpu_count() / 2       # at the start: other processes (the end includes our own workers)
    LOG.log(logging.WARNING if busy else logging.INFO, "  system load %.1f at the start (%.1f at the end) on %d cores%s",
            load[0], load[1], os.cpu_count(), " - other processes were running, times are not comparable" if busy else "")
    LOG.info("  work packages (process seconds): %s", ", ".join("%s %.0f" % kv for kv in sorted(kinds.items())))
    if report["timings"]["render_cached"]:
        LOG.info("  render cache: %d images reused, %d rendered (cache/render)", report["timings"]["render_cached"],
                 report["timings"]["rendered"])
    if report["timings"]["qa_cached"]:
        LOG.info("  QA cache: %d clip results reused (cache/qa; --no-cache checks everything again)",
                 report["timings"]["qa_cached"])
    if pruned["skipped"]:
        LOG.info("  shared cache: pruning skipped (another run prunes right now)")
    else:
        LOG.info("  shared cache: %.2f GB of %.1f GB%s", pruned["total"] / 1024 ** 3, pruned["limit"] / 1024 ** 3,
                 ", removed %d least recently used entries (%.0f MB)" % (pruned["removed"], pruned["freed"] / 1024 ** 2)
                 if pruned["removed"] else "")
    problems = bool(floating)
    for label, qa in report.get("qa", {}).items():
        for name, entry in qa["actions"].items():
            if qa_problem(entry):
                problems = True
    LOG.info("finished %s", "WITH QA WARNINGS" if problems else "OK")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001 - log everything for the CLI user
        LOG.critical("Unhandled exception:\n%s", traceback.format_exc())
        sys.exit(1)
    sys.exit(code)
