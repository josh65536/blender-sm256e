import bpy
import math
from pathlib import PurePath
from mathutils import Euler, Matrix
from .util import *

def import_bone(bytestr, bone_bytes):
    """ Returns (bone, displist_material_map) """
    # Main info
    bone_id = to_uint(bone_bytes, 0, 4)
    name = cstr_to_str(bytestr, to_uint(bone_bytes, 4, 4))
    parent_id = to_int(bone_bytes, 8, 2)
    parent_id = -1 if parent_id == 0 else bone_id + parent_id
    sibling_id = to_int(bone_bytes, 0xc, 2)
    sibling_id = -1 if sibling_id == 0 else bone_id + sibling_id

    # Transform
    scale = to_vec(bone_bytes, 0x10, 4, 3, 12)
    rotation = Euler([math.radians(to_deg(bone_bytes, 0x1c + i * 2) * 16)
            for i in range(3)], "XYZ")
    translation = to_vec(bone_bytes, 0x24, 4, 3, 12)
    transform = Matrix.Translation(translation) * \
            rotation.to_matrix().to_4x4() * \
            Matrix.Scale(scale[0], 4, Vector((1, 0, 0))) * \
            Matrix.Scale(scale[1], 4, Vector((0, 1, 0))) * \
            Matrix.Scale(scale[2], 4, Vector((0, 0, 1)))

    # Material display-list pairs
    num_pairs = to_uint(bone_bytes, 0x30, 4)
    mat_ids = to_uint_list(bytestr, to_uint(bone_bytes, 0x34, 4), 1, num_pairs)
    displist_ids = to_uint_list(bytestr, to_uint(bone_bytes, 0x38, 4), 1, num_pairs)

    bone = Bone(name, parent_id, sibling_id, transform, set(mat_ids), set(displist_ids))
    return bone, {d: m for m, d in zip(mat_ids, displist_ids)}
    

def import_bones(bytestr):
    """ Returns (skeleton, displist_material_map) """
    displist_material_map = {}
    bones = []
    bone_offset = to_uint(bytestr, 8, 4)

    for i in range(to_uint(bytestr, 4, 4)):
        bone, dmm = import_bone(bytestr, get_n_bytes(bytestr, bone_offset + i * 0x40, 0x40))
        bones.append(bone)
        displist_material_map.update(dmm)

    return Skeleton(bones), displist_material_map


def read_vertex(cmd, data_bytes, offset, p_type, group_id, tex_coords, normal, color, position,
        material_id, ref_vertices, ref_faces):
    """ Returns (offset, position), where:
    offset: int is the new offset
    position: Vector is the new previous position
    """
    new_position = None

    if cmd == 0x23:
        new_position = Vector(to_vec(data_bytes, offset, 2, 3, 12))
    
    elif cmd == 0x24:
        new_position = Vector(to_vecb(data_bytes, offset, 10, 3, 6))

    else:
        new_position = position.copy()

        if cmd == 0x25:
            new_position.x = to_fix(data_bytes, offset + 0, 2, 12)
            new_position.y = to_fix(data_bytes, offset + 2, 2, 12)
        elif cmd == 0x26:
            new_position.x = to_fix(data_bytes, offset + 0, 2, 12)
            new_position.z = to_fix(data_bytes, offset + 2, 2, 12)
        elif cmd == 0x27:
            new_position.y = to_fix(data_bytes, offset + 0, 2, 12)
            new_position.z = to_fix(data_bytes, offset + 2, 2, 12)

        elif cmd == 0x28:
            new_position += Vector(to_vecb(data_bytes, offset, 10, 3, 12))
    
    ref_vertices.append(Vertex(new_position, 
        normal.copy() if normal else None,
        tex_coords.copy() if tex_coords else None,
        color.copy() if color else None,
        group_id))

    # Face addition
    if p_type in (0, 1) and len(ref_vertices) % (p_type + 3) == 0:
        ref_faces.append(Face(ref_vertices[-(p_type + 3):], material_id))

    elif p_type == 2 and len(ref_vertices) >= 3:
        ref_faces.append(Face([ref_vertices[i] for i in
            ((-2, -3, -1) if len(ref_vertices) % 2 == 0 else (-3, -2, -1))], material_id))

    elif p_type == 3 and len(ref_vertices) % 2 == 0 and len(ref_vertices) >= 4:
        ref_faces.append(Face([ref_vertices[i] for i in (-4, -3, -1, -2)], material_id))

    offset += 8 if cmd == 0x23 else 4
    return offset, new_position


