# SPDX-License-Identifier: GPL-2.0-or-later
#
# corona_dump.py
# Diagnostik: skriver ut ALLA parametrar och texturplatser for Corona-material
# i en .max-fil, sa att vi kan se vilket index som ar vilken parameter.
# Maste ligga i samma mapp som corona_export.py.
#
# Anvandning (--slots = bara texturplatserna, kortare utskrift):
#   python corona_dump.py "scen.max" --slots > dump.txt
# Anvandning (spara utskriften i en fil):
#   python corona_dump.py "scen.max" > dump.txt

import sys
import struct
import corona_export as c

CORONA_IDS = {
    c.CORO_MTL: "CoronaMtl",
    c.COPHY_MTL: "CoronaPhysicalMtl",
    c.SKIN_MTL: "CoronaSkinMtl",
    c.HAIR_MTL: "CoronaHairMtl",
}


def describe(ch):
    if isinstance(ch, c.ContainerChunk):
        return "CONTAINER med %d barn" % len(getattr(ch, "children", []))
    d = ch.data
    if isinstance(d, (bytes, bytearray)):
        s = "bytes[%d]" % len(d)
        if len(d) >= 12:
            s += "  sista12=%s" % (tuple(round(x, 4) for x in struct.unpack('<3f', d[-12:])),)
        if len(d) >= 4:
            s += "  sista4=%s" % round(struct.unpack('<f', d[-4:])[0], 4)
        return s
    return "data=%r" % (d,)


def dump_children(chunks, indent, depth):
    for idx, ch in enumerate(chunks):
        print("%s[0x%02X] typ=0x%04X  %s" % (indent, idx, ch.types, describe(ch)))
        if isinstance(ch, c.ContainerChunk) and depth > 0:
            dump_children(ch.children, indent + "      ", depth - 1)


def print_tree(node, indent, depth, visited=None):
    """Skriver ut klassnamnen pa noderna under en textur-slot."""
    if visited is None:
        visited = set()
    if node is None or depth < 0 or id(node) in visited:
        return
    visited.add(id(node))
    kids = []
    try:
        kids += [r for r in c.get_references(node) if r is not None]
    except Exception:
        pass
    try:
        kids += [r for r in c.get_reference(node).values() if r is not None]
    except Exception:
        pass
    for kid in kids:
        print("%s%s" % (indent, c.get_cls_name(kid)))
        print_tree(kid, indent + "  ", depth - 1, visited)


def dump_material(node, mat, where, show_params):
    kind = CORONA_IDS.get(c.get_guid(mat), "CoronaLayeredMtl")
    mtl_id = mat.get_first(0x0FA0)
    mname = mtl_id.children[0].data if (mtl_id and mtl_id.children) else c.get_cls_name(mat)
    print("=" * 70)
    print("OBJEKT: %r   PLATS: %s   MATERIAL: %r   TYP: %s" % (c.get_node_name(node), where, mname, kind))
    refs = c.get_references(mat)
    if not refs or refs[0] is None:
        print("  (inga referenser)")
        return
    block = refs[0]
    if show_params:
        print("--- PARAMETRAR (index i mtl[0].children) ---")
        dump_children(block.children, "  ", 1)
    print("--- TEXTURPLATSER (slot -> fil, via vilka noder) ---")
    for key, ref in sorted(c.get_reference(block).items()):
        if ref is None:
            print("  slot %s -> (tom)" % key)
            continue
        try:
            found = c.find_bitmaps_deep(ref)
            if not found:
                print("  slot %s -> (ingen bildfil hittad)" % key)
            for path, chain in found:
                print("  slot %s -> %s   via %s" % (key, c._fname(path), " > ".join(chain)))
            print("      nodtrad:", c.get_cls_name(ref))
            print_tree(ref, "        ", 4)
        except Exception as exc:
            print("  slot %s -> (fel: %s)" % (key, exc))


def corona_mats(mat, path="root", depth=0):
    """Alla Corona-material i mat, med samma sokvagar som corona_export
    ('root', 'root/2' = plats 2 i Multi/Sub osv)."""
    if mat is None or depth > 6:
        return
    uid = c.get_guid(mat)
    if uid in CORONA_IDS or uid == c.LAYER_MTL:
        yield mat, path
    elif uid == c.MULTI_MTL:
        for idx, sub in c.sub_slots(mat):
            for item in corona_mats(sub, "%s/%d" % (path, idx), depth + 1):
                yield item


def main(filepath, show_params=True):
    maxfile = c.ImportMaxFile(filepath)
    c.read_class_data(maxfile, filepath)
    c.read_directory(maxfile, filepath)
    c.read_class_directory(maxfile, filepath)
    metasid = min(max(e.sid for e in maxfile.direntries if e is not None), 11)
    c.SCENE_LIST = c.read_chunks(maxfile, 'Scene', c.SceneChunk)
    c.META_DATA = c.read_chunks(maxfile, maxfile.direntries[metasid].name, superId=metasid) if metasid >= 0xA else []

    for node in c.SCENE_LIST[0].children:
        try:
            is_node = (isinstance(node, c.SceneChunk) and c.get_guid(node) == 0x1 and c.get_super_id(node) == 0x1) \
                or c.get_node_parent(node)
            if not is_node:
                continue
            prs, msh, mat, lyr = c.get_matrix_mesh_material(node)
            for cmat, where in corona_mats(mat):
                dump_material(node, cmat, where, show_params)
        except Exception as exc:
            print("Fel pa nod:", exc)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Anvandning: python corona_dump.py "scen.max" > dump.txt')
        sys.exit(1)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], show_params=("--slots" not in sys.argv))
