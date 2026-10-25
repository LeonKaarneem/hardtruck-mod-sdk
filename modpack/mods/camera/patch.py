import struct

from htmod.errors import PEError, StateError
from htmod.patch import Anchor, BinaryMod, BytePatch, PatchState
from htmod.pe import align


JT_MODE6 = 0x33E18
JT_MODE6_ORIG = 0x10032D5B
JT_MODE6_PATCH = 0x100336BC
RESET30 = 0x39FE8
RESET30_ORIG = b"\xc7\x86\xd0\x03\x00\x00\x00\x00\xf0\xc1"
RESET30_PATCH = b"\xc7\x86\xd0\x03\x00\x00\x00\x00\x00\x00"
RST_CAM2 = 0x35C56
RST_CAM7 = 0x35E13
RST_SETUP_ORIG = b"\x89\xbe\xd0\x03\x00\x00"
RST_CAM2_ANCHOR = (
    RST_SETUP_ORIG
    + b"\x89\x86\xd4\x03\x00\x00"
    + b"\x89\x86\xd8\x03\x00\x00"
    + b"\x8b\x96\x78\x07\x00\x00"
    + b"\x89\x96\xc8\x0a\x00\x00"
    + b"\x89\x86\x30\x0d\x00\x00"
)
RST_CAM7_ANCHOR = (
    RST_SETUP_ORIG
    + b"\x89\x86\xd4\x03\x00\x00"
    + b"\x89\x86\xd8\x03\x00\x00"
    + b"\x8b\x86\x78\x07\x00\x00"
    + b"\x89\x86\xc8\x0a\x00\x00"
)
RST_CAM2_RESUME = 0x10035C5C
RST_CAM7_RESUME = 0x10035E19
CAB = 0x32D40
CAB_ANCHOR = 0x32D33
CAB_ANCHOR_BYTES = (
    b"\x8b\x86\xd4\x03\x00\x00"
    + b"\x8d\x94\x24\x10\x02\x00\x00"
    + b"\x89\x9c\x24\x10\x02\x00\x00"
)
ORBIT_POS = 0x33708
ORBIT_POS_BYTES = b"\x89\x9c\x24\x74\x01\x00\x00"
ORIENT_POS = 0x3386F
ORIENT_POS_BYTES = b"\x89\x9c\x24\x08\x02\x00\x00"
NEWSEC_NAME = ".htcam"
NEWSEC_SIZE = 0x1000
NEWSEC_FLAGS = 0xE0000020
CAB_RESUME = 0x10033D9D
CAB_ROTATE_IAT = 0x100AB218
ORBIT_POS_RESUME = 0x1003370F
ORIENT_POS_RESUME = 0x10033876
CAB_PITCH_HI = 0x42A00000
CAB_PITCH_LO = 0xC2A00000
ORB_PITCH_HI = 0x42200000
ORB_PITCH_LO = 0xC1200000


def i_jmp(src_va, dst_va):
    return b"\xe9" + struct.pack("<i", dst_va - (src_va + 5))


def i_call(src_va, dst_va):
    return b"\xe8" + struct.pack("<i", dst_va - (src_va + 5))


def clamp_routine(va, lo_bits, hi_bits):
    code = b""
    code += b"\x8b\x86\xd0\x03\x00\x00"
    code += b"\xb9" + struct.pack("<I", hi_bits)
    code += b"\x39\xc8"
    code += b"\x7e\x03"
    code += b"\x89\xc8"
    code += b"\xc3"
    code += b"\xb9" + struct.pack("<I", lo_bits)
    code += b"\x39\xc8"
    code += b"\x7d\x02"
    code += b"\x89\xc8"
    code += b"\xc3"
    return code


def cab_clamp_routine(va, lo_bits, hi_bits):
    code = b""
    code += b"\x8b\x86\xd0\x03\x00\x00"
    code += b"\x85\xc0"
    code += b"\x79\x0c"
    code += b"\xb9" + struct.pack("<I", lo_bits)
    code += b"\x39\xc8"
    code += b"\x76\x02"
    code += b"\x89\xc8"
    code += b"\xc3"
    code += b"\xb9" + struct.pack("<I", hi_bits)
    code += b"\x39\xc8"
    code += b"\x7e\x02"
    code += b"\x89\xc8"
    code += b"\xc3"
    return code


