import struct

from htmod.errors import PEError, StateError
from htmod.patch import BinaryMod, PatchState
from htmod.pe import align


HOOK_RVA = 0x70E20
HOOK_ORIGINAL = bytes.fromhex("8b4c240456")
POST_KEYBOARD = 0x10070E9E
ORIGINAL_RESUME = 0x10070E27
NEWSEC_NAME = ".uitab"
NEWSEC_SIZE = 0x1000
NEWSEC_FLAGS = 0x60000020
WRAPPER = bytes.fromhex(
    "8b4c2404568b3183fe0275348a510f84d275148079080074276683790c0f7520b840000000eb1d"
    "f6c203741480790800740e6683790c0f7507b880000000eb0431c0eb05e9554ef7ffe9d94df7ff"
)


def i_jmp(src_va, dst_va):
    return b"\xe9" + struct.pack("<i", dst_va - (src_va + 5))


def build_wrapper(base_va):
    blob = bytearray(WRAPPER)
    struct.pack_into("<i", blob, 0x45, POST_KEYBOARD - (base_va + 0x44 + 5))
    struct.pack_into("<i", blob, 0x4A, ORIGINAL_RESUME - (base_va + 0x49 + 5))
    return bytes(blob)


class UIKeyboardMod(BinaryMod):
    def __init__(self):
        super().__init__("ui_keyboard", "game/sys/game.dll", ())

    @staticmethod
    def _write_hook(image, original):
        offset = image.va_to_offset(image.image_base + HOOK_RVA, 5)
        if offset is None:
            raise PEError("ui_keyboard hook is not mapped")
        replacement = HOOK_ORIGINAL if original else i_jmp(
            image.image_base + HOOK_RVA,
            image.image_base + image.section(NEWSEC_NAME).virtual_address,
        )
        image.data[offset:offset + 5] = replacement

    @staticmethod
    def _hook_is_original(image):
        offset = image.va_to_offset(image.image_base + HOOK_RVA, 5)
        return offset is not None and image.data[offset:offset + 5] == HOOK_ORIGINAL

    @staticmethod
    def _valid_section(image):
        section = image.section(NEWSEC_NAME)
        if section != image.sections[-1]:
            return False
        last_va_end = max(
            item.virtual_address + align(item.virtual_span, image.section_alignment)
            for item in image.sections[:-1]
        )
        expected_vaddr = align(last_va_end, image.section_alignment)
        previous_raw_end = max(
            item.raw_offset + item.raw_size for item in image.sections[:-1]
        )
        expected_raw_offset = align(previous_raw_end, image.file_alignment)
        if section.virtual_address != expected_vaddr \
                or section.raw_offset != expected_raw_offset:
            return False
        if (section.virtual_size, section.raw_size, section.flags) != (
            NEWSEC_SIZE, NEWSEC_SIZE, NEWSEC_FLAGS
        ):
            return False
        if len(image.data) != section.raw_offset + section.raw_size:
            return False
        if previous_raw_end > section.raw_offset or any(
            image.data[previous_raw_end:section.raw_offset]
        ):
            return False
        base_va = image.image_base + section.virtual_address
        expected = build_wrapper(base_va) + bytes(NEWSEC_SIZE - len(WRAPPER))
        if image.data[section.raw_offset:section.raw_offset + NEWSEC_SIZE] != expected:
            return False
        expected_image = align(section.virtual_address + NEWSEC_SIZE, image.section_alignment)
        return struct.unpack_from("<I", image.data, image.optional_offset + 56)[0] == expected_image

    def inspect(self, image):
        try:
            if not any(section.name == NEWSEC_NAME for section in image.sections):
                return PatchState.ORIGINAL if self._hook_is_original(image) else PatchState.FOREIGN
            base_va = image.image_base + image.section(NEWSEC_NAME).virtual_address
            offset = image.va_to_offset(image.image_base + HOOK_RVA, 5)
            expected_hook = i_jmp(image.image_base + HOOK_RVA, base_va)
            if offset is not None and image.data[offset:offset + 5] == expected_hook \
                    and self._valid_section(image):
                return PatchState.PATCHED
            return PatchState.FOREIGN
        except (PEError, struct.error):
            return PatchState.FOREIGN

    def apply(self, image):
        if self.inspect(image) != PatchState.ORIGINAL:
            raise StateError("ui_keyboard is not ORIGINAL; refusing apply")
        working = bytearray(image.data)
        candidate = type(image).parse(working)
        base_va = candidate.image_base + candidate.next_section_layout(NEWSEC_SIZE)[0]
        wrapper = build_wrapper(base_va)
        candidate.append_section(NEWSEC_NAME, wrapper + bytes(NEWSEC_SIZE - len(wrapper)), NEWSEC_FLAGS)
        self._write_hook(candidate, original=False)
        if self.inspect(candidate) != PatchState.PATCHED:
            raise StateError("ui_keyboard failed post-apply verification")
        image.data[:] = working

    def restore(self, image):
        if self.inspect(image) == PatchState.ORIGINAL and self.allow_original_restore:
            return
        if self.inspect(image) != PatchState.PATCHED:
            raise StateError("ui_keyboard is not PATCHED; refusing restore")
        working = bytearray(image.data)
        candidate = type(image).parse(working)
        section = candidate.section(NEWSEC_NAME)
        candidate.remove_section(
            NEWSEC_NAME,
            original_eof=section.raw_offset,
            original_image_size=align(section.virtual_address, candidate.section_alignment),
        )
        self._write_hook(candidate, original=True)
        if self.inspect(candidate) != PatchState.ORIGINAL:
            raise StateError("ui_keyboard failed post-restore verification")
        image.data[:] = working


MOD = UIKeyboardMod()


def main():
    from htmod.patch import legacy_main
    return legacy_main(MOD)
