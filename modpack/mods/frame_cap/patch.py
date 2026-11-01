import hashlib
import struct

from htmod.errors import PEError, StateError
from htmod.patch import BinaryMod, PatchState
from htmod.pe import PEImage, align

HOOK_VA = 0x40607C
HOOK_ORIGINAL = bytes.fromhex('e8bfc4ffff')
CLOCK_VA = 0x402CC0
SYS_FRAME_VA = 0x402540
RESUME_VA = 0x406081
PRISTINE_SHA256 = '5a8f94da924b05620e2a69daa09682dae3cab6b301ddc463b54f5750b514bddd'
PATCHED_SHA256 = '38bd905e7493326788d5e02ff790ce92eac43c7b533b1f9e8dc185ae5de92a63'
NEWSEC_NAME = '.htfps'
NEWSEC_SIZE = 0x1000
NEWSEC_FLAGS = 0xE0000020
INTERVAL_US = 16667
LONG_GAP_US = 250000
UINT64_MASK = 2**64 - 1
STATE_OFFSET = 0x200
COUNTER_OFFSET = STATE_OFFSET
INITIALIZED_OFFSET = STATE_OFFSET + 8
NEXT_OFFSET = STATE_OFFSET + 16
LAST_OFFSET = STATE_OFFSET + 24
NOW_OFFSET = STATE_OFFSET + 32


def i_jmp(src_va, dst_va):
    return b'\xe9' + struct.pack('<i', dst_va - (src_va + 5))


class Asm:
    def __init__(self, base):
        self.base = base
        self.code = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, data):
        self.code.extend(data)

    def imm32(self, value):
        self.emit(struct.pack('<I', value))

    def label(self, name):
        self.labels[name] = len(self.code)

    def branch(self, opcode, label):
        self.emit(opcode)
        self.fixups.append((len(self.code), label))
        self.emit(bytes(4))

    def call(self, target):
        source = self.base + len(self.code)
        self.emit(b'\xe8' + struct.pack('<i', target - (source + 5)))

    def jmp(self, target):
        source = self.base + len(self.code)
        self.emit(b'\xe9' + struct.pack('<i', target - (source + 5)))

    def finish(self):
        for offset, label in self.fixups:
            source = self.base + offset
            target = self.base + self.labels[label]
            struct.pack_into('<i', self.code, offset, target - (source + 4))
        return bytes(self.code)


def build_wrapper(base_va):
    a = Asm(base_va)
    counter = base_va + COUNTER_OFFSET
    initialized = base_va + INITIALIZED_OFFSET
    next_deadline = base_va + NEXT_OFFSET
    last = base_va + LAST_OFFSET
    now = base_va + NOW_OFFSET

    def load_eax(address):
        a.emit(b'\xa1')
        a.imm32(address)

    def load_edx(address):
        a.emit(b'\x8b\x15')
        a.imm32(address)

    def store_eax(address):
        a.emit(b'\xa3')
        a.imm32(address)

    def store_edx(address):
        a.emit(b'\x89\x15')
        a.imm32(address)

    def cmp_eax(address):
        a.emit(b'\x3b\x05')
        a.imm32(address)

    def cmp_edx(address):
        a.emit(b'\x3b\x15')
        a.imm32(address)

    def store_now():
        store_eax(now)
        store_edx(now + 4)

    def load_now():
        load_eax(now)
        load_edx(now + 4)

    def add_interval_to_now_and_store():
        a.emit(b'\x05')
        a.imm32(INTERVAL_US)
        a.emit(b'\x83\xd2\x00')
        store_eax(next_deadline)
        store_edx(next_deadline + 4)

    a.emit(b'\x83\x05')
    a.imm32(counter)
    a.emit(b'\x01\x83\x15')
    a.imm32(counter + 4)
    a.emit(b'\x00')
    a.call(CLOCK_VA)
    store_now()

    a.emit(b'\x83\x3d')
    a.imm32(initialized)
    a.emit(b'\x00')
    a.branch(b'\x0f\x84', 'reset')

    cmp_edx(last + 4)
    a.branch(b'\x0f\x82', 'reset')
    a.branch(b'\x0f\x87', 'gap')
    cmp_eax(last)
    a.branch(b'\x0f\x82', 'reset')

    a.label('gap')
    a.emit(b'\x2b\x05')
    a.imm32(last)
    a.emit(b'\x1b\x15')
    a.imm32(last + 4)
    a.emit(b'\x83\xfa\x00')
    a.branch(b'\x0f\x85', 'reset')
    a.emit(b'\x3d')
    a.imm32(LONG_GAP_US)
    a.branch(b'\x0f\x87', 'reset')

    a.label('schedule')
    load_now()
    cmp_edx(next_deadline + 4)
    a.branch(b'\x0f\x82', 'wait')
    a.branch(b'\x0f\x87', 'slow')
    cmp_eax(next_deadline)
    a.branch(b'\x0f\x83', 'slow')

    a.label('wait')
    a.call(CLOCK_VA)
    store_now()
    cmp_edx(last + 4)
    a.branch(b'\x0f\x82', 'reset')
    a.branch(b'\x0f\x87', 'wait_compare')
    cmp_eax(last)
    a.branch(b'\x0f\x82', 'reset')

    a.label('wait_compare')
    cmp_edx(next_deadline + 4)
    a.branch(b'\x0f\x82', 'wait')
    a.branch(b'\x0f\x87', 'after_wait')
    cmp_eax(next_deadline)
    a.branch(b'\x0f\x82', 'wait')

    a.label('after_wait')
    load_eax(next_deadline)
    load_edx(next_deadline + 4)
    a.emit(b'\x05')
    a.imm32(INTERVAL_US)
    a.emit(b'\x83\xd2\x00')
    store_eax(next_deadline)
    store_edx(next_deadline + 4)
    a.branch(b'\xe9', 'finish')

    a.label('slow')
    a.branch(b'\xe9', 'rebase')

    a.label('reset')
    load_now()
    a.emit(b'\xc7\x05')
    a.imm32(initialized)
    a.imm32(1)

    a.label('rebase')
    add_interval_to_now_and_store()

    a.label('finish')
    load_now()
    store_eax(last)
    store_edx(last + 4)
    a.emit(b'\x68')
    a.imm32(RESUME_VA)
    a.jmp(SYS_FRAME_VA)
    return a.finish()