def build_helpers(base_va):
    va = base_va
    labels = {}
    labels["htcam_cab"] = va
    labels["cab_rotate_relocs"] = []
    code = b""
    code += b"\x89\x84\x24\x1c\x00\x00\x00"
    for eye_offset, saved_offset in ((0x88, 0x44), (0x8C, 0x48), (0x90, 0x4C)):
        code += b"\x8b\x84\x24" + struct.pack("<I", eye_offset)
        code += b"\x89\x84\x24" + struct.pack("<I", saved_offset)
    for saved_offset, negative_offset in ((0x44, 0x10), (0x48, 0x14), (0x4C, 0x18)):
        code += b"\x8b\x84\x24" + struct.pack("<I", saved_offset)
        code += b"\x35\x00\x00\x00\x80"
        code += b"\x89\x84\x24" + struct.pack("<I", negative_offset)
    code += b"\x8d\x4c\x24\x58"
    code += b"\x8d\x54\x24\x10"
    code += b"\x52\xff\xd7"
    clamp_call_offset = len(code)
    code += i_call(va + len(code), base_va)
    code += b"\x89\x84\x24\x20\x00\x00\x00"
    code += b"\x8b\x84\x24\x1c\x00\x00\x00"
    code += b"\x89\x9c\x24\x10\x02\x00\x00"
    code += b"\x89\x84\x24\x14\x02\x00\x00"
    code += b"\x89\x9c\x24\x18\x02\x00\x00"
    code += b"\x8d\x94\x24\x10\x02\x00\x00"
    code += b"\x52"
    code += b"\x8d\x4c\x24\x5c"
    labels["cab_rotate_relocs"].append(len(code) + 2)
    code += b"\xff\x15" + struct.pack("<I", CAB_ROTATE_IAT)
    code += b"\x8b\x84\x24\x20\x00\x00\x00"
    code += b"\x89\x84\x24\x10\x02\x00\x00"
    code += b"\x89\x9c\x24\x14\x02\x00\x00"
    code += b"\x89\x9c\x24\x18\x02\x00\x00"
    code += b"\x8d\x94\x24\x10\x02\x00\x00"
    code += b"\x52"
    code += b"\x8d\x4c\x24\x5c"
    labels["cab_rotate_relocs"].append(len(code) + 2)
    code += b"\xff\x15" + struct.pack("<I", CAB_ROTATE_IAT)
    code += b"\x8d\x4c\x24\x58"
    code += b"\x8d\x54\x24\x44"
    code += b"\x52\xff\xd7"
    code += i_jmp(va + len(code), CAB_RESUME)
    cab = code

    labels["htcam_orbit"] = va + len(cab)
    code = b""
    code += b"\x51"
    code += i_call(va + len(cab) + len(code), base_va)
    code += b"\x59"
    code += b"\x89\x84\x24\x74\x01\x00\x00"
    code += i_jmp(va + len(cab) + len(code), ORBIT_POS_RESUME)
    orbit = code

    labels["htcam_orient"] = va + len(cab) + len(orbit)
    code = b""
    code += b"\x51"
    code += i_call(va + len(cab) + len(orbit) + len(code), base_va)
    code += b"\x59"
    code += b"\x89\x84\x24\x08\x02\x00\x00"
    code += i_jmp(va + len(cab) + len(orbit) + len(code), ORIENT_POS_RESUME)
    orient = code

    labels["htcam_clamp80"] = va + len(cab) + len(orbit) + len(orient)
    c80 = cab_clamp_routine(labels["htcam_clamp80"], CAB_PITCH_LO, CAB_PITCH_HI)
    labels["htcam_clamp40"] = labels["htcam_cab"] + len(cab) + len(orbit) + len(orient) + len(c80)
    c40 = clamp_routine(labels["htcam_clamp40"], ORB_PITCH_LO, ORB_PITCH_HI)

    labels["htcam_rst_cam2"] = base_va
    pre_rst = cab + orbit + orient + c80 + c40
    labels["htcam_rst_cam2"] = va + len(pre_rst)
    code = b""
    code += b"\xc7\x86\xd0\x03\x00\x00\x00\x00\x00\x00"
    code += i_jmp(va + len(pre_rst) + len(code), RST_CAM2_RESUME)
    rst2 = code
    labels["htcam_rst_cam7"] = va + len(pre_rst) + len(rst2)
    code = b""
    code += b"\xc7\x86\xd0\x03\x00\x00\x00\x00\x00\x00"
    code += i_jmp(va + len(pre_rst) + len(rst2) + len(code), RST_CAM7_RESUME)
    rst7 = code

    blob = bytearray(pre_rst + rst2 + rst7)

    def fix(at_off, target):
        struct.pack_into("<i", blob, at_off + 1, target - (base_va + at_off + 5))

    fix(clamp_call_offset, labels["htcam_clamp80"])
    fix(len(cab) + 1, labels["htcam_clamp40"])
    fix(len(cab) + len(orbit) + 1, labels["htcam_clamp40"])
    return bytes(blob), labels


