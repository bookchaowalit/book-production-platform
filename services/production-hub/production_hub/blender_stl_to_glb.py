"""Blender script: convert a CAD STL (millimetres) into a self-contained GLB (metres).

Run by the Hub, never imported by it::

    blender --background --factory-startup --python blender_stl_to_glb.py -- in.stl out.glb [#RRGGBB]

The mesh is scaled from millimetres to metres, recentred on the origin with
its base at z=0, given one plain metallic material, and exported as binary
glTF without extensions so the Scene Composer GLB preflight accepts it.
"""

import sys

import bpy  # type: ignore[import-not-found]


def _hex_to_rgba(value: str) -> tuple[float, float, float, float]:
    value = value.lstrip("#")
    return (int(value[0:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255, 1.0)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    source, target = argv[0], argv[1]
    color = _hex_to_rgba(argv[2] if len(argv) > 2 else "#B8BEC6")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.stl_import(filepath=source)
    objects = [item for item in bpy.context.scene.objects if item.type == "MESH"]
    if len(objects) != 1:
        raise SystemExit(f"expected one mesh in the STL, found {len(objects)}")
    part = objects[0]
    part.name = "cad_part"
    part.scale = (0.001, 0.001, 0.001)
    bpy.context.view_layer.objects.active = part
    part.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    part.location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    lowest = min((part.matrix_world @ vertex.co).z for vertex in part.data.vertices)
    part.location.z -= lowest

    material = bpy.data.materials.new("cad_metal")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = color
    shader.inputs["Metallic"].default_value = 0.6
    shader.inputs["Roughness"].default_value = 0.35
    part.data.materials.clear()
    part.data.materials.append(material)

    bpy.ops.export_scene.gltf(
        filepath=target,
        export_format="GLB",
        export_apply=True,
        export_extras=False,
        export_cameras=False,
        export_lights=False,
        export_animations=False,
    )


main()
