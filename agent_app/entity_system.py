import base64
import copy
import json
import math
import struct
import uuid


# =========================
# Skeleton / class templates
# =========================

def _bone(name, parent=None, head=(0, 0, 0), tail=(0, 0, 0), length=None, roll=0.0):
    if length is None:
        dx = tail[0]-head[0]; dy = tail[1]-head[1]; dz = tail[2]-head[2]
        length = math.sqrt(dx*dx + dy*dy + dz*dz)
    return {
        "name": name,
        "parent": parent,
        "head": [float(head[0]), float(head[1]), float(head[2])],
        "tail": [float(tail[0]), float(tail[1]), float(tail[2])],
        "length": float(length),
        "roll": float(roll),
        "rest_transform": {"position": [float(head[0]), float(head[1]), float(head[2])], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
    }


def human_skeleton_template(detail="full"):
    """Humanoid iskelet: Y-up, metre. detail: basic|full"""
    bones = []
    # core
    bones += [
        _bone("root", None, (0, 0, 0), (0, 0.95, 0)),
        _bone("pelvis", "root", (0, 0.95, 0), (0, 1.02, 0)),
        _bone("spine_01", "pelvis", (0, 1.02, 0), (0, 1.12, 0)),
        _bone("spine_02", "spine_01", (0, 1.12, 0), (0, 1.24, 0)),
        _bone("spine_03", "spine_02", (0, 1.24, 0), (0, 1.36, 0)),
        _bone("chest", "spine_03", (0, 1.36, 0), (0, 1.46, 0)),
        _bone("neck", "chest", (0, 1.46, 0), (0, 1.56, 0)),
        _bone("head", "neck", (0, 1.56, 0), (0, 1.78, 0)),
    ]
    # left arm
    bones += [
        _bone("clavicle_l", "chest", (0.06, 1.42, 0), (0.18, 1.40, 0)),
        _bone("upper_arm_l", "clavicle_l", (0.18, 1.40, 0), (0.46, 1.40, 0)),
        _bone("lower_arm_l", "upper_arm_l", (0.46, 1.40, 0), (0.72, 1.40, 0)),
        _bone("hand_l", "lower_arm_l", (0.72, 1.40, 0), (0.82, 1.40, 0)),
    ]
    # right arm
    bones += [
        _bone("clavicle_r", "chest", (-0.06, 1.42, 0), (-0.18, 1.40, 0)),
        _bone("upper_arm_r", "clavicle_r", (-0.18, 1.40, 0), (-0.46, 1.40, 0)),
        _bone("lower_arm_r", "upper_arm_r", (-0.46, 1.40, 0), (-0.72, 1.40, 0)),
        _bone("hand_r", "lower_arm_r", (-0.72, 1.40, 0), (-0.82, 1.40, 0)),
    ]
    # legs
    bones += [
        _bone("upper_leg_l", "pelvis", (0.10, 0.95, 0), (0.10, 0.50, 0)),
        _bone("lower_leg_l", "upper_leg_l", (0.10, 0.50, 0), (0.10, 0.08, 0)),
        _bone("foot_l", "lower_leg_l", (0.10, 0.08, 0), (0.10, 0.02, 0.14)),
        _bone("toes_l", "foot_l", (0.10, 0.02, 0.14), (0.10, 0.02, 0.20)),
        _bone("upper_leg_r", "pelvis", (-0.10, 0.95, 0), (-0.10, 0.50, 0)),
        _bone("lower_leg_r", "upper_leg_r", (-0.10, 0.50, 0), (-0.10, 0.08, 0)),
        _bone("foot_r", "lower_leg_r", (-0.10, 0.08, 0), (-0.10, 0.02, 0.14)),
        _bone("toes_r", "foot_r", (-0.10, 0.02, 0.14), (-0.10, 0.02, 0.20)),
    ]
    if detail == "full":
        # simplified fingers (3 segments each) for both hands
        for side, sign, base_x in [("l", 1, 0.82), ("r", -1, -0.82)]:
            fingers = ["thumb", "index", "middle", "ring", "pinky"]
            offsets = [0.03, 0.015, 0.0, -0.015, -0.03]
            lengths = [0.045, 0.055, 0.06, 0.055, 0.045]
            for fname, oz, seglen in zip(fingers, offsets, lengths):
                p = f"hand_{side}"
                x = base_x
                y = 1.40
                z = oz
                for i in range(1, 4):
                    bname = f"{fname}_{i:02d}_{side}"
                    nx = x + sign * seglen
                    bones.append(_bone(bname, p, (x, y, z), (nx, y, z)))
                    p = bname
                    x = nx
    return {"root": "root", "bones": bones, "version": 2, "detail": detail}


def normalize_physics(phys=None):
    p = copy.deepcopy(phys) if isinstance(phys, dict) else {}
    defaults = {
        "enabled": False,
        "body": "static",
        "mass": 1.0,
        "collider": None,
        "constraints": [],
        "gravity": [0.0, -9.81, 0.0],
        "damping": 0.1,
        "velocity": [0.0, 0.0, 0.0],
        "angular_velocity": [0.0, 0.0, 0.0],
        "last_impulse": None,
    }
    for k, v in defaults.items():
        p.setdefault(k, copy.deepcopy(v) if isinstance(v, (list, dict)) else v)
    try:
        p["mass"] = float(p.get("mass", 1.0) or 1.0)
    except Exception:
        p["mass"] = 1.0
    try:
        p["damping"] = max(0.0, min(1.0, float(p.get("damping", 0.1))))
    except Exception:
        p["damping"] = 0.1
    for vec_key in ("gravity", "velocity", "angular_velocity"):
        val = p.get(vec_key)
        if not (isinstance(val, list) and len(val) == 3):
            p[vec_key] = copy.deepcopy(defaults[vec_key])
        else:
            try:
                p[vec_key] = [float(val[0]), float(val[1]), float(val[2])]
            except Exception:
                p[vec_key] = copy.deepcopy(defaults[vec_key])
    return p


def normalize_animation(anim=None):
    a = copy.deepcopy(anim) if isinstance(anim, dict) else {}
    a.setdefault("clips", [])
    a.setdefault("active", None)
    a.setdefault("active_action", a.get("active"))
    a.setdefault("pose", {})
    a.setdefault("keyframes", [])
    if not isinstance(a.get("clips"), list):
        a["clips"] = []
    if not isinstance(a.get("pose"), dict):
        a["pose"] = {}
    if not isinstance(a.get("keyframes"), list):
        a["keyframes"] = []
    return a


def default_components_for_class(entity_class):
    tpl = copy.deepcopy(entity_class.components_template or {})
    skel = copy.deepcopy(entity_class.skeleton_template or {})
    base = {
        "identity": {
            "stable_id": str(uuid.uuid4()),
            "label": entity_class.name,
            "locked_fields": [],
            "class_id": str(entity_class.id),
            "class_name": entity_class.name,
        },
        "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "mesh": {"asset_id": None, "primitive": None, "generated": False},
        "skeleton": skel or {"root": None, "bones": []},
        "animation": normalize_animation({"clips": [], "active": None, "pose": {}, "keyframes": []}),
        "physics": normalize_physics({"enabled": False, "body": "dynamic", "mass": 70.0, "collider": "capsule", "constraints": []}),
        "render": {"visible": True, "material": {"color": [0.8, 0.8, 0.85, 1.0]}},
        "parameters": copy.deepcopy(entity_class.parameters or {}),
    }
    for k, v in tpl.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        elif v is not None:
            base[k] = v
    if skel and (not base.get("skeleton") or not base["skeleton"].get("bones")):
        base["skeleton"] = skel
    base["animation"] = normalize_animation(base.get("animation"))
    base["physics"] = normalize_physics(base.get("physics"))
    # ensure identity locked_fields preserved/merged
    if isinstance(tpl.get("identity"), dict) and "locked_fields" in tpl["identity"]:
        base["identity"]["locked_fields"] = list(tpl["identity"].get("locked_fields") or [])
    if "stable_id" not in (base.get("identity") or {}):
        base.setdefault("identity", {})["stable_id"] = str(uuid.uuid4())
    return base


SYSTEM_ENTITY_CLASSES = [
    {
        "name": "Human",
        "slug": "human",
        "category": "human",
        "description": "Detayli insan karakteri: tam vucut + el parmak kemikleri, 3D prosedurel render, fizik/animasyon hazir.",
        "skeleton_template": human_skeleton_template(detail="full"),
        "components_template": {
            "identity": {"label": "Human", "locked_fields": ["face", "body_proportions"]},
            "physics": {"enabled": True, "body": "dynamic", "mass": 75.0, "collider": "humanoid", "constraints": []},
            "animation": {"clips": [], "active": None, "pose": {}, "keyframes": []},
            "render": {"visible": True, "material": {"color": [0.86, 0.76, 0.68, 1.0]}},
        },
        "render_template": {"type": "humanoid_procedural", "lod": "proxy", "detail": "full"},
        "parameters": {"height": 1.75, "build": "medium", "skin_color": [0.86, 0.76, 0.68, 1.0]},
    },
    {
        "name": "Sword",
        "slug": "sword",
        "category": "weapon",
        "description": "Kilic prop'u: fizik ve aksiyon parametreleri.",
        "skeleton_template": {"root": None, "bones": []},
        "components_template": {
            "identity": {"label": "Sword", "locked_fields": ["blade_shape"]},
            "physics": {"enabled": True, "body": "dynamic", "mass": 2.0, "collider": "box", "constraints": []},
            "render": {"visible": True, "material": {"color": [0.75, 0.78, 0.82, 1.0]}},
        },
        "render_template": {"type": "box", "size": [0.05, 0.9, 0.02], "center": [0, 0.45, 0]},
        "parameters": {"length": 0.9},
    },
    {
        "name": "Cube",
        "slug": "cube",
        "category": "prop",
        "description": "Temel kup nesne.",
        "skeleton_template": {"root": None, "bones": []},
        "components_template": {
            "identity": {"label": "Cube", "locked_fields": []},
            "physics": {"enabled": True, "body": "dynamic", "mass": 1.0, "collider": "box", "constraints": []},
            "render": {"visible": True, "material": {"color": [0.6, 0.7, 0.9, 1.0]}},
        },
        "render_template": {"type": "box", "size": [1, 1, 1], "center": [0, 0.5, 0]},
        "parameters": {},
    },
]


def ensure_system_entity_classes(user=None):
    """Sabit şablonları zorla uygulamaz (şablonsuz/öğrenen sistem).

    Eski sabit sistem sınıfları varsa dokunulmaz; yeni sabit preset eklenmez/üzerine yazılmaz.
    Dinamik öğrenim `learned_models.research_and_learn_entity` ile sağlanır.
    """
    return []


# =========================
# Minimal glTF generator
# =========================

def _box_mesh_data(cx, cy, cz, sx, sy, sz):
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    corners = [
        (cx-hx, cy-hy, cz-hz), (cx+hx, cy-hy, cz-hz), (cx+hx, cy+hy, cz-hz), (cx-hx, cy+hy, cz-hz),
        (cx-hx, cy-hy, cz+hz), (cx+hx, cy-hy, cz+hz), (cx+hx, cy+hy, cz+hz), (cx-hx, cy+hy, cz+hz),
    ]
    faces = [
        (4,5,6,4,6,7,(0,0,1)), (1,0,3,1,3,2,(0,0,-1)),
        (5,1,2,5,2,6,(1,0,0)), (0,4,7,0,7,3,(-1,0,0)),
        (3,7,6,3,6,2,(0,1,0)), (0,1,5,0,5,4,(0,-1,0)),
    ]
    positions, normals = [], []
    for f in faces:
        idxs = f[0:6]; n = f[6]
        for i in idxs:
            p = corners[i]
            positions.extend([p[0], p[1], p[2]])
            normals.extend([n[0], n[1], n[2]])
    return positions, normals


def _make_gltf(primitive_boxes, name="Model", skeleton=None, rigged=False, scale_y=1.0):
    if not primitive_boxes:
        primitive_boxes = [{"center": [0, 0.5, 0], "size": [1, 1, 1], "name": "box"}]

    bin_chunks = bytearray()
    buffer_views, accessors, meshes = [], [], []

    def add_mesh_for_box(box, center_local):
        c = center_local
        s = box.get("size", [1, 1, 1])
        pos, nor = _box_mesh_data(c[0], c[1], c[2], s[0], s[1], s[2])
        pos_bytes = struct.pack("<" + "f" * len(pos), *pos)
        nor_bytes = struct.pack("<" + "f" * len(nor), *nor)

        pos_offset = len(bin_chunks); bin_chunks.extend(pos_bytes); pos_len = len(pos_bytes)
        nor_offset = len(bin_chunks); bin_chunks.extend(nor_bytes); nor_len = len(nor_bytes)

        xs = pos[0::3]; ys = pos[1::3]; zs = pos[2::3]
        pmin = [min(xs), min(ys), min(zs)] if xs else [0, 0, 0]
        pmax = [max(xs), max(ys), max(zs)] if xs else [0, 0, 0]
        vcount = len(pos) // 3

        bv_pos = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": pos_offset, "byteLength": pos_len, "target": 34962})
        bv_nor = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": nor_offset, "byteLength": nor_len, "target": 34962})

        acc_pos = len(accessors)
        accessors.append({"bufferView": bv_pos, "componentType": 5126, "count": vcount, "type": "VEC3", "min": pmin, "max": pmax})
        acc_nor = len(accessors)
        accessors.append({"bufferView": bv_nor, "componentType": 5126, "count": vcount, "type": "VEC3"})

        mesh_idx = len(meshes)
        meshes.append({"name": box.get("name", f"mesh_{mesh_idx}"), "primitives": [{"attributes": {"POSITION": acc_pos, "NORMAL": acc_nor}, "mode": 4}]})
        return mesh_idx

    # Rigid bone hierarchy mode for humanoid procedural models:
    # - bone nodes are named (pose application targets) and parented per skeleton
    # - each bone's box mesh is attached to its bone node with local geometry offset
    bones = (skeleton or {}).get("bones") if isinstance(skeleton, dict) else None
    if rigged and isinstance(bones, list) and bones:
        bone_by_name = {b.get("name"): b for b in bones if isinstance(b, dict) and b.get("name")}
        # map boxes by bone/part name
        box_by_name = {}
        for b in primitive_boxes:
            nm = b.get("name") or ""
            if nm and nm not in box_by_name:
                box_by_name[nm] = b

        # precompute joint world positions (scaled y like humanoid_boxes_from_skeleton)
        joint_pos = {}
        for bn, b in bone_by_name.items():
            h = b.get("head") or [0, 0, 0]
            joint_pos[bn] = [float(h[0]), float(h[1]) * float(scale_y or 1.0), float(h[2])]

        nodes = []
        node_index = {}
        children_map = {}

        def ensure_bone_node(bn):
            if bn in node_index:
                return node_index[bn]
            b = bone_by_name.get(bn) or {}
            parent = b.get("parent")
            wp = joint_pos.get(bn, [0.0, 0.0, 0.0])
            if parent and parent in bone_by_name:
                pp = joint_pos.get(parent, [0.0, 0.0, 0.0])
                local_t = [wp[0] - pp[0], wp[1] - pp[1], wp[2] - pp[2]]
            else:
                local_t = [wp[0], wp[1], wp[2]]
            idx = len(nodes)
            nodes.append({"name": bn, "translation": local_t, "children": []})
            node_index[bn] = idx
            # ensure parent exists and linkage
            if parent and parent in bone_by_name:
                pidx = ensure_bone_node(parent)
                if idx not in nodes[pidx]["children"]:
                    nodes[pidx]["children"].append(idx)
            return idx

        # create bone nodes and attach meshes
        for bn in bone_by_name.keys():
            ensure_bone_node(bn)
        for bn, idx in node_index.items():
            box = box_by_name.get(bn)
            if not box:
                continue
            wp = joint_pos.get(bn, [0.0, 0.0, 0.0])
            wc = box.get("center") or [0, 0, 0]
            local_center = [float(wc[0]) - wp[0], float(wc[1]) - wp[1], float(wc[2]) - wp[2]]
            mesh_idx = add_mesh_for_box(box, local_center)
            nodes[idx]["mesh"] = mesh_idx

        # include any boxes that don't map to a bone as separate root nodes
        scene_nodes = []
        roots = []
        for bn, idx in node_index.items():
            b = bone_by_name.get(bn) or {}
            parent = b.get("parent")
            if not parent or parent not in bone_by_name:
                roots.append(idx)
        for i, box in enumerate(primitive_boxes):
            nm = box.get("name") or ""
            if nm in node_index:
                continue
            mesh_idx = add_mesh_for_box(box, box.get("center") or [0, 0, 0])
            nidx = len(nodes)
            nodes.append({"name": nm or f"node_{i}", "mesh": mesh_idx})
            roots.append(nidx)
        scene_nodes = roots if roots else list(range(len(nodes)))
        # cleanup empty children arrays for compactness
        for n in nodes:
            if isinstance(n.get("children"), list) and not n["children"]:
                n.pop("children", None)
    else:
        # Flat mode (backward-compatible)
        nodes = []
        scene_nodes = []
        for i, box in enumerate(primitive_boxes):
            c = box.get("center", [0, 0, 0])
            mesh_idx = add_mesh_for_box(box, c)
            node_idx = len(nodes)
            nodes.append({"name": box.get("name", f"node_{i}"), "mesh": mesh_idx})
            scene_nodes.append(node_idx)

    while len(bin_chunks) % 4 != 0:
        bin_chunks.append(0)

    b64 = base64.b64encode(bytes(bin_chunks)).decode("ascii")
    uri = "data:application/octet-stream;base64," + b64
    return {
        "asset": {"version": "2.0", "generator": "EMOG EntitySystem"},
        "scene": 0,
        "scenes": [{"name": name, "nodes": scene_nodes}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_chunks), "uri": uri}],
    }


