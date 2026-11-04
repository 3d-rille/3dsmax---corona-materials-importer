# SPDX-License-Identifier: GPL-2.0-or-later
#
# corona_export.py
# Laser Corona-material ur en .max-fil (utan Corona, Max eller Blender) och
# skriver en MAXScript-fil (.ms) med materialdatan.
#
# Baserad pa Blender-importern io_scene_max:
#   Copyright 2023-2026 Sebastian Schrand
#             2017-2022 Jens M. Plonka  (ImportMAX)
#             2005-2018 Philippe Lagadec (olefile)
# Licens: GPL-2.0-or-later
#
# Anvandning:
#   python corona_export.py "C:\scener\scen.max" "C:\temp\corona_data.ms"

import io
import os
import re
import sys
import zlib
import array
import struct
from pathlib import Path

###################
# DATA STRUCTURES #
###################

MAGIC = b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1'

MIN_FILE_SIZE = 1536
UNKNOWN_SIZE = 0x7FFFFFFF
MAXFILE_SIZE = 0x7FFFFFFFFFFFFFFF
MAXREGSECT = 0xFFFFFFFA
DIFSECT = 0xFFFFFFFC
FATSECT = 0xFFFFFFFD
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
MAX_STREAM = 2
ROOT_STORE = 5

TYP_REFS = {0x9, 0x1040, 0x2034, 0x2035}
TYP_SHORT = {0x103, 0x105, 0x99C, 0x2500, 0x3005}
TYP_LINK = {0x1020, 0x1030, 0x1050, 0x1080, 0x3002, 0x4003}
TYP_NAME = {0x340, 0x456, 0x962, 0x10A0, 0x1010, 0x1230, 0x4001}
TYP_ARRAY = {0x102, 0x96A, 0x96B, 0x96C, 0x9ED, 0x2501, 0x2503, 0x2504, 0x2505, 0x2511}

LAYER_MTL = 0x8425554E65486584  # CoronaLayeredMtl
COPHY_MTL = 0x871517206912AB89  # CoronaPhysicalMtl
SKIN_MTL = 0x44CFDE95BEC41C73   # CoronaSkinMtl
HAIR_MTL = 0xCA41F54622C05DD6   # CoronaHairMtl
CORO_MTL = 0x448931DD70BE6506   # CoronaMtl

CLS_DATA = []
DLL_DIR_LIST = []
CLS_DIR3_LIST = []
SCENE_LIST = []
META_DATA = []


def i8(data):
    return data if data.__class__ is int else data[0]


def i16(data, offset=0):
    return struct.unpack("<H", data[offset:offset + 2])[0]


def i32(data, offset=0):
    return struct.unpack("<I", data[offset:offset + 4])[0]


def get_byte(data, offset=0):
    size = offset + 1
    value = struct.unpack('<B', data[offset:size])[0]
    return value, size


def get_short(data, offset=0):
    size = offset + 2
    value = struct.unpack('<H', data[offset:size])[0]
    return value, size


def get_long(data, offset=0):
    size = offset + 4
    value = struct.unpack('<I', data[offset:size])[0]
    return value, size


def get_float(data, offset=0):
    size = offset + 4
    value = struct.unpack('<f', data[offset:size])[0]
    return value, size


def get_longs(data, offset=0, count=1):
    size = offset + count * 4
    values = struct.unpack('<' + 'I' * count, data[offset:size])
    return values, size


def get_floats(data, offset=0, count=1):
    size = offset + count * 4
    values = struct.unpack('<' + 'f' * count, data[offset:size])
    return values, size


def _clsid(clsid):
    """Converts a CLSID to a readable string."""
    assert len(clsid) == 16
    if not clsid.strip(b"\0"):
        return ""
    return (("%08X-%04X-%04X-%02X%02X-" + "%02X" * 6) %
            ((i32(clsid, 0), i16(clsid, 4), i16(clsid, 6)) +
             tuple(map(i8, clsid[8:16]))))


###############
# DATA IMPORT #
###############

def is_maxfile(filename):
    """Test if file is a MAX OLE2 container."""
    if hasattr(filename, 'read'):
        header = filename.read(len(MAGIC))
        filename.seek(0)
    elif isinstance(filename, bytes) and len(filename) >= MIN_FILE_SIZE:
        header = filename[:len(MAGIC)]
    else:
        with open(filename, 'rb') as fp:
            header = fp.read(len(MAGIC))
    return header == MAGIC


class MaxStream(io.BytesIO):
    """Returns an instance of the BytesIO class as read-only file object."""
    def __init__(self, fp, sect, size, offset, sectorsize, fat, filesize):
        if size == UNKNOWN_SIZE:
            size = len(fat) * sectorsize
        nb_sectors = (size + (sectorsize - 1)) // sectorsize

        data = []
        for i in range(nb_sectors):
            try:
                fp.seek(offset + sectorsize * sect)
            except:
                break
            sector_data = fp.read(sectorsize)
            data.append(sector_data)
            try:
                sect = fat[sect] & FREESECT
            except IndexError:
                break
        data = b"".join(data)
        if len(data) >= size:
            data = data[:size]
            self.size = size
        else:
            self.size = len(data)
        io.BytesIO.__init__(self, data)


