"""Build a fixed, editable product studio scene inside Blender."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

RENDER_PROFILES = {
    "square-1024.v1": (1024, 1024),
    "landscape-1920x1080.v1": (1920, 1080),
}
RECIPE_ID = "product-hero.studio.v1"
COMPOSITION_ID = "three-quarter.v1"
FIT_COMPOSITION_ID = "three-quarter-fit.v1"
# View directions (camera minus target) for the product-fitted compositions.
FIT_DIRECTIONS = {
    FIT_COMPOSITION_ID: (3.7, -6.0, 2.3),
    "front-fit.v1": (0.0, -1.0, 0.18),
    "side-fit.v1": (1.0, 0.0, 0.18),
    "top-fit.v1": (0.0, -0.3, 1.0),
}
# Turntable stills: product-fitted orbit at the three-quarter-fit elevation,
# every 45 degrees starting from the front (0 = camera on -Y, 90 = camera on +X).
ORBIT_STEP_DEGREES = 45
ORBIT_ELEVATION = 0.33
ORBIT_COMPOSITIONS = {
    f"orbit-fit-{angle:03d}.v1": (
        round(math.sin(math.radians(angle)), 6) + 0.0,  # + 0.0 turns -0.0 into 0.0
        round(-math.cos(math.radians(angle)), 6) + 0.0,
        ORBIT_ELEVATION,
    )
    for angle in range(0, 360, ORBIT_STEP_DEGREES)
}
FIT_DIRECTIONS.update(ORBIT_COMPOSITIONS)
COMPOSITION_IDS = {COMPOSITION_ID, *FIT_DIRECTIONS}
_CAMERA_OFFSET = (3.7, -6.0, 3.1)
_LEGACY_TARGET = (0.0, 0.0, 0.8)
_FIT_MARGIN = 1.15
CAMERA_LENS_MM = 55.0
# Blender's default camera sensor (Sensor Fit = Auto); _fit_camera reads it.
CAMERA_SENSOR_WIDTH_MM = 36.0
LIGHT_TARGET = (0.0, 0.0, 0.8)
LIGHTING_ID = "softbox-three-point.v1"
# Every light is an AREA light with a DISK shape aimed at LIGHT_TARGET:
# (name, location, energy W, size m, color RGB).
LightSpec = tuple[str, tuple[float, float, float], float, float, tuple[float, float, float]]
LIGHTING_PRESETS: dict[str, tuple[LightSpec, ...]] = {
    LIGHTING_ID: (
        ("Key Softbox", (-3.5, -4.0, 5.0), 720.0, 4.0, (1.0, 0.86, 0.72)),
        ("Fill Softbox", (4.0, -2.0, 3.2), 430.0, 4.5, (0.72, 0.84, 1.0)),
        ("Rim Softbox", (1.0, 3.6, 4.4), 850.0, 3.0, (1.0, 0.94, 0.82)),
    ),
    # Bright, low-contrast catalogue light: large neutral key and fill.
    "high-key-white.v1": (
        ("Key Softbox", (-3.0, -4.5, 5.0), 900.0, 6.0, (1.0, 1.0, 1.0)),
        ("Fill Softbox", (4.0, -3.0, 3.5), 700.0, 6.0, (1.0, 1.0, 1.0)),
        ("Top Softbox", (0.0, 0.0, 6.0), 500.0, 5.0, (1.0, 1.0, 1.0)),
        ("Rim Softbox", (0.5, 4.0, 4.0), 400.0, 3.0, (1.0, 1.0, 1.0)),
    ),
    # Low-key hero light: small warm key, strong cool rim, almost no fill.
    "dramatic-rim.v1": (
        ("Key Softbox", (-4.0, -2.5, 3.5), 500.0, 1.5, (1.0, 0.82, 0.64)),
        ("Rim Left", (-2.5, 3.5, 3.0), 1400.0, 1.0, (0.70, 0.82, 1.0)),
        ("Rim Right", (2.5, 3.5, 3.0), 1400.0, 1.0, (0.70, 0.82, 1.0)),
    ),
}
BACKGROUND_ID = "studio-neutral.v1"
# World colour/strength, ground plane (or None), and film transparency.
BACKGROUND_PRESETS: dict[str, dict[str, Any]] = {
    BACKGROUND_ID: {
        "world_color": (0.52, 0.56, 0.62, 1.0),
        "world_strength": 0.45,
        "ground": {"size": 200.0, "location": (0.0, 0.0, -0.04), "base_color": (0.68, 0.71, 0.76, 1.0), "roughness": 0.72},
        "film_transparent": False,
    },
    "studio-white.v1": {
        "world_color": (0.95, 0.95, 0.95, 1.0),
        "world_strength": 0.6,
        "ground": {"size": 200.0, "location": (0.0, 0.0, -0.04), "base_color": (0.92, 0.92, 0.92, 1.0), "roughness": 0.6},
        "film_transparent": False,
    },
    # Cut-out PNG (RGBA): no ground plane, world still lights the product.
    "transparent.v1": {
        "world_color": (0.52, 0.56, 0.62, 1.0),
        "world_strength": 0.45,
        "ground": None,
        "film_transparent": True,
    },
}
# Presets the deterministic engine accepts but the V1B brief adapter may not
# propose yet (its eval set does not cover them).
ENGINE_ONLY_PRESETS: dict[str, frozenset[str]] = {
    "composition": frozenset(ORBIT_COMPOSITIONS),
    "lighting": frozenset(set(LIGHTING_PRESETS) - {LIGHTING_ID}),
    "background": frozenset(set(BACKGROUND_PRESETS) - {BACKGROUND_ID}),
}
MATERIAL_ID = "preserve-source.v1"
RENDER_ENGINE = "BLENDER_EEVEE"
EEVEE_SAMPLES = 64
PNG_COMPRESSION = 15
VIEW_TRANSFORM = "AgX"
MAX_IMPORTED_OBJECTS = 100
MAX_TRIANGLES = 1_000_000
MAX_TEXTURE_DIMENSION = 4096


def _load_validated_scene_ir(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as scene_file:
        value = json.load(scene_file)
    expected_fields = {
        "schema_version",
        "asset",
        "recipe",
        "composition",
        "lighting",
        "background",
        "material_preset",
        "render_profile",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ValueError("invalid_scene_ir")
    if value["schema_version"] != "scene-ir.v1":
        raise ValueError("invalid_scene_ir")
    asset = value["asset"]
    if not isinstance(asset, dict) or set(asset) != {"path"} or not isinstance(asset["path"], str):
        raise ValueError("invalid_scene_ir")
    allowlists = {
        "recipe": {RECIPE_ID},
        "composition": COMPOSITION_IDS,
        "lighting": set(LIGHTING_PRESETS),
        "background": set(BACKGROUND_PRESETS),
        "material_preset": {MATERIAL_ID},
        "render_profile": set(RENDER_PROFILES),
    }
    if any(value.get(key) not in allowed for key, allowed in allowlists.items()):
        raise ValueError("invalid_scene_ir")
    return value


def _point_at(obj: Any, target: Any) -> None:
    from mathutils import Vector

    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _make_world(bpy: Any, background: str = BACKGROUND_ID) -> None:
    preset = BACKGROUND_PRESETS[background]
    world = bpy.data.worlds.new("Studio Neutral World")
    world.use_nodes = True
    node = world.node_tree.nodes.get("Background")
    if node is not None:
        node.inputs["Color"].default_value = preset["world_color"]
        node.inputs["Strength"].default_value = preset["world_strength"]
    bpy.context.scene.world = world


def _make_ground(bpy: Any, background: str = BACKGROUND_ID) -> None:
    spec = BACKGROUND_PRESETS[background]["ground"]
    if spec is None:
        return
    bpy.ops.mesh.primitive_plane_add(size=spec["size"], location=spec["location"])
    ground = bpy.context.object
    ground.name = "Studio Ground"
    material = bpy.data.materials.new("Studio Neutral Ground")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    if shader is not None:
        shader.inputs["Base Color"].default_value = spec["base_color"]
        shader.inputs["Roughness"].default_value = spec["roughness"]
    ground.data.materials.append(material)


def _make_light(bpy: Any, name: str, location: tuple[float, float, float], energy: float, size: float, color: tuple[float, float, float]) -> None:
    light_data = bpy.data.lights.new(name=name, type="AREA")
    light_data.energy = energy
    light_data.shape = "DISK"
    light_data.size = size
    light_data.color = color
    light_object = bpy.data.objects.new(name, light_data)
    bpy.context.scene.collection.objects.link(light_object)
    light_object.location = location
    _point_at(light_object, LIGHT_TARGET)


def _fit_camera(
    camera: Any,
    size: tuple[float, float, float],
    resolution: tuple[int, int],
    view: tuple[float, float, float] = FIT_DIRECTIONS[FIT_COMPOSITION_ID],
) -> tuple[float, float, float]:
    """Place the camera along ``view`` so the product's bounding sphere fills the frame.

    ``size`` is the product's fitted bounding box (x, y, z) with its base on the
    ground. The camera looks along the composition's view direction (the
    three-quarter one matches the legacy camera), aims at the product centre and
    backs off just far enough for the sphere to fit the narrower field of view,
    so flat or small parts are no longer left low and tiny in the frame.
    """
    from mathutils import Vector

    target = Vector((0.0, 0.0, size[2] * 0.5))
    radius = Vector(size).length * 0.5
    sensor = camera.data.sensor_width
    horizontal = 2.0 * math.atan(sensor * 0.5 / camera.data.lens)
    width, height = resolution
    vertical = 2.0 * math.atan(math.tan(horizontal * 0.5) * height / width) if width >= height else horizontal
    half = min(horizontal, vertical) * 0.5
    distance = radius * _FIT_MARGIN / math.sin(half)
    direction = Vector(view).normalized()
    camera.location = target + direction * distance
    return (target.x, target.y, target.z)


def _configure_studio(
    bpy: Any,
    render_profile: str,
    composition: str = COMPOSITION_ID,
    product_size: tuple[float, float, float] = (2.0, 2.0, 2.0),
    lighting: str = LIGHTING_ID,
    background: str = BACKGROUND_ID,
) -> None:
    scene = bpy.context.scene
    width, height = RENDER_PROFILES[render_profile]
    transparent = bool(BACKGROUND_PRESETS[background]["film_transparent"])
    scene.render.engine = RENDER_ENGINE
    scene.eevee.taa_render_samples = EEVEE_SAMPLES
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = PNG_COMPRESSION
    scene.render.film_transparent = transparent
    scene.view_settings.view_transform = VIEW_TRANSFORM
    scene.camera = None

    _make_world(bpy, background)
    _make_ground(bpy, background)

    camera_data = bpy.data.cameras.new("Product Hero Camera")
    camera = bpy.data.objects.new("Product Hero Camera", camera_data)
    scene.collection.objects.link(camera)
    camera_data.lens = CAMERA_LENS_MM
    camera_data.dof.use_dof = False
    if composition in FIT_DIRECTIONS:
        target = _fit_camera(camera, product_size, (width, height), FIT_DIRECTIONS[composition])
    else:
        camera.location = _CAMERA_OFFSET
        target = _LEGACY_TARGET
    _point_at(camera, target)
    scene.camera = camera

    for name, location, energy, size, color in LIGHTING_PRESETS[lighting]:
        _make_light(bpy, name, location, energy, size, color)

    scene["book_production_scene_ir_version"] = "scene-ir.v1"
    scene["book_production_recipe"] = RECIPE_ID
    scene["book_production_composition"] = composition
    scene["book_production_lighting"] = lighting
    scene["book_production_background"] = background
    scene["book_production_material_preset"] = MATERIAL_ID
    scene["book_production_render_profile"] = render_profile
    scene["book_production_render_engine"] = RENDER_ENGINE
    scene["book_production_eevee_samples"] = EEVEE_SAMPLES
    scene.unit_settings.system = "NONE"


def build_scene(scene_ir_path: Path, asset_path: Path, blend_path: Path) -> None:
    """Import one preflighted GLB and save a deterministic editable studio scene."""
    import bpy
    from mathutils import Matrix, Vector

    scene_ir = _load_validated_scene_ir(scene_ir_path)
    if asset_path.is_symlink() or not asset_path.is_file():
        raise ValueError("unsafe_glb_resource")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(asset_path))

    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not mesh_objects or len(mesh_objects) > MAX_IMPORTED_OBJECTS:
        raise ValueError("resource_cap_exceeded")
    triangle_count = sum(len(obj.data.polygons) for obj in mesh_objects)
    if triangle_count == 0 or triangle_count > MAX_TRIANGLES:
        raise ValueError("resource_cap_exceeded")
    for image in bpy.data.images:
        if image.source != "FILE":
            continue
        width, height = image.size[:]
        if width <= 0 or height <= 0 or width > MAX_TEXTURE_DIMENSION or height > MAX_TEXTURE_DIMENSION:
            raise ValueError("resource_cap_exceeded")

    world_points = [obj.matrix_world @ Vector(corner) for obj in mesh_objects for corner in obj.bound_box]
    minimum = Vector(tuple(min(point[axis] for point in world_points) for axis in range(3)))
    maximum = Vector(tuple(max(point[axis] for point in world_points) for axis in range(3)))
    dimensions = maximum - minimum
    largest_dimension = max(dimensions)
    if not math.isfinite(largest_dimension) or largest_dimension <= 0.0:
        raise ValueError("invalid_glb")
    fit_scale = 2.0 / largest_dimension
    center_x = (minimum.x + maximum.x) * 0.5
    center_y = (minimum.y + maximum.y) * 0.5

    product_root = bpy.data.objects.new("Product Root", None)
    bpy.context.scene.collection.objects.link(product_root)
    product_root.location = (-center_x * fit_scale, -center_y * fit_scale, -minimum.z * fit_scale)
    product_root.scale = (fit_scale, fit_scale, fit_scale)
    for obj in mesh_objects:
        original_world = obj.matrix_world.copy()
        obj.parent = product_root
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = original_world
        obj["book_production_imported_asset"] = True

    fitted = dimensions * fit_scale
    _configure_studio(
        bpy,
        scene_ir["render_profile"],
        scene_ir["composition"],
        (fitted.x, fitted.y, fitted.z),
        scene_ir["lighting"],
        scene_ir["background"],
    )
    bpy.ops.file.pack_all()
    for image in bpy.data.images:
        if image.source == "FILE" and image.packed_file is None:
            raise ValueError("unsafe_glb_resource")

    blend_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path.resolve()))


def render_saved_scene(blend_path: Path, png_path: Path) -> None:
    """Reopen the saved scene in this fresh Blender process and render its PNG."""
    import bpy

    if not blend_path.is_file():
        raise ValueError("invalid_saved_scene")
    bpy.ops.wm.open_mainfile(filepath=str(blend_path.resolve()))
    scene = bpy.context.scene
    profile = scene.get("book_production_render_profile")
    if profile not in RENDER_PROFILES or scene.camera is None:
        raise ValueError("invalid_saved_scene")
    width, height = RENDER_PROFILES[profile]
    if (scene.render.resolution_x, scene.render.resolution_y) != (width, height):
        raise ValueError("invalid_saved_scene")
    if scene.render.engine != RENDER_ENGINE:
        raise ValueError("invalid_saved_scene")
    imported_meshes = [
        obj for obj in scene.objects if obj.type == "MESH" and obj.get("book_production_imported_asset")
    ]
    if not imported_meshes:
        raise ValueError("invalid_saved_scene")
    for image in bpy.data.images:
        if image.source == "FILE" and image.packed_file is None:
            raise ValueError("invalid_saved_scene")

    png_path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(png_path.resolve())
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if scene.render.film_transparent else "RGB"
    bpy.ops.render.render(write_still=True)