def humanoid_boxes_from_skeleton(skeleton, params=None):
    params = params or {}
    height = float(params.get("height", 1.75) or 1.75)
    build = str(params.get("build", "medium") or "medium").lower()
    scale_y = max(0.5, min(2.5, height / 1.75))
    width_scale = 1.15 if build in ("heavy", "large", "strong") else 0.9 if build in ("slim", "light") else 1.0
    bones = (skeleton or {}).get("bones") or []
    by_name = {b.get("name"): b for b in bones}
    boxes = []

    def add(name, size, color=(0.86, 0.76, 0.68, 1.0)):
        b = by_name.get(name)
        if b:
            h = b.get("head", [0,0,0]); t = b.get("tail", h)
            cx = (h[0]+t[0])/2.0; cy = ((h[1]+t[1])/2.0) * scale_y
            cz = (h[2]+t[2])/2.0
        else:
            cx, cy, cz = 0, 0.5*scale_y, 0
        sx, sy, sz = size
        boxes.append({"center":[cx, cy, cz], "size":[sx*width_scale, sy*scale_y, sz*width_scale], "color":list(color), "name":name})

    add("head", [0.22, 0.24, 0.22])
    add("chest", [0.34, 0.28, 0.20])
    add("spine_03", [0.30, 0.14, 0.18])
    add("spine_02", [0.30, 0.14, 0.18])
    add("spine_01", [0.30, 0.12, 0.18])
    add("pelvis", [0.32, 0.16, 0.20])
    # arms
    for side in ("l", "r"):
        add(f"upper_arm_{side}", [0.26, 0.10, 0.10])
        add(f"lower_arm_{side}", [0.24, 0.09, 0.09])
        add(f"hand_{side}", [0.10, 0.08, 0.06])
    # legs
    for side in ("l", "r"):
        add(f"upper_leg_{side}", [0.12, 0.46, 0.12])
        add(f"lower_leg_{side}", [0.11, 0.44, 0.11])
        add(f"foot_{side}", [0.10, 0.06, 0.18])
    # simplified fingers (aggregate small boxes) if full detail
    if (skeleton or {}).get("detail") == "full":
        for b in bones:
            n = b.get("name", "")
            if n.startswith(("thumb_", "index_", "middle_", "ring_", "pinky_")) and n.endswith(("_l", "_r")):
                h = b.get("head", [0,0,0]); t = b.get("tail", h)
                cx = (h[0]+t[0])/2.0; cy = ((h[1]+t[1])/2.0) * scale_y; cz = (h[2]+t[2])/2.0
                boxes.append({"center":[cx,cy,cz], "size":[0.045, 0.02*scale_y, 0.02], "color":[0.86,0.76,0.68,1.0], "name":n})
    return boxes