def import_display_list(bytestr, displist_bytes, skeleton, material_id):
    """ Returns (vertices, faces) of the whole display list. """

    header_bytes = get_n_bytes(bytestr, to_uint(displist_bytes, 4, 4), 0x10)
    transform_ids = to_uint_list(bytestr, to_uint(header_bytes, 4, 4), 1,
            to_uint(header_bytes, 0, 4))
    data_bytes = get_n_bytes(bytestr, to_uint(header_bytes, 0xc, 4),
            to_uint(header_bytes, 8, 4))

    p_type = None
    vertices = None
    faces = None
    offset = 0
    tex_coords = None
    normal = None
    color = None
    position = None
    group_id = None
    accum_vertices = []
    accum_faces = []

    # for i in range(0, 0x100, 16):
    #     print(["{:2x}".format(byte) for byte in get_n_bytes(data_bytes, i, 16)])
    
    while offset < len(data_bytes):
        cmds = to_uint_list(data_bytes, offset, 1, 4)
        offset += 4

        for cmd in cmds:
            if cmd == 0x40: # Begin vertex list
                p_type = to_uint(data_bytes, offset, 1) % 4
                if vertices is not None:
                    accum_vertices += vertices
                    accum_faces += faces
                vertices = []
                faces = []
                offset += 4

            elif cmd == 0x14: # Matrix restore
                transform_id = to_uint(data_bytes, offset, 4) % 32
                group_id = to_uint(bytestr, 
                        to_uint(bytestr, 0x2c, 4) + transform_ids[transform_id] * 2, 2)
                offset += 4

            elif cmd == 0x20: # Color
                color = uint16_to_color(to_uint(data_bytes, offset, 2), 1)
                offset += 4

            elif cmd == 0x21: # Normal
                normal = Vector(to_vecb(data_bytes, offset, 10, 3, 9))
                offset += 4

            elif cmd == 0x22: # Texture coordinates
                tex_coords = Vector(to_vec(data_bytes, offset, 2, 2, 4))
                offset += 4

            elif cmd in (0x23, 0x24, 0x25, 0x26, 0x27, 0x28):
                offset, position = read_vertex(cmd, data_bytes, offset, p_type, group_id,
                        tex_coords, normal, color, position, material_id, vertices, faces)
                
            elif cmd in (0x34,):
                offset += 128

            elif cmd in (0x16, 0x18):
                offset += 64

            elif cmd in (0x17, 0x19):
                offset += 48

            elif cmd in (0x1a,):
                offset += 36
            
            elif cmd in (0x1b, 0x1c, 0x70):
                offset += 12

            elif cmd in (0x71,):
                offset += 8

            elif cmd in (0x10, 0x12, 0x13, 0x29, 0x2a, 0x2b,
                    0x30, 0x31, 0x32, 0x33, 0x50, 0x60, 0x72):
                offset += 4

            elif cmd in (0x00, 0x11, 0x15, 0x41):
                pass

            else:
                raise Exception("Unknown GX command: " + hex(cmd) +
                        " at offset: " + hex(offset))

    if vertices is not None:
        accum_vertices += vertices
        accum_faces += faces

    return accum_vertices, accum_faces
                

def import_display_lists(bytestr, skeleton, displist_material_map):
    """ Returns the geometry. """

    displist_offset = to_uint(bytestr, 0x10, 4)
    vertices = []
    faces = []

    for i in range(to_uint(bytestr, 0xc, 4)):
        if i in displist_material_map:
            v, f = import_display_list(bytestr, 
                    get_n_bytes(bytestr, displist_offset + i * 8, 8),
                    skeleton, displist_material_map[i])
            vertices += v
            faces += f

    return Geometry(vertices, faces, False)

def import_texture(bytestr, texture_id, palette_id, material, tex_cache):
    if texture_id < 0:
        return

    tex_bytes = get_n_bytes(bytestr, to_uint(bytestr, 0x18, 4) + 0x14 * texture_id, 0x14)
    pal_bytes = get_n_bytes(bytestr, to_uint(bytestr, 0x20, 4) + 0x10 * palette_id, 0x10) \
            if palette_id >= 0 else None

    name = cstr_to_str(bytestr, to_uint(tex_bytes, 0, 4))
    pal_name = cstr_to_str(bytestr, to_uint(pal_bytes, 0, 4)) if palette_id >= 0 else None

    tex = None
    if (name, pal_name) in tex_cache:
        tex = tex_cache[(name, pal_name)]

    else:
        size = to_uint(tex_bytes, 0x8, 4)
        width = to_uint(tex_bytes, 0xc, 2)
        height = to_uint(tex_bytes, 0xe, 2)

        tex_param = to_uint(tex_bytes, 0x10, 4)
        type_ = tex_param >> 26 & 7
        transparency = tex_param >> 29 & 1
        if type_ == Texture.COMPRESSED:
            size = size * 3 // 2

        tex_data = get_n_bytes(bytestr, to_uint(tex_bytes, 4, 4), size)
        pal_data = get_n_bytes(bytestr, to_uint(pal_bytes, 4, 4), to_uint(pal_bytes, 8, 4)) \
                if palette_id >= 0 else None

        tex = Texture.from_bytestr(tex_data, pal_data, name, width, height, type_, transparency)
        tex_cache[(name, pal_name)] = tex

    slot = material.texture_slots.add()
    slot.texture = tex.texture
    return tex.texture