class DeadlineSchedule:
    def __init__(self):
        self.initialized = False
        self.next = 0
        self.last = 0

    def frame(self, samples):
        if not samples:
            raise ValueError('at least one clock sample is required')
        index = 0
        now = samples[index]
        if not self.initialized or now < self.last or now - self.last > LONG_GAP_US:
            self.initialized = True
            self.next = (now + INTERVAL_US) & UINT64_MASK
            self.last = now
            return now, self.next
        while now < self.next:
            index += 1
            if index >= len(samples):
                raise ValueError('clock samples exhausted while waiting')
            now = samples[index]
            if now < self.last:
                self.next = (now + INTERVAL_US) & UINT64_MASK
                self.last = now
                return now, self.next
        if now == samples[0]:
            self.next = (now + INTERVAL_US) & UINT64_MASK
        else:
            self.next = (self.next + INTERVAL_US) & UINT64_MASK
        self.last = now
        return now, self.next


class FrameCapMod(BinaryMod):
    def __init__(self):
        super().__init__('frame_cap', 'prism3d.exe', ())

    @staticmethod
    def _hook_offset(image):
        offset = image.va_to_offset(HOOK_VA, 5)
        if offset is None:
            raise PEError('frame-cap hook is not mapped')
        return offset

    def _write_hook(self, image, original):
        replacement = HOOK_ORIGINAL if original else i_jmp(
            HOOK_VA, image.image_base + image.section(NEWSEC_NAME).virtual_address
        )
        offset = self._hook_offset(image)
        image.data[offset:offset + 5] = replacement

    @staticmethod
    def _valid_section(image):
        section = image.validate_appended_tail(NEWSEC_NAME)
        if (section.virtual_size, section.raw_size, section.flags) != (
            NEWSEC_SIZE, NEWSEC_SIZE, NEWSEC_FLAGS
        ):
            return False
        base = image.image_base + section.virtual_address
        wrapper = build_wrapper(base)
        expected = wrapper + bytes(NEWSEC_SIZE - len(wrapper))
        return image.data[section.raw_offset:section.raw_offset + NEWSEC_SIZE] == expected

    def inspect(self, image):
        try:
            offset = self._hook_offset(image)
            if not any(section.name == NEWSEC_NAME for section in image.sections):
                if hashlib.sha256(image.data).hexdigest() != PRISTINE_SHA256:
                    return PatchState.FOREIGN
                return PatchState.ORIGINAL if image.data[offset:offset + 5] == HOOK_ORIGINAL else PatchState.FOREIGN
            if hashlib.sha256(image.data).hexdigest() != PATCHED_SHA256:
                return PatchState.FOREIGN
            base = image.image_base + image.section(NEWSEC_NAME).virtual_address
            if image.data[offset:offset + 5] == i_jmp(HOOK_VA, base) and self._valid_section(image):
                return PatchState.PATCHED
            return PatchState.FOREIGN
        except (PEError, struct.error):
            return PatchState.FOREIGN

    def apply(self, image):
        if self.inspect(image) != PatchState.ORIGINAL:
            raise StateError('frame_cap is not ORIGINAL; refusing apply')
        working = bytearray(image.data)
        candidate = type(image).parse(working)
        base = candidate.image_base + candidate.next_section_layout(NEWSEC_SIZE)[0]
        wrapper = build_wrapper(base)
        candidate.append_section(NEWSEC_NAME, wrapper + bytes(NEWSEC_SIZE - len(wrapper)), NEWSEC_FLAGS)
        self._write_hook(candidate, original=False)
        if self.inspect(candidate) != PatchState.PATCHED:
            raise StateError('frame_cap failed post-apply verification')
        image.data[:] = working

    def restore(self, image):
        if self.inspect(image) == PatchState.ORIGINAL and self.allow_original_restore:
            return
        if self.inspect(image) != PatchState.PATCHED:
            raise StateError('frame_cap is not PATCHED; refusing restore')
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
            raise StateError('frame_cap failed post-restore verification')
        image.data[:] = working


MOD = FrameCapMod()


def main(argv=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Apply, inspect, or restore the registered Hard Truck frame cap component')
    parser.add_argument('exe', type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--restore', action='store_true')
    args = parser.parse_args(argv)
    data = bytearray(args.exe.read_bytes())
    if args.check:
        print(MOD.inspect_bytes(data).value)
        return 0
    image = PEImage.parse(data)
    if args.apply:
        MOD.apply(image)
    else:
        MOD.restore(image)
    args.exe.write_bytes(image.data)
    print(MOD.inspect_bytes(image.data).value)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