def _boxes_from_parts_or_hierarchy(entity_class, params=None):
    """Araştırma ile öğrenilmiş/dinamik spec'ten yaklaşık görsel parçalar üretir."""
    params = params or {}
    rt = (entity_class.render_template or {}) if hasattr(entity_class, "render_template") else {}
    ct = (entity_class.components_template or {}) if hasattr(entity_class, "components_template") else {}
    skel = (entity_class.skeleton_template or {}) if hasattr(entity_class, "skeleton_template") else {}
    color = (((ct.get("render") or {}).get("material") or {}).get("color")) or [0.75, 0.75, 0.8, 1.0]
    parts = []
    # explicit render parts
    for p in (rt.get("parts") or []):
        if isinstance(p, dict):
            parts.append(p)
        elif isinstance(p, str):
            parts.append({"name": p})
    # mesh parts
    mesh = ct.get("mesh") or {}
    for p in (mesh.get("parts") or []):
        if isinstance(p, dict):
            parts.append(p)
        elif isinstance(p, str):
            parts.append({"name": p})
    # hierarchy-derived parts (prefer upper/mid layers for visible assembly)
    hier = skel.get("hierarchy") or {}
    layers = hier.get("layers") if isinstance(hier, dict) else None
    if isinstance(layers, list) and layers:
        # pick elements from layers above fundamental, bottom-up assembly
        for layer in sorted(layers, key=lambda x: (x.get("level", 0) if isinstance(x, dict) else 0)):
            if not isinstance(layer, dict):
                continue
            lvl = layer.get("level", 0)
            for el in (layer.get("elements") or []):
                if isinstance(el, dict):
                    parts.append({"name": el.get("name") or el.get("id") or f"part_l{lvl}", "layer": lvl, **{k:v for k,v in el.items() if k in ('size','center','color')}})
                elif isinstance(el, str):
                    parts.append({"name": el, "layer": lvl})
    # de-dup by name preserving order
    seen = set(); uniq = []
    for p in parts:
        n = (p.get("name") if isinstance(p, dict) else str(p)) or "part"
        if n in seen:
            continue
        seen.add(n); uniq.append(p if isinstance(p, dict) else {"name": n})
    if not uniq:
        return [{"center":[0,0.5,0], "size":[1,1,1], "color":list(color), "name":"body"}]
    # layout parts as a simple bottom-up stack/cluster for visualization
    boxes = []
    n = len(uniq)
    cols = 1 if n <= 4 else 2 if n <= 9 else 3
    spacing_x, spacing_y, spacing_z = 0.7, 0.5, 0.7
    base_y = 0.25
    for i, p in enumerate(uniq[:24]):
        name = p.get("name") or f"part_{i}"
        size = p.get("size") or [0.4, 0.4, 0.4]
        if not (isinstance(size, list) and len(size) == 3):
            size = [0.4, 0.4, 0.4]
        cx = ((i % cols) - (cols - 1) / 2.0) * spacing_x
        cz = ((i // cols) % cols - (cols - 1) / 2.0) * spacing_z
        cy = base_y + (i // (cols * cols)) * spacing_y + size[1] / 2.0
        center = p.get("center") or [cx, cy, cz]
        pcolor = p.get("color") or color
        boxes.append({"center": list(center), "size": list(size), "color": list(pcolor), "name": str(name)[:60]})
    return boxes


def generate_gltf_for_entity(entity_class, instance_name=None, params=None):
    params = params or {}
    rt = (entity_class.render_template or {})
    rtype = rt.get("type") or ("humanoid_procedural" if entity_class.category in ("human", "character", "creature") else "auto")
    name = instance_name or entity_class.name
    skel = entity_class.skeleton_template or {}

    # Prefer explicit humanoid procedural only when skeleton bones exist in expected form
    bones = skel.get("bones") if isinstance(skel, dict) else None
    if rtype == "humanoid_procedural" and isinstance(bones, list) and bones:
        if params.get("detail") and isinstance(skel, dict):
            skel = {**skel, "detail": params.get("detail")}
        height = float(params.get("height", 1.75) or 1.75)
        scale_y = max(0.5, min(2.5, height / 1.75))
        boxes = humanoid_boxes_from_skeleton(skel, params)
        # Rigged hierarchy: named bone nodes with parent-child transforms so pose
        # rotations in Studio apply around joints (rigid segmented deformation).
        gltf = _make_gltf(boxes, name=name, skeleton=skel, rigged=True, scale_y=scale_y)
    elif rtype == "box":
        size = list(rt.get("size") or [1, 1, 1])
        center = list(rt.get("center") or [0, size[1]/2.0, 0])
        color = (((entity_class.components_template or {}).get("render") or {}).get("material") or {}).get("color") or [0.7,0.7,0.8,1]
        gltf = _make_gltf([{ "center": center, "size": size, "color": color, "name": name }], name=name)
    else:
        # Dynamic/researched/auto: build from learned parts/hierarchy (bottom-up) for any class
        boxes = _boxes_from_parts_or_hierarchy(entity_class, params)
        gltf = _make_gltf(boxes, name=name)
    return gltf


def write_gltf_asset(gltf_dict, user, scene=None, base_name="model"):
    from django.core.files.base import ContentFile
    from .models import SceneAsset
    safe = "".join(c for c in (base_name or "model") if c.isalnum() or c in ("-","_"))[:40] or "model"
    filename = f"{safe}-{uuid.uuid4().hex[:8]}.gltf"
    content = json.dumps(gltf_dict, ensure_ascii=False).encode("utf-8")
    asset = SceneAsset(user=user, scene=scene, name=base_name or "Model", file_type="gltf", metadata={"generator":"entity_system","format":"gltf"})
    asset.file.save(filename, ContentFile(content), save=True)
    return asset


def instantiate_entity_class(entity_class, user, scene, name=None, parameters=None, place=True):
    """Create EntityInstance (+ rendered asset + SceneNode) for a class."""
    from .models import EntityInstance, SceneNode
    parameters = parameters or {}
    inst_name = (name or entity_class.name)[:200]
    components = default_components_for_class(entity_class)
    # apply parameter variations
    if parameters:
        components.setdefault("parameters", {}).update(parameters)
        # height/build can influence identity hints but we respect locked_fields
        locked = set((components.get("identity") or {}).get("locked_fields") or [])
        if "height" in parameters and "body_proportions" not in locked:
            components.setdefault("meta", {})["height"] = parameters.get("height")
        if "build" in parameters and "body_proportions" not in locked:
            components.setdefault("meta", {})["build"] = parameters.get("build")
    instance = EntityInstance.objects.create(
        user=user,
        entity_class=entity_class,
        scene=scene if place else None,
        name=inst_name,
        status="draft",
        components=components,
        parameters=parameters,
    )
    gltf = generate_gltf_for_entity(entity_class, instance_name=inst_name, params=parameters)
    asset = write_gltf_asset(gltf, user=user, scene=scene, base_name=inst_name)
    instance.asset = asset
    instance.status = "rendered"
    components.setdefault("mesh", {})["asset_id"] = str(asset.id)
    components["mesh"]["generated"] = True
    components["mesh"]["format"] = "gltf"
    # keep skeleton data explicit in components
    components["skeleton"] = copy.deepcopy(entity_class.skeleton_template or components.get("skeleton") or {})
    instance.components = components

    node = None
    if place and scene is not None:
        # Kök düğüm (bütün) + bottom-up hiyerarşiyi temsil eden kompozit alt düğümler
        root_components = copy.deepcopy(components)
        root_components.setdefault("composition", {"type": "bottom_up", "role": "whole", "children": []})
        node = SceneNode.objects.create(
            scene=scene,
            asset=asset,
            name=inst_name,
            node_type="group" if entity_class.category not in ("camera","light") else entity_class.category,
            components=root_components,
            visible=True,
        )
        # Alt düğümler: her katman için (en dipten yukarı) temsilci bir kompozisyon düğümü
        skel = components.get("skeleton") or {}
        hier = skel.get("hierarchy") if isinstance(skel, dict) else {}
        layers = hier.get("layers") if isinstance(hier, dict) else []
        child_ids = []
        if isinstance(layers, list) and layers:
            for layer in sorted([l for l in layers if isinstance(l, dict)], key=lambda x: int(x.get("level", 0))):
                lvl = int(layer.get("level", 0))
                lname = str(layer.get("name") or f"layer_{lvl}")
                elements = layer.get("elements") or []
                elem_names = []
                for e in elements[:6]:
                    if isinstance(e, dict) and e.get("name"):
                        elem_names.append(str(e.get("name")))
                    elif isinstance(e, str):
                        elem_names.append(e)
                child_comp = copy.deepcopy(components)
                child_comp["identity"] = {**(components.get("identity") or {}), "label": f"{inst_name}::{lname}", "part_role": lname, "layer": lvl}
                child_comp["composition"] = {"type": "bottom_up_layer", "level": lvl, "name": lname, "elements": elements[:10], "parent_instance": inst_name}
                # görsel mesh kökte; alt düğümler yapısal/semantik kompozisyonu temsil eder
                child_comp.setdefault("mesh", {})
                child_comp["mesh"]["asset_id"] = None
                child_comp["mesh"]["generated"] = False
                child = SceneNode.objects.create(
                    scene=scene,
                    parent=node,
                    asset=None,
                    name=f"{inst_name} / L{lvl}:{lname}",
                    node_type="object",
                    components=child_comp,
                    visible=True,
                    sort_order=lvl,
                )
                child_ids.append(str(child.id))
        # kök composition güncelle
        root_components["composition"]["children"] = child_ids
        node.components = root_components
        node.save()
        instance.node = node
        instance.scene = scene
        instance.status = "placed"
    instance.save()
    return instance, asset, node


# =========================
# Actions / animation / physics helpers (practical, timeline-ready)
# =========================

POSE_PRESETS = {
    "t_pose": {},
    "idle": {},
    "wave": {
        "upper_arm_r": {"rotation": [-1.0, 0.0, 0.4]},
        "lower_arm_r": {"rotation": [-0.4, 0.0, 0.2]},
        "hand_r": {"rotation": [0.0, 0.0, 0.2]},
    },
    "crouch": {
        "spine_01": {"rotation": [0.35, 0, 0]},
        "upper_leg_l": {"rotation": [-0.8, 0, 0]},
        "lower_leg_l": {"rotation": [1.1, 0, 0]},
        "upper_leg_r": {"rotation": [-0.8, 0, 0]},
        "lower_leg_r": {"rotation": [1.1, 0, 0]},
    },
    "jump": {
        "spine_02": {"rotation": [-0.15, 0, 0]},
        "upper_leg_l": {"rotation": [-0.2, 0, 0]},
        "upper_leg_r": {"rotation": [-0.2, 0, 0]},
        "lower_leg_l": {"rotation": [0.2, 0, 0]},
        "lower_leg_r": {"rotation": [0.2, 0, 0]},
    },
    "look_left": {"neck": {"rotation": [0, 0.5, 0]}, "head": {"rotation": [0, 0.25, 0]}},
    "look_right": {"neck": {"rotation": [0, -0.5, 0]}, "head": {"rotation": [0, -0.25, 0]}},
}


def get_pose_preset(name):
    return copy.deepcopy(POSE_PRESETS.get(str(name or "").strip().lower()))


ACTION_TEMPLATES = {
    "idle": {"label": "Bekle", "type": "animation", "targets": [], "pose_delta": {}, "reset_pose": True, "duration": 0.5},
    "walk": {
        "label": "Yürü",
        "type": "animation",
        "targets": ["upper_leg_l", "lower_leg_l", "upper_leg_r", "lower_leg_r"],
        "pose_delta": {"upper_leg_l": {"rotation": [0.4, 0, 0]}, "lower_leg_l": {"rotation": [-0.2, 0, 0]}, "upper_leg_r": {"rotation": [-0.4, 0, 0]}, "lower_leg_r": {"rotation": [0.2, 0, 0]}},
        "duration": 1.0,
    },
    "run": {
        "label": "Koş",
        "type": "animation",
        "targets": ["upper_leg_l", "lower_leg_l", "upper_leg_r", "lower_leg_r", "spine_02"],
        "pose_delta": {"upper_leg_l": {"rotation": [0.7, 0, 0]}, "lower_leg_l": {"rotation": [-0.4, 0, 0]}, "upper_leg_r": {"rotation": [-0.7, 0, 0]}, "lower_leg_r": {"rotation": [0.4, 0, 0]}, "spine_02": {"rotation": [0.15, 0, 0]}},
        "physics_impulse": {"enabled": True, "force": [0, 0, 1.5], "duration": 0.6},
        "duration": 0.8,
    },
    "raise_arm": {
        "label": "Kol kaldır",
        "type": "animation",
        "targets": ["upper_arm_r", "lower_arm_r", "hand_r"],
        "pose_delta": {"upper_arm_r": {"rotation": [-1.2, 0, 0]}, "lower_arm_r": {"rotation": [-0.2, 0, 0]}},
        "duration": 0.6,
    },
    "wave": {
        "label": "El salla",
        "type": "animation",
        "targets": ["upper_arm_r", "lower_arm_r", "hand_r"],
        "pose_delta": POSE_PRESETS["wave"],
        "duration": 0.8,
    },
    "swing_sword": {
        "label": "Kılıçla vur",
        "type": "animation+physics",
        "targets": ["upper_arm_r", "lower_arm_r", "hand_r", "spine_02"],
        "pose_delta": {"upper_arm_r": {"rotation": [0.0, -0.8, 0.0]}, "lower_arm_r": {"rotation": [0.0, -1.2, 0.0]}, "spine_02": {"rotation": [0.0, -0.2, 0.0]}},
        "physics_impulse": {"enabled": True, "force": [0, 0, -6.0], "torque": [0, -1.2, 0], "duration": 0.4},
        "duration": 0.6,
    },
    "jump": {
        "label": "Zıpla",
        "type": "animation+physics",
        "targets": ["spine_02", "upper_leg_l", "lower_leg_l", "upper_leg_r", "lower_leg_r"],
        "pose_delta": POSE_PRESETS["jump"],
        "physics_impulse": {"enabled": True, "force": [0, 5.0, 0], "duration": 0.25},
        "duration": 0.9,
    },
    "crouch": {
        "label": "Çömel",
        "type": "animation",
        "targets": ["spine_01", "upper_leg_l", "lower_leg_l", "upper_leg_r", "lower_leg_r"],
        "pose_delta": POSE_PRESETS["crouch"],
        "duration": 0.5,
    },
    "turn_left": {
        "label": "Sola dön",
        "type": "animation+physics",
        "targets": ["root", "pelvis"],
        "pose_delta": {},
        "transform_delta": {"rotation": [0, 0.5, 0]},
        "physics_impulse": {"enabled": True, "torque": [0, 0.8, 0], "duration": 0.3},
        "duration": 0.4,
    },
    "turn_right": {
        "label": "Sağa dön",
        "type": "animation+physics",
        "targets": ["root", "pelvis"],
        "pose_delta": {},
        "transform_delta": {"rotation": [0, -0.5, 0]},
        "physics_impulse": {"enabled": True, "torque": [0, -0.8, 0], "duration": 0.3},
        "duration": 0.4,
    },
}


def _deep_merge_pose(base_pose, delta_pose):
    out = copy.deepcopy(base_pose or {})
    for bone, delta in (delta_pose or {}).items():
        cur = copy.deepcopy(out.get(bone) or {})
        if isinstance(delta, dict):
            for k, v in delta.items():
                cur[k] = copy.deepcopy(v)
        out[bone] = cur
    return out


def apply_action_to_components(components, action_name, params=None):
    """Map a high-level action to animation/physics component updates (practical)."""
    params = params or {}
    name = str(action_name or "").strip().lower()
    action = ACTION_TEMPLATES.get(name)
    if not action:
        return components, None
    comps = copy.deepcopy(components or {})
    anim = normalize_animation(comps.get("animation"))
    pose = anim.get("pose") or {}
    if action.get("reset_pose"):
        pose = {}
    pose = _deep_merge_pose(pose, action.get("pose_delta") or {})
    anim["pose"] = pose
    anim["active_action"] = name
    anim["active"] = name
    if action.get("duration") is not None:
        anim["last_duration"] = float(action.get("duration") or 0.0)
    comps["animation"] = anim

    # optional transform delta (for turning/movement hints)
    tdelta = action.get("transform_delta") or {}
    tr = comps.setdefault("transform", {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]})
    if isinstance(tdelta.get("rotation"), list) and len(tdelta["rotation"]) == 3:
        try:
            cur = tr.get("rotation") or [0, 0, 0]
            tr["rotation"] = [float(cur[i]) + float(tdelta["rotation"][i]) for i in range(3)]
        except Exception:
            pass
    if isinstance(tdelta.get("position"), list) and len(tdelta["position"]) == 3:
        try:
            cur = tr.get("position") or [0, 0, 0]
            tr["position"] = [float(cur[i]) + float(tdelta["position"][i]) for i in range(3)]
        except Exception:
            pass

    phys = normalize_physics(comps.get("physics"))
    if action.get("physics_impulse"):
        phys["enabled"] = True if phys.get("enabled") is False else phys.get("enabled", True)
        phys["last_impulse"] = {**copy.deepcopy(action.get("physics_impulse") or {}), **(params or {})}
        # apply a simple immediate impulse to velocity for practical simulation
        impulse = phys["last_impulse"] or {}
        force = impulse.get("force") or [0, 0, 0]
        if isinstance(force, list) and len(force) == 3:
            mass = max(0.001, float(phys.get("mass") or 1.0))
            dt = float(impulse.get("duration") or 0.16)
            try:
                v = phys.get("velocity") or [0, 0, 0]
                phys["velocity"] = [float(v[i]) + (float(force[i]) / mass) * dt for i in range(3)]
            except Exception:
                pass
    comps["physics"] = phys
    return comps, action


def default_timeline(fps=30, duration=10.0):
    try:
        fps = max(1, int(fps))
    except Exception:
        fps = 30
    try:
        duration = float(duration)
    except Exception:
        duration = 10.0
    if duration <= 0:
        duration = 10.0
    return {
        "fps": fps,
        "duration": duration,
        "tracks": [
            {"id": "track_transform", "name": "Transform", "type": "transform"},
            {"id": "track_pose", "name": "Pose", "type": "pose"},
            {"id": "track_physics", "name": "Physics", "type": "physics"},
        ],
        "keyframes": [],
    }


def normalize_timeline(tl=None):
    base = default_timeline()
    if isinstance(tl, dict):
        base.update({k: v for k, v in tl.items() if k in ("fps", "duration", "tracks", "keyframes") or k not in base})
        if "frames" in tl and "keyframes" not in tl:
            base["keyframes"] = tl.get("frames") or []
    try:
        base["fps"] = max(1, int(base.get("fps") or 30))
    except Exception:
        base["fps"] = 30
    try:
        base["duration"] = max(0.1, float(base.get("duration") or 10.0))
    except Exception:
        base["duration"] = 10.0
    tracks = base.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        tracks = default_timeline(base["fps"], base["duration"])["tracks"]
    norm_tracks = []
    for i, t in enumerate(tracks):
        if not isinstance(t, dict):
            continue
        norm_tracks.append({
            "id": str(t.get("id") or f"track_{i}"),
            "name": str(t.get("name") or t.get("id") or f"Track {i+1}"),
            "type": str(t.get("type") or "generic"),
            "target": t.get("target"),
        })
    base["tracks"] = norm_tracks or default_timeline(base["fps"], base["duration"])["tracks"]
    kfs = base.get("keyframes")
    if not isinstance(kfs, list):
        kfs = []
    norm_kfs = []
    for k in kfs:
        if not isinstance(k, dict):
            continue
        try:
            tval = float(k.get("time", k.get("t", 0.0)) or 0.0)
        except Exception:
            tval = 0.0
        item = copy.deepcopy(k)
        item["time"] = max(0.0, min(base["duration"], tval))
        norm_kfs.append(item)
    norm_kfs.sort(key=lambda x: x.get("time", 0.0))
    base["keyframes"] = norm_kfs
    return base


def step_physics(transform=None, physics=None, dt=1.0 / 30.0):
    """Practical deterministic physics step (no external engine). Returns (transform, physics)."""
    tr = copy.deepcopy(transform) if isinstance(transform, dict) else {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}
    ph = normalize_physics(physics)
    try:
        dt = max(0.0, min(1.0, float(dt)))
    except Exception:
        dt = 1.0 / 30.0
    pos = tr.get("position") or [0, 0, 0]
    rot = tr.get("rotation") or [0, 0, 0]
    vel = ph.get("velocity") or [0, 0, 0]
    avel = ph.get("angular_velocity") or [0, 0, 0]
    grav = ph.get("gravity") or [0, -9.81, 0]
    damping = float(ph.get("damping", 0.1) or 0.1)
    if ph.get("enabled") and str(ph.get("body") or "").lower() != "static":
        for i in range(3):
            vel[i] = float(vel[i]) + float(grav[i]) * dt
            vel[i] = float(vel[i]) * max(0.0, 1.0 - damping * dt)
            pos[i] = float(pos[i]) + float(vel[i]) * dt
            rot[i] = float(rot[i]) + float(avel[i]) * dt
        # simple ground collision
        if pos[1] < 0.0:
            pos[1] = 0.0
            if vel[1] < 0:
                vel[1] = 0.0
    tr["position"] = [float(pos[0]), float(pos[1]), float(pos[2])]
    tr["rotation"] = [float(rot[0]), float(rot[1]), float(rot[2])]
    ph["velocity"] = [float(vel[0]), float(vel[1]), float(vel[2])]
    ph["angular_velocity"] = [float(avel[0]), float(avel[1]), float(avel[2])]
    return tr, ph


def timeline_state_at(timeline, t=0.0):
    """Compute preview state at time t from timeline keyframes (practical)."""
    tl = normalize_timeline(timeline)
    try:
        t = float(t)
    except Exception:
        t = 0.0
    t = max(0.0, min(tl["duration"], t))
    kfs = [k for k in tl.get("keyframes", []) if isinstance(k, dict)]
    state = {"time": t, "nodes": {}}

    # group by node_id (global keyframes may use node_id None)
    by_node = {}
    for k in kfs:
        nid = k.get("node_id") or k.get("target_node_id") or "__global__"
        by_node.setdefault(str(nid), []).append(k)

    for nid, items in by_node.items():
        items = sorted(items, key=lambda x: float(x.get("time", 0.0) or 0.0))
        prev = None
        nxt = None
        for k in items:
            kt = float(k.get("time", 0.0) or 0.0)
            if kt <= t:
                prev = k
            if kt >= t and nxt is None:
                nxt = k
        base_k = prev or nxt or {}
        node_state = {
            "transform": copy.deepcopy(base_k.get("transform")) if isinstance(base_k.get("transform"), dict) else None,
            "pose": copy.deepcopy(base_k.get("pose")) if isinstance(base_k.get("pose"), dict) else None,
            "physics": copy.deepcopy(base_k.get("physics")) if isinstance(base_k.get("physics"), dict) else None,
            "action": base_k.get("action"),
            "keyframe_time": base_k.get("time"),
        }
        # simple linear interpolation for transform.position/rotation if both sides exist
        if prev and nxt and prev is not nxt:
            t0 = float(prev.get("time", 0.0) or 0.0)
            t1 = float(nxt.get("time", 0.0) or 0.0)
            if t1 > t0:
                a = (t - t0) / (t1 - t0)
                p_tr = prev.get("transform") if isinstance(prev.get("transform"), dict) else {}
                n_tr = nxt.get("transform") if isinstance(nxt.get("transform"), dict) else {}
                interp_tr = copy.deepcopy(node_state.get("transform") or p_tr or n_tr or {})
                for key in ("position", "rotation", "scale"):
                    pv = p_tr.get(key); nv = n_tr.get(key)
                    if isinstance(pv, list) and isinstance(nv, list) and len(pv) == 3 and len(nv) == 3:
                        try:
                            interp_tr[key] = [float(pv[i]) + (float(nv[i]) - float(pv[i])) * a for i in range(3)]
                        except Exception:
                            pass
                if interp_tr:
                    node_state["transform"] = interp_tr
        state["nodes"][nid] = node_state
    return state


def apply_timeline_state(components, node_state=None, preview=False):
    comps = copy.deepcopy(components or {})
    st = node_state or {}
    if isinstance(st.get("transform"), dict):
        tr = comps.setdefault("transform", {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]})
        for k, v in st["transform"].items():
            if k in ("position", "rotation", "scale") and isinstance(v, list) and len(v) == 3:
                tr[k] = [float(x) for x in v]
    if isinstance(st.get("pose"), dict):
        anim = normalize_animation(comps.get("animation"))
        anim["pose"] = copy.deepcopy(st["pose"])
        comps["animation"] = anim
    if isinstance(st.get("physics"), dict):
        comps["physics"] = normalize_physics({**normalize_physics(comps.get("physics")), **st["physics"]})
    if st.get("action"):
        anim = normalize_animation(comps.get("animation"))
        anim["active_action"] = st.get("action")
        anim["active"] = st.get("action")
        comps["animation"] = anim
    if preview:
        comps.setdefault("meta", {})["timeline_preview"] = True
    return comps