def import_material(bytestr, material_bytes, material_id, mesh, geo, tex_cache):
    material = bpy.data.materials.new(cstr_to_str(bytestr, to_uint(material_bytes, 0, 4)))
    mesh.materials.append(material)
    tex = import_texture(bytestr, to_int(material_bytes, 4, 4), to_int(material_bytes, 8, 4),
            material, tex_cache)

    transparency = any(i % 4 == 3 and v < 1 for i, v in enumerate(tex.image.pixels)) \
            if tex else False

    # Texture parameters
    if tex:
        tex_param = to_uint(material_bytes, 0x20, 4)
        if (tex_param >> 16 & 3) == 0:
            tex.extension = "EXTEND"
        if tex_param >> 18 & 1:
            tex.use_mirror_x = True
        if tex_param >> 19 & 1:
            tex.use_mirror_y = True

        if (tex_param >> 30 & 3) == 2:
            material["Environment Map"] = 1

    # Polygon parameters
    poly_param = to_uint(material_bytes, 0x24, 4)
    blend_type = poly_param >> 4 & 3
    if tex:
        material.texture_slots[0].blend_type = "MIX" if blend_type == 1 else "MULTIPLY"
    material.game_settings.use_backface_culling = not (poly_param >> 6 & 1)
    if poly_param >> 14 & 1:
        material["Depth Equal"] = 1

    alpha = poly_param >> 16 & 0x1f
    if (transparency and blend_type != 1) or alpha != 31:
        if tex:
            material.texture_slots[0].use_map_alpha = True
        material.use_transparency = True
        material.alpha = alpha / 31

    material["Polygon ID"] = poly_param >> 24 & 0x3f

    # Lighting colors
    diff_amb = to_uint(material_bytes, 0x28, 4)
    diffuse = uint16_to_color(diff_amb, 2.2)
    ambient = sum(uint16_to_color(diff_amb >> 16, 1)) / sum(diffuse) \
            if sum(diffuse) > 0 else 0.5
    material.diffuse_color = diffuse
    material.diffuse_intensity = 1
    material.ambient = ambient
    material.use_vertex_color_paint = all(v.normal is None
            for f in geo.faces for v in f.vertices if f.material_id == material_id)
    material.use_shadeless = material.use_vertex_color_paint
    
    spec_emit = to_uint(material_bytes, 0x2c, 4)
    specular = uint16_to_color(spec_emit, 2.2)
    emission = sum(uint16_to_color(spec_emit >> 16, 1)) / 3
    material.specular_color = specular
    material.specular_intensity = 1
    material.emit = emission


def import_materials(bytestr, mesh, geo, material_ids, tex_cache):
    material_offset = to_uint(bytestr, 0x28, 4)

    for i in range(to_uint(bytestr, 0x24, 4)):
        if i in material_ids:
            import_material(bytestr, get_n_bytes(bytestr, material_offset + i * 0x30, 0x30),
                    i, mesh, geo, tex_cache)

    counter = 0
    material_table = {m: i for i, m in enumerate(sorted(material_ids))}
    for face, poly in zip(geo.faces, mesh.polygons):
        poly.material_index = material_table[face.material_id]

        # UV downscaling
        material = mesh.materials[material_table[face.material_id]]
        if material.texture_slots[0]:
            size = material.texture_slots[0].texture.image.size
            for i in range(counter, counter + len(face.vertices)):
                for j in range(2):
                    mesh.uv_layers[0].data[i].uv[j] /= size[j]

        counter += len(face.vertices)


def load_ret(context, filepath):
    bytestr = None
    with open(filepath, "rb") as f:
        bytestr = f.read()

    scale = 2 ** to_uint(bytestr, 0, 4)
    skeleton, displist_material_map = import_bones(bytestr)

    bone = skeleton.bones[0]
    tex_cache = {} # maps (tex_name, pal_name) to texture
    objs = []
    while bone:
        geo = import_display_lists(bytestr, skeleton, 
                {d: m for d, m in displist_material_map.items() if d in bone.displist_ids})
        obj = geo.create_mesh(context, PurePath(filepath).stem, skeleton, scale)
        import_materials(bytestr, obj.data, geo, bone.material_ids, tex_cache)
        bone = bone.sibling
        objs.append(obj)

    return objs

def load(context, filepath):
    load_ret(context, filepath)
    return {"FINISHED"}