class MaxFileDirEntry:
    """Directory Entry for a stream or storage."""
    STRUCT_DIRENTRY = '<64sHBBIII16sIQQIII'
    DIRENTRY_SIZE = 128
    assert struct.calcsize(STRUCT_DIRENTRY) == DIRENTRY_SIZE

    def __init__(self, entry, sid, maxfile):
        self.sid = sid
        self.maxfile = maxfile
        self.kids = []
        self.kids_dict = {}
        self.used = False
        (
            self.name_raw,
            self.namelength,
            self.entry_type,
            self.color,
            self.sid_left,
            self.sid_right,
            self.sid_child,
            clsid,
            self.dwUserFlags,
            self.createTime,
            self.modifyTime,
            self.isectStart,
            self.sizeLow,
            self.sizeHigh
        ) = struct.unpack(MaxFileDirEntry.STRUCT_DIRENTRY, entry)

        if self.namelength > 64:
            self.namelength = 64
        self.name_utf16 = self.name_raw[:(self.namelength - 2)]
        self.name = maxfile._decode_utf16_str(self.name_utf16)
        if maxfile.sectorsize == 512:
            self.size = self.sizeLow
        else:
            self.size = self.sizeLow + (int(self.sizeHigh) << 32)
        self.clsid = _clsid(clsid)
        self.is_minifat = False
        if self.entry_type in (ROOT_STORE, MAX_STREAM) and self.size > 0:
            if self.size < maxfile.minisectorcutoff \
                    and self.entry_type == MAX_STREAM:
                self.is_minifat = True
            else:
                self.is_minifat = False
            maxfile._check_duplicate_stream(self.isectStart, self.is_minifat)
        self.sect_chain = None

    def build_sect_chain(self, maxfile):
        if self.sect_chain:
            return
        if self.entry_type not in (ROOT_STORE, MAX_STREAM) or self.size == 0:
            return
        self.sect_chain = list()
        if self.is_minifat and not maxfile.minifat:
            maxfile.loadminifat()
        next_sect = self.isectStart
        while next_sect != ENDOFCHAIN:
            self.sect_chain.append(next_sect)
            if self.is_minifat:
                next_sect = maxfile.minifat[next_sect]
            else:
                next_sect = maxfile.fat[next_sect]

    def build_storage_tree(self):
        if self.sid_child != FREESECT:
            self.append_kids(self.sid_child)
            self.kids.sort()

    def append_kids(self, child_sid):
        if child_sid == FREESECT:
            return
        else:
            child = self.maxfile._load_direntry(child_sid)
            if child.used:
                return
            child.used = True
            self.append_kids(child.sid_left)
            name_lower = child.name.lower()
            self.kids.append(child)
            self.kids_dict[name_lower] = child
            self.append_kids(child.sid_right)
            child.build_storage_tree()

    def __eq__(self, other):
        return self.name == other.name

    def __lt__(self, other):
        return self.name < other.name

    def __ne__(self, other):
        return not self.__eq__(other)

    def __le__(self, other):
        return self.__eq__(other) or self.__lt__(other)