class CameraMod(BinaryMod):
    def __init__(self):
        super().__init__("camera", "game/sys/game.dll", ())

    @staticmethod
    def _patches(base_va=None, image_base=0x10000000):
        labels = None if base_va is None else build_helpers(base_va)[1]
        patched = None if labels is None else {
            CAB: i_jmp(image_base + CAB, labels["htcam_cab"]) + b"\x90\x90",
            ORBIT_POS: i_jmp(image_base + ORBIT_POS, labels["htcam_orbit"]) + b"\x90\x90",
            ORIENT_POS: i_jmp(image_base + ORIENT_POS, labels["htcam_orient"]) + b"\x90\x90",
            RST_CAM2: i_jmp(image_base + RST_CAM2, labels["htcam_rst_cam2"]) + b"\x90",
            RST_CAM7: i_jmp(image_base + RST_CAM7, labels["htcam_rst_cam7"]) + b"\x90",
        }

        def replacement(offset, original):
            return original if patched is None else patched[offset]

        return (
            BytePatch("jump-table mode6", JT_MODE6, struct.pack("<I", JT_MODE6_ORIG),
                      struct.pack("<I", JT_MODE6_PATCH)),
            BytePatch("look_y reset", RESET30, RESET30_ORIG, RESET30_PATCH),
            BytePatch("cab hook", CAB, CAB_ANCHOR_BYTES[-7:], replacement(CAB, CAB_ANCHOR_BYTES[-7:]),
                      (Anchor(CAB_ANCHOR, CAB_ANCHOR_BYTES[:-7]),)),
            BytePatch("orbit position", ORBIT_POS, ORBIT_POS_BYTES,
                      replacement(ORBIT_POS, ORBIT_POS_BYTES)),
            BytePatch("orbit orientation", ORIENT_POS, ORIENT_POS_BYTES,
                      replacement(ORIENT_POS, ORIENT_POS_BYTES)),
            BytePatch("camera2 reset", RST_CAM2, RST_SETUP_ORIG,
                      replacement(RST_CAM2, RST_SETUP_ORIG),
                      (Anchor(RST_CAM2 + 6, RST_CAM2_ANCHOR[6:]),)),
            BytePatch("camera7 reset", RST_CAM7, RST_SETUP_ORIG,
                      replacement(RST_CAM7, RST_SETUP_ORIG),
                      (Anchor(RST_CAM7 + 6, RST_CAM7_ANCHOR[6:]),)),
        )

    @staticmethod
    def _write(image, field):
        for patch in CameraMod._patches(
            None if field == "original" else image.image_base + image.section(NEWSEC_NAME).virtual_address,
            image.image_base,
        ):
            replacement = getattr(patch, field)
            image.data[patch.offset:patch.offset + len(replacement)] = replacement

    @staticmethod
    def _valid_section(image):
        section = image.section(NEWSEC_NAME)
        index = image.sections.index(section)
        later_sections = image.sections[index + 1:]
        if any(later.name != ".uitab" for later in later_sections):
            return False
        if (section.virtual_size, section.raw_size, section.flags) != (
            NEWSEC_SIZE, NEWSEC_SIZE, NEWSEC_FLAGS
        ):
            return False
        previous_raw_end = max(
            item.raw_offset + item.raw_size for item in image.sections[:index]
        )
        if previous_raw_end > section.raw_offset or any(
            image.data[previous_raw_end:section.raw_offset]
        ):
            return False
        previous_raw_end = section.raw_offset + section.raw_size
        for later in later_sections:
            if previous_raw_end > later.raw_offset or any(
                image.data[previous_raw_end:later.raw_offset]
            ):
                return False
            previous_raw_end = later.raw_offset + later.raw_size
        last = image.sections[-1]
        if len(image.data) != previous_raw_end or last.raw_offset + last.raw_size != previous_raw_end:
            return False
        base_va = image.image_base + section.virtual_address
        helpers, labels = build_helpers(base_va)
        expected = helpers + bytes(NEWSEC_SIZE - len(helpers))
        if image.data[section.raw_offset:section.raw_offset + NEWSEC_SIZE] != expected:
            return False
        directory_rva, directory_size = image.data_directory(5)
        block = image.rva_to_offset(directory_rva + directory_size - 12, 12)
        entries = [0x3000 | offset for offset in labels["cab_rotate_relocs"]]
        if block is None or bytes(image.data[block:block + 12]) != struct.pack(
            "<IIHH", section.virtual_address, 12, *entries
        ):
            return False
        expected_image = align(
            last.virtual_address + align(last.virtual_size, image.section_alignment),
            image.section_alignment,
        )
        return struct.unpack_from("<I", image.data, image.optional_offset + 56)[0] == expected_image

    def inspect(self, image):
        try:
            states = [patch.state(image.data) for patch in self._patches()]
            if not any(section.name == NEWSEC_NAME for section in image.sections):
                return PatchState.ORIGINAL if all(
                    state == PatchState.ORIGINAL for state in states
                ) else PatchState.FOREIGN
            base_va = image.image_base + image.section(NEWSEC_NAME).virtual_address
            patched_states = [
                patch.state(image.data) for patch in self._patches(base_va, image.image_base)
            ]
            if all(state == PatchState.PATCHED for state in patched_states) and self._valid_section(image):
                return PatchState.PATCHED
            return PatchState.FOREIGN
        except (PEError, struct.error):
            return PatchState.FOREIGN

    def apply(self, image):
        if self.inspect(image) != PatchState.ORIGINAL:
            raise StateError("camera is not ORIGINAL; refusing apply")
        working = bytearray(image.data)
        candidate = type(image).parse(working)
        new_vaddr, _new_rawoff = candidate.next_section_layout(NEWSEC_SIZE)
        helpers, labels = build_helpers(candidate.image_base + new_vaddr)
        candidate.append_section(
            NEWSEC_NAME, helpers + bytes(NEWSEC_SIZE - len(helpers)), NEWSEC_FLAGS
        )
        candidate.append_highlow_relocations(new_vaddr, labels["cab_rotate_relocs"])
        self._write(candidate, "patched")
        if self.inspect(candidate) != PatchState.PATCHED:
            raise StateError("camera failed post-apply verification")
        image.data[:] = working

    def restore(self, image):
        if self.inspect(image) == PatchState.ORIGINAL and self.allow_original_restore:
            return
        if self.inspect(image) != PatchState.PATCHED:
            raise StateError("camera is not PATCHED; refusing restore")
        working = bytearray(image.data)
        candidate = type(image).parse(working)
        section = candidate.section(NEWSEC_NAME)
        _helpers, labels = build_helpers(candidate.image_base + section.virtual_address)
        candidate.remove_highlow_relocations(
            section.virtual_address, labels["cab_rotate_relocs"]
        )
        candidate.remove_section(
            NEWSEC_NAME,
            original_eof=section.raw_offset,
            original_image_size=align(section.virtual_address, candidate.section_alignment),
        )
        self._write(candidate, "original")
        if self.inspect(candidate) != PatchState.ORIGINAL:
            raise StateError("camera failed post-restore verification")
        image.data[:] = working


MOD = CameraMod()


def main():
    from htmod.patch import legacy_main
    return legacy_main(MOD)