class ImportMaxFile:
    """Representing an interface for importing .max files."""
    def __init__(self, filename=None):
        self._filesize = None
        self.byte_order = None
        self.directory_fp = None
        self.direntries = None
        self.dll_version = None
        self.fat = None
        self.first_difat_sector = None
        self.first_dir_sector = None
        self.first_mini_fat_sector = None
        self.fp = None
        self.header_clsid = None
        self.header_signature = None
        self.mini_sector_shift = None
        self.mini_sector_size = None
        self.mini_stream_cutoff_size = None
        self.minifat = None
        self.minifatsect = None
        self.minisectorcutoff = None
        self.minisectorsize = None
        self.ministream = None
        self.minor_version = None
        self.nb_sect = None
        self.num_difat_sectors = None
        self.num_dir_sectors = None
        self.num_fat_sectors = None
        self.num_mini_fat_sectors = None
        self.reserved1 = None
        self.reserved2 = None
        self.root = None
        self.sector_shift = None
        self.sector_size = None
        self.transaction_signature_number = None
        if filename:
            self.open(filename)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _decode_utf16_str(self, utf16_str, errors='replace'):
        unicode_str = utf16_str.decode('UTF-16LE', errors)
        return unicode_str

    def open(self, filename):
        if hasattr(filename, 'read'):
            self.fp = filename
        elif isinstance(filename, bytes) and len(filename) >= MIN_FILE_SIZE:
            self.fp = io.BytesIO(filename)
        else:
            self.fp = open(filename, 'rb')
        filesize = 0
        self.fp.seek(0, os.SEEK_END)
        try:
            filesize = self.fp.tell()
        finally:
            self.fp.seek(0)
        self._filesize = filesize
        self._used_streams_fat = []
        self._used_streams_minifat = []
        header = self.fp.read(512)
        fmt_header = '<8s16sHHHHHHLLLLLLLLLL'
        header_size = struct.calcsize(fmt_header)
        header1 = header[:header_size]
        (
            self.header_signature,
            self.header_clsid,
            self.minor_version,
            self.dll_version,
            self.byte_order,
            self.sector_shift,
            self.mini_sector_shift,
            self.reserved1,
            self.reserved2,
            self.num_dir_sectors,
            self.num_fat_sectors,
            self.first_dir_sector,
            self.transaction_signature_number,
            self.mini_stream_cutoff_size,
            self.first_mini_fat_sector,
            self.num_mini_fat_sectors,
            self.first_difat_sector,
            self.num_difat_sectors
        ) = struct.unpack(fmt_header, header1)

        self.sector_size = 2**self.sector_shift
        self.mini_sector_size = 2**self.mini_sector_shift
        if self.mini_stream_cutoff_size != 0x1000:
            self.mini_stream_cutoff_size = 0x1000
        self.nb_sect = ((filesize + self.sector_size - 1) // self.sector_size) - 1

        self.header_clsid = _clsid(header[8:24])
        self.sectorsize = self.sector_size
        self.minisectorsize = self.mini_sector_size
        self.minisectorcutoff = self.mini_stream_cutoff_size
        self._check_duplicate_stream(self.first_dir_sector)
        if self.num_mini_fat_sectors:
            self._check_duplicate_stream(self.first_mini_fat_sector)
        if self.num_difat_sectors:
            self._check_duplicate_stream(self.first_difat_sector)

        self.loadfat(header)
        self.loaddirectory(self.first_dir_sector)
        self.minifatsect = self.first_mini_fat_sector

    def close(self):
        self.fp.close()

    def _check_duplicate_stream(self, first_sect, minifat=False):
        if minifat:
            used_streams = self._used_streams_minifat
        else:
            if first_sect in (DIFSECT, FATSECT, ENDOFCHAIN, FREESECT):
                return
            used_streams = self._used_streams_fat
        if first_sect in used_streams:
            pass
        else:
            used_streams.append(first_sect)

    def sector_array(self, sect):
        ary = array.array('I', sect)
        if sys.byteorder == 'big':
            ary.byteswap()
        return ary

    def loadfat_sect(self, sect):
        if isinstance(sect, array.array):
            fat1 = sect
        else:
            fat1 = self.sector_array(sect)
        isect = None
        for isect in fat1:
            isect = isect & FREESECT
            if isect == ENDOFCHAIN or isect == FREESECT:
                break
            sector = self.getsect(isect)
            nextfat = self.sector_array(sector)
            self.fat = self.fat + nextfat
        return isect

    def loadfat(self, header):
        sect = header[76:512]
        self.fat = array.array('I')
        self.loadfat_sect(sect)
        if self.num_difat_sectors != 0:
            nb_difat_sectors = (self.sectorsize // 4) - 1
            nb_difat = (self.num_fat_sectors - 109 + nb_difat_sectors - 1) // nb_difat_sectors
            isect_difat = self.first_difat_sector
            for i in range(nb_difat):
                sector_difat = self.getsect(isect_difat)
                difat = self.sector_array(sector_difat)
                self.loadfat_sect(difat[:nb_difat_sectors])
                isect_difat = difat[nb_difat_sectors]
        if len(self.fat) > self.nb_sect:
            self.fat = self.fat[:self.nb_sect]

    def loadminifat(self):
        stream_size = self.num_mini_fat_sectors * self.sector_size
        nb_minisectors = (self.root.size + self.mini_sector_size - 1) // self.mini_sector_size
        sect = self._open(self.minifatsect, stream_size, force_FAT=True).read()
        self.minifat = self.sector_array(sect)
        self.minifat = self.minifat[:nb_minisectors]

    def getsect(self, sect):
        try:
            self.fp.seek(self.sectorsize * (sect + 1))
        except:
            print('IndexError: Sector index out of range')
        sector = self.fp.read(self.sectorsize)
        return sector

    def loaddirectory(self, sect):
        self.directory_fp = self._open(sect, force_FAT=True)
        max_entries = self.directory_fp.size // 128
        self.direntries = [None] * max_entries
        self._load_direntry(0)
        self.root = self.direntries[0]
        self.root.build_storage_tree()

    def _load_direntry(self, sid):
        if self.direntries[sid] is not None:
            return self.direntries[sid]
        self.directory_fp.seek(sid * 128)
        entry = self.directory_fp.read(128)
        self.direntries[sid] = MaxFileDirEntry(entry, sid, self)
        return self.direntries[sid]

    def _open(self, start, size=UNKNOWN_SIZE, force_FAT=False):
        if size < self.minisectorcutoff and not force_FAT:
            if not self.ministream:
                self.loadminifat()
                size_ministream = self.root.size
                self.ministream = self._open(self.root.isectStart,
                                             size_ministream, force_FAT=True)
            return MaxStream(fp=self.ministream, sect=start, size=size,
                             offset=0, sectorsize=self.minisectorsize,
                             fat=self.minifat, filesize=self.ministream.size)
        else:
            return MaxStream(fp=self.fp, sect=start, size=size,
                             offset=self.sectorsize, sectorsize=self.sectorsize,
                             fat=self.fat, filesize=self._filesize)

    def _find(self, filename):
        if isinstance(filename, str):
            filename = filename.split('/')
        node = self.root
        for name in filename:
            for kid in node.kids:
                if kid.name.lower() == name.lower():
                    break
            node = kid
        return node.sid

    def openstream(self, filename):
        sid = self._find(filename)
        entry = self.direntries[sid]
        return self._open(entry.isectStart, entry.size)


###################
# DATA PROCESSING #
###################

class MaxChunk(object):
    """Representing a chunk of a .max file."""
    __slots__ = "superid", "types", "level", "number", "size", "data"

    def __init__(self, superid, types, level, number, size, data=None):
        self.superid = superid
        self.types = types
        self.data = data
        self.number = 0
        self.level = 0
        self.size = 0

    def __str__(self):
        return "%s[%4x]%04X:%s" % ("" * self.level, self.number, self.types, self.data)


class ByteArrayChunk(MaxChunk):
    """A byte array of a .max chunk."""
    def __init__(self, superid, types, level, number, size, data):
        MaxChunk.__init__(self, superid, types, level, number, size, data)
        self.superid = superid
        self.children = []

    def get_first(self, types):
        return None

    def set(self, data, fmt, start, end):
        try:
            self.data = struct.unpack(fmt, data[start:end])
        except Exception:
            self.data = data

    def set_string(self, data):
        try:
            self.data = data.decode('UTF-16LE')
        except:
            self.data = data.decode('UTF-8', 'replace')
        finally:
            self.data = data.decode('UTF-16LE', 'ignore')

    def set_meta_data(self, data):
        metadict = {}
        try:
            mdatazip = list(zip(*[iter(data.split(b'\x00\x00\x00'))] * 2))
            metadata = list(filter(lambda tpl: b'' not in tpl, mdatazip))
            for mdata in metadata:
                header = mdata[0]
                imgkey = header[-1]
                mtitle = b''.join(mdata[1].split(b'\x00')).decode('UTF-8', 'ignore')
                if (len(header) > ROOT_STORE):
                    size = len(header[:-ROOT_STORE])
                    head = struct.unpack('<' + 'IH' * int(size / 6), header[:size])
                    meta = get_longs(header, size, len(header[size:]) // 4)[0]
                    metakey = meta[0]
                    metadict[metakey] = [(head[0], head[1])]
                elif metadict and metakey:
                    metadict[metakey].insert(-imgkey, (imgkey, mtitle))
        except:
            self.data = data
        finally:
            self.data = metadict

    def set_data(self, data):
        if (self.types in TYP_NAME):
            self.set_string(data)
        elif (self.types in TYP_LINK):
            self.set(data, '<I', 0, len(data))
        elif (self.types in TYP_REFS):
            self.set(data, '<' + 'I' * int(len(data) / 4), 0, len(data))
        elif (self.types in TYP_SHORT):
            self.set(data, '<' + 'H' * int(len(data) / 2), 0, len(data))
        elif (self.types in TYP_ARRAY):
            self.set(data, '<' + 'f' * int(len(data) / 4), 0, len(data))
        elif (self.types == 0x2510):
            self.set(data, '<' + 'f' * int(len(data) / 4 - 1) + 'I', 0, len(data))
        else:
            self.data = data


class ClassIDChunk(ByteArrayChunk):
    """The class ID subchunk of a .max chunk."""
    def __init__(self, superid, types, level, number, size, data):
        MaxChunk.__init__(self, superid, types, level, number, size, data)
        self.superid = 0x5
        self.dll = None

    def set_data(self, data):
        if (self.types == 0x2042):
            self.set_string(data)  # ClsName
        elif (self.types == 0x2060):
            self.set(data, '<IQI', 0, 16)  # DllIndex, ID, SuperID
        else:
            self.data = ":".join("%02x" % (c) for c in data)


class DirectoryChunk(ByteArrayChunk):
    """The directory chunk of a .max file."""
    def __init__(self, superid, types, level, number, size, data):
        MaxChunk.__init__(self, superid, types, level, number, size, data)
        self.superid = 0x4

    def set_data(self, data):
        if (self.types in (0x2037, 0x2039)):
            self.set_string(data)


class ContainerChunk(MaxChunk):
    """A container chunk in a .max file wich includes byte arrays."""
    def __init__(self, superid, types, level, number, size, data, primReader=ByteArrayChunk):
        MaxChunk.__init__(self, superid, types, level, number, size, data)
        self.primReader = primReader
        self.superid = superid

    def __str__(self):
        return "%s[%4x]%04X" % ("" * self.level, self.number, self.types)

    def get_first(self, types):
        for child in self.children:
            if (child.types == types):
                return child
        return None

    def set_data(self, data):
        reader = ChunkReader()
        self.children = reader.get_chunks(self.superid, data, self.level + 1, ContainerChunk, self.primReader)


class SceneChunk(ContainerChunk):
    """The scene chunk of a .max file."""
    def __init__(self, superid, types, data, number, level, size, primReader=ByteArrayChunk):
        MaxChunk.__init__(self, superid, types, data, number, level, size)
        self.primReader = primReader
        self.superid = 0x2

    def __str__(self):
        return "%s[%4x]%s" % ("" * self.level, self.number, get_cls_name(self))

    def set_data(self, data):
        reader = ChunkReader()
        self.children = reader.get_chunks(self.superid, data, self.level + 1, SceneChunk, ByteArrayChunk)


class ChunkReader(object):
    """The chunk reader class for decoding the byte arrays."""
    def __init__(self, name=None):
        self.name = name

    def get_chunks(self, superid, data, level, conReader, primReader):
        chunks = []
        offset = 0
        if (len(data) > ROOT_STORE and level == 0):
            root, step = get_short(data, 0)
            long, step = get_long(data, step)
            print("  reading '%s'..." % self.name, len(data))
            if (root == 0x8B1F):
                long, step = get_long(data, step)
                if (long in (0xB000000, 0xA040000, 0x8000001E)):
                    data = zlib.decompress(data, zlib.MAX_WBITS | 32)
            elif (superid in (0xA, 0xB)):
                chunk = primReader(superid, root, level, 1, len(data), data)
                chunk.set_meta_data(data)
                return [chunk]
        while offset < len(data):
            offset, chunk = self.get_next_chunk(superid, data, offset, level,
                                                len(chunks), conReader, primReader)
            chunks.append(chunk)
        return chunks

    def get_next_chunk(self, superid, data, offset, level, number, conReader, primReader):
        header = 6
        typ, siz = struct.unpack("<Hi", data[offset:offset + header])
        chunksize = siz & UNKNOWN_SIZE
        if (siz == 0):
            siz, = struct.unpack("<q", data[offset + header:offset + header + 8])
            header += 8
            chunksize = siz & MAXFILE_SIZE
        if (siz < 0):
            chunk = conReader(superid, typ, level, number, chunksize, data, primReader)
        else:
            chunk = primReader(superid, typ, level, number, chunksize, data)
        chunkdata = data[offset + header:offset + chunksize]
        chunk.set_data(chunkdata)
        return offset + chunksize, chunk


class Material(object):
    """Representing a material chunk of a scene chunk."""
    def __init__(self):
        self.data = {}

    def set(self, name, value):
        self.data[name] = value

    def get(self, name, default=None):
        value = None
        if (name in self.data):
            value = self.data[name]
        if (value is None):
            return default
        return value


def get_node(index):
    if isinstance(index, tuple):
        index = index[0]
    global SCENE_LIST
    if (index < len(SCENE_LIST[0].children)):
        return SCENE_LIST[0].children[index]
    return None


def get_node_parent(node):
    parent = None
    if (node):
        chunk = node.get_first(0x0960)
        if (chunk and len(chunk.data) > 3):
            idx, offset = get_long(chunk.data, 0)
            parent = get_node(idx)
    return parent


def get_node_name(node):
    if (node):
        name = node.get_first(0x0962)
        if (name):
            return name.data
    return node.__class__.__name__


def get_class(chunk):
    global CLS_DIR3_LIST
    if chunk and (chunk.types < len(CLS_DIR3_LIST)):
        return CLS_DIR3_LIST[chunk.types]
    return None


def get_dll(chunk):
    global DLL_DIR_LIST
    dll = chunk.get_first(0x2060)
    if (dll):
        idx = dll.data[0]
        if (idx < len(DLL_DIR_LIST)):
            return DLL_DIR_LIST[idx]
    return None


def get_metadata(index):
    global META_DATA
    pathdata = META_DATA[0].data if META_DATA else None
    if pathdata:
        pathname = pathdata.get(index)
        if pathname is None:
            for key, uid in pathdata.items():
                if index in uid[2]:
                    return uid[0][1]
        else:
            return pathname[0][1]
    return pathdata


def get_guid(chunk):
    clid = get_class(chunk)
    if (clid):
        guid = clid.get_first(0x2060)
        if guid is not None:
            return guid.data[1]
    return chunk.types if chunk else 0


def get_super_id(chunk):
    clid = get_class(chunk)
    if (clid):
        suid = clid.get_first(0x2060)
        if suid is not None:
            return suid.data[2]
    return 0x0


def get_cls_name(chunk):
    clid = get_class(chunk)
    if (clid and clid.get_first(0x2042)):
        cls_name = clid.get_first(0x2042).data
        try:
            return "'%s'" % (cls_name)
        except:
            return "'%r'" % (cls_name)
    return u"%04X" % (chunk.types)


def get_references(chunk):
    references = []
    if chunk is None:
        return references
    refs = chunk.get_first(0x2034)
    if (refs):
        references = [get_node(idx) for idx in refs.data]
    return references


def get_reference(chunk):
    references = {}
    refs = chunk.get_first(0x2035)
    if (refs):
        offset = 1
        while offset < len(refs.data):
            key = refs.data[offset]
            offset += 1
            idx = refs.data[offset]
            offset += 1
            references[key] = get_node(idx)
    return references


def read_chunks(maxfile, name, conReader=ContainerChunk, primReader=ByteArrayChunk, superId=None):
    with maxfile.openstream(name) as file:
        scene = file.read()
        reader = ChunkReader(name)
        return reader.get_chunks(superId, scene, 0, conReader, primReader)


def read_class_data(maxfile, filename):
    global CLS_DATA
    CLS_DATA = read_chunks(maxfile, 'ClassData', superId=6)


def read_class_directory(maxfile, filename):
    global CLS_DIR3_LIST
    try:
        CLS_DIR3_LIST = read_chunks(maxfile, 'ClassDirectory3', ContainerChunk, ClassIDChunk)
    except:
        CLS_DIR3_LIST = read_chunks(maxfile, 'ClassDirectory', ContainerChunk, ClassIDChunk)
    for clsdir in CLS_DIR3_LIST:
        clsdir.dll = get_dll(clsdir)


def read_directory(maxfile, filename):
    global DLL_DIR_LIST
    DLL_DIR_LIST = read_chunks(maxfile, 'DllDirectory', ContainerChunk, DirectoryChunk)


def get_bitmap(chunk):
    if (chunk is not None):
        pathstring = matlib = None
        parameters = get_references(chunk)
        if (len(parameters) >= 2) and parameters[1]:
            custom = parameters[1].get_first(0x3)
            if (custom is not None):
                pathchunk = custom.get_first(0x1230)
                pathlink = custom.get_first(0x1260)
                if (pathchunk and pathchunk.data):
                    pathstring = pathchunk.data
                elif (pathlink and pathlink.children):
                    matlib = pathlink.children[0]
            else:
                references = get_references(parameters[1])
                for reference in references:
                    if reference is not None:
                        for ref in reference.children:
                            imglink = ref.get_first(0x1260)
                            if imglink and imglink.children:
                                matlib = imglink.children[0]
                if matlib is None:
                    for block in parameters[1].children:
                        if (block.children and get_guid(block) == 0x3333):
                            matlib = block.children[0]
        if (matlib and matlib.data):
            idsize = len(matlib.data[:-4])
            metaidx = get_longs(matlib.data, idsize, len(matlib.data[idsize:]) // 4)[0]
            pathstring = get_metadata(metaidx[0])
        return pathstring
    return None


def get_parameter(values, fmt):
    if (fmt == 0x1):
        siz = len(values.data) - 12
        para, offset = get_floats(values.data, siz, 3)
    else:
        siz = len(values.data) - 4
        para, offset = get_float(values.data, siz)
    return para


IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.tga', '.bmp', '.exr',
             '.hdr', '.gif', '.psd', '.dds', '.webp', '.tx'}


def _valid_image(path):
    return isinstance(path, str) and Path(path).suffix.lower() in IMAGE_EXT


def _fname(path):
    """Filnamnet ur en sokvag (fungerar for bade \\ och /)."""
    return re.split(r'[\\/]', str(path))[-1]


def find_bitmaps_deep(chunk, depth=8, visited=None):
    """Hittar ALLA Bitmap-noder under chunk (genom Color Correction, CoronaMix osv).
    Returnerar lista av (sokvag, kedja av klassnamn) i ordning."""
    results = []
    if chunk is None or depth < 0:
        return results
    if visited is None:
        visited = set()
    if id(chunk) in visited:
        return results
    visited.add(id(chunk))
    name = get_cls_name(chunk)
    if name.strip("'") == "Bitmap":
        try:
            path = get_bitmap(chunk)
        except Exception:
            path = None
        if _valid_image(path):
            results.append((path, [name]))
        return results
    children = []
    try:
        children += [r for r in get_references(chunk) if r is not None]
    except Exception:
        pass
    try:
        children += [r for r in get_reference(chunk).values() if r is not None]
    except Exception:
        pass
    for child in children:
        for path, chain in find_bitmaps_deep(child, depth - 1, visited):
            results.append((path, [name] + chain))
    return results


def find_bitmap_deep(chunk, depth=8):
    found = find_bitmaps_deep(chunk, depth)
    return found[0] if found else (None, [])


def bitmaps_of(chunk):
    """Alla distinkta bildsokvagar under chunk, i ordning."""
    out = []
    for path, chain in find_bitmaps_deep(chunk):
        if path not in out:
            out.append(path)
    return out


def bitmap_of(chunk):
    """Forsta bilden under chunk (foljer referenser genom justeringsnoder)."""
    found = bitmaps_of(chunk)
    if found:
        return found[0]
    try:
        path = get_bitmap(chunk)
    except Exception:
        path = None
    return path if _valid_image(path) else None


def get_corona_material(mtl):
    material = Material()
    try:
        corona = mtl[0].children
        parameter = get_reference(mtl[0])
        bitmap = bitmap_of(parameter.get(0))
        shinmap = bitmap_of(parameter.get(1))
        glossmap = bitmap_of(parameter.get(2))
        transmap = bitmap_of(parameter.get(5))
        normal = get_references(parameter.get(6))
        material.set('diffuse', get_parameter(corona[0x03], 1))
        material.set('specular', get_parameter(corona[0x05], 1))
        material.set('shinines', get_parameter(corona[0x0D], 2))
        material.set('emissive', get_parameter(corona[0x08], 1))
        material.set('glossines', get_parameter(corona[0x0A], 2))
        material.set('metallic', get_parameter(corona[0x0E], 2))
        material.set('refraction', get_parameter(corona[0x3F], 2))
        material.set('opacity', 1.0 - get_parameter(corona[0x0B], 2))
        if (bitmap is not None):
            material.set('bitmap', Path(bitmap).name)
        if (shinmap is not None):
            material.set('shinmap', Path(shinmap).name)
        if (glossmap is not None):
            material.set('glossmap', Path(glossmap).name)
        if (transmap is not None):
            material.set('transmap', Path(transmap).name)
        if (normal and len(normal) > 0):
            values = normal[0].children
            refs = get_references(normal[0])
            if refs:
                normalmap = bitmap_of(refs[0])
                material.set('strength', get_parameter(values[0x03], 2))
                if (normalmap is not None):
                    material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'CoronaMtl' Error:", exc)
    return material


def get_skinhair_material(mtl):
    material = Material()
    try:
        corona = mtl[0].children
        parameter = get_reference(mtl[0])
        bitmap = bitmap_of(parameter.get(0))
        shinmap = bitmap_of(parameter.get(1))
        glossmap = bitmap_of(parameter.get(5))
        transmap = bitmap_of(parameter.get(2))
        normal = get_references(parameter.get(6))
        material.set('diffuse', get_parameter(corona[0x03], 1))
        material.set('specular', get_parameter(corona[0x0A], 1))
        material.set('shinines', get_parameter(corona[0x0C], 2))
        material.set('glossines', get_parameter(corona[0x08], 2))
        material.set('refraction', get_parameter(corona[0x0B], 2))
        material.set('opacity', 1.0 - get_parameter(corona[0x15], 2))
        if (bitmap is not None):
            material.set('bitmap', Path(bitmap).name)
        if (shinmap is not None):
            material.set('shinmap', Path(shinmap).name)
        if (glossmap is not None):
            material.set('glossmap', Path(glossmap).name)
        if (transmap is not None):
            material.set('transmap', Path(transmap).name)
        if (normal and len(normal) > 0):
            values = normal[0].children
            refs = get_references(normal[0])
            if refs:
                normalmap = bitmap_of(refs[0])
                material.set('strength', get_parameter(values[0x03], 2))
                if (normalmap is not None):
                    material.set('normalmap', Path(normalmap).name)
    except Exception as exc:
        print("\t'CoronaSkinMtl' Error:", exc)
    return material


def get_corophysical_material(mtl):
    """CoronaPhysicalMtl. Texturplatser (enligt filnamnen i testfilen):
    0 = base color, 2 = roughness, 20 = normal, 27 = metalness."""
    material = Material()
    parameter = {}
    try:
        corona = mtl[0].children
        parameter = get_reference(mtl[0])
        base = bitmaps_of(parameter.get(0))
        rough = bitmaps_of(parameter.get(2))
        metal = bitmaps_of(parameter.get(27))
        material.set('diffuse', get_parameter(corona[0x03], 1))
        material.set('roughness', get_parameter(corona[0x0F], 2))
        material.set('refraction', get_parameter(corona[0x1B], 2))
        material.set('opacity', get_parameter(corona[0x36], 2))
        if base:
            material.set('bitmap', _fname(base[0]))
        if len(base) > 1:
            material.set('bitmap2', _fname(base[1]))
        if rough:
            material.set('roughmap', _fname(rough[0]))
        if metal:
            material.set('metalmap', _fname(metal[0]))
        # Alla ovriga texturplatser (t.ex. opacity): sparas som slotN, och som
        # 'opacitymap' om filnamnet ser ut som en opacity/alpha/mask-bild.
        for key, ref in sorted(parameter.items()):
            if key in (0, 2, 20, 27) or ref is None:
                continue
            others = bitmaps_of(ref)
            if not others:
                continue
            fn = _fname(others[0])
            material.set('slot%d' % key, fn)
            print("\t  extra texturplats %d: %s" % (key, fn))
            if re.search(r'opac|alpha|mask|cutout|transp', fn, re.I):
                material.set('opacitymap', fn)
    except Exception as exc:
        print("\t'CoronaPhysicalMtl' Error:", exc)
    try:
        nmaps = bitmaps_of(parameter.get(20))
        if nmaps:
            material.set('normalmap', _fname(nmaps[0]))
        if len(nmaps) > 1:
            material.set('normalmap2', _fname(nmaps[1]))
        normal = get_references(parameter.get(20))
        if normal and normal[0] is not None:
            material.set('strength', get_parameter(normal[0].children[0x03], 2))
    except Exception as exc:
        print("\t'CoronaPhysicalMtl normal' Error:", exc)
    return material


def get_matrix_mesh_material(node):
    refs = get_reference(node)
    if (refs):
        prs = refs.get(0, None)
        msh = refs.get(1, None)
        mat = refs.get(3, None)
        lyr = refs.get(6, None)
    else:
        refs = get_references(node)
        prs = refs[0] if len(refs) else None
        msh = refs[1] if len(refs) > 1 else None
        mat = refs[3] if len(refs) > 3 else None
        lyr = refs[6] if len(refs) > 6 else None
    return prs, msh, mat, lyr


########################
# COLLECT + WRITE .MS  #
########################

MATERIAL_SUPER_ID = 0xC00   # superklass for alla material i Max
MULTI_MTL = 0x0200          # Multi/Sub-Object
CORONA_GUIDS = (CORO_MTL, COPHY_MTL, SKIN_MTL, HAIR_MTL, LAYER_MTL)


def _is_material(chunk):
    return get_super_id(chunk) == MATERIAL_SUPER_ID or get_guid(chunk) in CORONA_GUIDS + (MULTI_MTL,)


def material_name(mat):
    mtl_id = mat.get_first(0x0FA0)
    return mtl_id.children[0].data if (mtl_id and mtl_id.children) else get_cls_name(mat)


def sub_slots(mat):
    """Delmaterialen i ett Multi/Sub som (plats, chunk), plats 1 = forsta platsen
    i Maxs materialList. Referens 0 ar parameterblocket, darefter kommer
    delmaterialen i platsordning (tomma platser blir None)."""
    keyed = get_reference(mat)
    if keyed:
        items = sorted(keyed.items())
    else:
        items = list(enumerate(get_references(mat)))
    if not items:
        return []
    first_key, first = items[0]
    base = first_key + 1 if (first is not None and not _is_material(first)) else first_key
    return [(k - base + 1, ch) for k, ch in items if k >= base]


def layered_base(mat, depth=0):
    """Basmaterialet i ett CoronaLayeredMtl (forsta Corona-lagret)."""
    if depth > 5:
        return None
    refs = get_references(mat)
    if not refs or refs[0] is None:
        return None
    layers = get_reference(refs[0])
    for key in sorted(layers):
        layer = layers[key]
        if layer is None:
            continue
        uid = get_guid(layer)
        if uid == LAYER_MTL:
            base = layered_base(layer, depth + 1)
            if base is not None:
                return base
        elif uid in CORONA_GUIDS:
            return layer
    return None


def corona_data(mat):
    """Parametrar for ett Corona-material som dict (med 'material' = originalnamnet)."""
    name = material_name(mat)
    src = mat
    if get_guid(mat) == LAYER_MTL:
        src = layered_base(mat)
        if src is None:
            print("  OBS: Layered-materialet '%s' har inget Corona-lager" % name)
            return {"material": name}
        print("  OBS: Layered-materialet '%s' blir bara basmaterialet '%s'" % (name, material_name(src)))
    uid = get_guid(src)
    if uid == CORO_MTL:
        material = get_corona_material(get_references(src))
    elif uid in (HAIR_MTL, SKIN_MTL):
        material = get_skinhair_material(get_references(src))
    else:
        material = get_corophysical_material(get_references(src))
    data = {"material": name}
    data.update(material.data)
    return data


def walk_material(mat, path, out, depth=0):
    """Samlar (sokvag, chunk) for alla Corona-material under mat.
    Sokvagen ar 'root' for objektets eget material och t.ex. 'root/2' for
    plats 2 i ett Multi/Sub, 'root/2/1' for nastlade Multi/Sub."""
    if mat is None or depth > 6:
        return
    uid = get_guid(mat)
    if uid in CORONA_GUIDS:
        out.append((path, mat))
    elif uid == MULTI_MTL:
        slots = sub_slots(mat)
        out.append((path + "#n", len(slots)))
        print("  Multi/Sub '%s' (%s): %d platser" % (material_name(mat), path, len(slots)))
        for idx, sub in slots:
            walk_material(sub, "%s/%d" % (path, idx), out, depth + 1)
    elif get_super_id(mat) == MATERIAL_SUPER_ID:
        for sub in [r for r in get_references(mat) if r is not None and get_guid(r) in CORONA_GUIDS]:
            print("  OBS: '%s' (%s) har Corona-delmaterialet '%s', sadana material hanteras inte"
                  % (material_name(mat), get_cls_name(mat), material_name(sub)))


def collect_corona(filepath):
    """Returnerar (mtls, objs):
    mtls: nyckel -> parametrar for varje unikt Corona-material
    objs: objektnamn -> {sokvag: nyckel, sokvag#n: antal platser i Multi/Sub}"""
    global SCENE_LIST, META_DATA
    if not is_maxfile(filepath):
        print("Filen verkar inte vara en 3ds Max-fil:", filepath)
        return {}, {}
    maxfile = ImportMaxFile(filepath)
    read_class_data(maxfile, filepath)
    read_directory(maxfile, filepath)
    read_class_directory(maxfile, filepath)

    metasid = min(max(e.sid for e in maxfile.direntries if e is not None), 11)
    SCENE_LIST = read_chunks(maxfile, 'Scene', SceneChunk)
    META_DATA = read_chunks(maxfile, maxfile.direntries[metasid].name, superId=metasid) if metasid >= 0xA else []

    keys = {}   # id(chunk) -> nyckel, sa att delade material blir ett Physical
    mtls = {}
    objs = {}
    for node in SCENE_LIST[0].children:
        try:
            is_node = (isinstance(node, SceneChunk) and get_guid(node) == 0x1 and get_super_id(node) == 0x1) \
                or get_node_parent(node)
            if not is_node:
                continue
            prs, msh, mat, lyr = get_matrix_mesh_material(node)
            entries = []
            walk_material(mat, "root", entries)
            if not any(not p.endswith("#n") for p, ch in entries):
                continue
            objname = get_node_name(node)
            if objname in objs:
                print("  OBS: flera objekt heter '%s', bara det forsta far material" % objname)
                continue
            paths = {}
            for path, ch in entries:
                if path.endswith("#n"):
                    paths[path] = ch
                    continue
                key = keys.get(id(ch))
                if key is None:
                    key = "m%d" % (len(mtls) + 1)
                    keys[id(ch)] = key
                    mtls[key] = corona_data(ch)
                paths[path] = key
                print("  %s %s -> '%s'" % (objname, path, mtls[key]["material"]))
            objs[objname] = paths
        except Exception as exc:
            print("  Fel pa nod:", exc)
    return mtls, objs


def ms_val(v):
    if isinstance(v, (tuple, list)):
        return "[%s]" % ",".join("%.6f" % x for x in v)
    if isinstance(v, float):
        return "%.6f" % v
    return '"%s"' % str(v).replace("\\", "\\\\").replace('"', '\\"')


def write_ms(mtls, objs, path):
    """coronaMtls: nyckel -> parametrar.  coronaObjs: objektnamn -> {sokvag: nyckel}."""
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("coronaMtls = Dictionary #string\n")
        for key, d in mtls.items():
            f.write("d = Dictionary #string\n")
            for k, v in d.items():
                if v is not None:
                    f.write('d["%s"] = %s\n' % (k, ms_val(v)))
            f.write("coronaMtls[%s] = d\n" % ms_val(key))
        f.write("coronaObjs = Dictionary #string\n")
        for obj, paths in objs.items():
            f.write("p = Dictionary #string\n")
            for k, v in paths.items():
                f.write('p[%s] = %s\n' % (ms_val(k), ms_val(v)))
            f.write("coronaObjs[%s] = p\n" % ms_val(obj))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print('Anvandning: python corona_export.py "scen.max" "corona_data.ms"')
        sys.exit(1)
    mtls, objs = collect_corona(sys.argv[1])
    write_ms(mtls, objs, sys.argv[2])
    print("Klart: %d Corona-material pa %d objekt skrevs till %s" % (len(mtls), len(objs), sys.argv[2]))
