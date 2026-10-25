#!/usr/bin/env python3
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time


class LiveSmokeError(RuntimeError):
    pass


def normalize_display(value):
    display = value if value.startswith(":") else ":" + value
    if not re.fullmatch(r":[0-9]+", display):
        raise argparse.ArgumentTypeError("display must be an X11 number such as 121 or :121")
    return display


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run an isolated Hard Truck Wine/Xvfb smoke")
    parser.add_argument("--game", required=True, type=Path, help="disposable game folder containing prism3d.exe")
    parser.add_argument("--prefix", required=True, type=Path, help="dedicated Wine prefix; never share a live prefix")
    parser.add_argument("--output", required=True, type=Path, help="screenshot and log output folder")
    parser.add_argument("--display", required=True, type=normalize_display, help="unused X11 display, for example :121")
    parser.add_argument("--resolution", choices=("1024x768", "1920x1080"), default="1024x768")
    parser.add_argument("--smoke", choices=("quick-setup", "camera", "accessibility"), default="quick-setup")
    parser.add_argument("--title", default="HTMODSDKSMOKE", help="unique window title used to find the game")
    parser.add_argument("--boot-wait", type=float, default=30)
    parser.add_argument("--map-wait", type=float, default=25)
    return parser.parse_args(argv)


def validate_camera_bindings(game):
    config = Path(game) / "game/usr/config.cfg"
    text = config.read_text(encoding="utf-8", errors="replace")
    expected = {"y": "mleft", "o": "mright", "i": "mlookup", "k": "mlookdown"}
    missing = [
        key for key, impulse in expected.items()
        if not re.search(rf'(?im)^\s*bind_imp\s+{key}\s+"?{impulse}"?\s*$', text)
    ]
    if missing:
        raise LiveSmokeError(
            "camera test copy lacks required look bindings for " + ", ".join(missing)
            + "; add the test-only y/o/i/k bindings documented in sdk/README.md"
        )


def validate_game_log(text, require_quick_setup=False, allow_headless_graphics=False):
    lowered = text.casefold()
    if "setting state game" not in lowered:
        raise LiveSmokeError("game log did not reach GAME state")
    if require_quick_setup and "script ui/cosetup.sc read ok" not in lowered:
        raise LiveSmokeError("game log did not load ui/cosetup.sc")
    for marker in ("page fault", "0x88888786", "creation failed", "unhandled exception", "assertion failed"):
        if marker in lowered:
            raise LiveSmokeError(f"fatal game/Wine log marker: {marker}")
    benign = ["dx8_gamma", "setviewport"]
    if allow_headless_graphics:
        benign.append("no modes found")
    unexpected = [
        line for line in text.splitlines()
        if "*** ERROR ***" in line
        and not any(marker in line.casefold() for marker in benign)
    ]
    if unexpected:
        raise LiveSmokeError("unexpected game errors: " + " | ".join(unexpected[-5:]))


def validate_truck_samples(samples):
    if not samples:
        raise LiveSmokeError("no truck memory samples were captured")
    transforms = {sample["physical_transform_hex"] for sample in samples}
    if len(transforms) != 1:
        raise LiveSmokeError("physical transform changed during the parked camera battery")


def find_player_truck(pid):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.ptrace(16, pid, None, None) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    try:
        os.waitpid(pid, 0)
        maps = Path(f"/proc/{pid}/maps").read_text().splitlines()
        with open(f"/proc/{pid}/mem", "rb", buffering=0) as mem:
            regions = []
            for line in maps:
                fields = line.split()
                lo, hi = (int(value, 16) for value in fields[0].split("-"))
                regions.append((line, lo, hi, fields[1], fields[-1] if len(fields) >= 6 else ""))
            game_base = next((
                lo for line, lo, _hi, _perms, path in regions
                if path.casefold().endswith(("\\game.dll", "/game.dll"))
                and line.split()[2] == "00000000"
            ), None)
            if game_base is None:
                raise LiveSmokeError("game.dll base mapping not found for truck probe")
            target = struct.pack("<I", game_base + 0xB0300)
            matches = []
            for _line, lo, hi, perms, _path in regions:
                if not perms.startswith("rw") or hi - lo > 1024 * 1024 * 1024:
                    continue
                try:
                    mem.seek(lo)
                    data = mem.read(hi - lo)
                except OSError:
                    continue
                offset = data.find(target)
                while offset >= 0:
                    address = lo + offset
                    offset = data.find(target, offset + 1)
                    try:
                        mem.seek(address)
                        obj = mem.read(0x1000)
                        speed = struct.unpack_from("<f", obj, 0x7A4)[0]
                        mass = struct.unpack_from("<f", obj, 0x994)[0]
                        weight = struct.unpack_from("<f", obj, 0x2D4)[0]
                        if 0 <= speed < 100000 and abs(mass - 400.0) < 0.01 and abs(weight - 2000.0) < 0.01:
                            matches.append(address)
                    except (OSError, struct.error):
                        pass
            if len(matches) != 1:
                raise LiveSmokeError(f"expected one player truck, found {len(matches)}")
            return matches[0], game_base
    finally:
        libc.ptrace(17, pid, None, None)


class WineSession:
    def __init__(self, game, prefix, output, display, resolution="1024x768", title="HTMODSDKSMOKE"):
        self.game = Path(game).resolve()
        self.prefix = Path(prefix).resolve()
        self.output = Path(output).resolve()
        self.display = normalize_display(display)
        self.resolution = resolution
        self.title = title
        self.processes = []
        self.xvfb_process = None
        self.openbox_process = None
        self.game_process = None
        self.environment = os.environ.copy()
        self.environment.update(DISPLAY=self.display, WINEPREFIX=str(self.prefix))
        self.screenshots = []
        self.truck_address = None
        self.truck_game_base = None
        self.truck_mem = None
        self.truck_samples = []
        self.cleaned = False

    def start_truck_probe(self):
        if self.game_process is None or self.game_process.poll() is not None:
            raise LiveSmokeError("Wine must be running before truck probing")
        self.truck_address, self.truck_game_base = find_player_truck(self.game_process.pid)
        self.truck_mem = open(f"/proc/{self.game_process.pid}/mem", "rb", buffering=0)
        (self.output / "truck-memory").mkdir(exist_ok=True)

    def capture_truck_sample(self, name):
        self.truck_mem.seek(self.truck_address)
        obj = self.truck_mem.read(0x1000)
        if len(obj) != 0x1000:
            raise LiveSmokeError("short truck memory read")
        (self.output / "truck-memory" / (name + ".bin")).write_bytes(obj)
        self.truck_samples.append({
            "label": name,
            "wall": time.time(),
            "address": hex(self.truck_address),
            "game_base": hex(self.truck_game_base),
            "position": struct.unpack_from("<3f", obj, 0x124),
            "velocity": struct.unpack_from("<3f", obj, 0x9D4),
            "speed_raw": struct.unpack_from("<f", obj, 0x7A4)[0],
            "look_y_pitch": struct.unpack_from("<f", obj, 0x3D0)[0],
            "look_x_yaw": struct.unpack_from("<f", obj, 0x3D4)[0],
            "physical_transform_hex": obj[0xF0:0x130].hex(),
        })

    def __enter__(self):
        try:
            self.start()
        except BaseException:
            self.cleanup()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.cleanup()

    def start(self):
        if not (self.game / "prism3d.exe").is_file():
            raise LiveSmokeError(f"prism3d.exe not found in disposable game folder: {self.game}")
        missing = [name for name in ("Xvfb", "openbox", "wine", "wineserver", "xdotool", "scrot") if not shutil.which(name)]
        if missing:
            raise LiveSmokeError("missing live-smoke commands: " + ", ".join(missing))
        self.output.mkdir(parents=True, exist_ok=True)
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.xvfb_process = self.spawn(
            "xvfb",
            ["Xvfb", self.display, "-screen", "0", self.resolution + "x24", "-nolisten", "tcp"],
        )
        time.sleep(2)
        self.require_alive()
        self.openbox_process = self.spawn("openbox", ["openbox"])
        time.sleep(2)
        self.require_alive()
        self.game_process = self.spawn(
            "wine",
            ["wine", "./prism3d.exe", "-game", "game", "-icon", "game.ico", "-title", self.title],
            cwd=self.game,
        )
        self.require_alive()
        self.wait_for_window(45)

    def require_alive(self):
        for name, process in (
            ("Xvfb", self.xvfb_process),
            ("openbox", self.openbox_process),
            ("Wine", self.game_process),
        ):
            if process is not None and process.poll() is not None:
                raise LiveSmokeError(
                    f"{name} exited on {self.display}; display may be unavailable or already in use"
                )

    def spawn(self, name, command, cwd=None):
        with (self.output / (name + ".log")).open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        self.processes.append(process)
        with (self.output / "owned-pids").open("a", encoding="utf-8") as record:
            record.write(f"{process.pid} {name}\n")
        return process

    def command(self, arguments, timeout=20):
        return subprocess.run(
            arguments,
            env=self.environment,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=True,
        )

    def window_id(self):
        self.require_alive()
        result = subprocess.run(
            ["xdotool", "search", "--name", self.title],
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )
        candidates = []
        for value in result.stdout.split():
            geometry = subprocess.run(
                ["xdotool", "getwindowgeometry", value],
                env=self.environment,
                text=True,
                capture_output=True,
                check=False,
            )
            sizes = re.findall(r"([0-9]+)x([0-9]+)", geometry.stdout)
            if sizes:
                width, height = map(int, sizes[-1])
                candidates.append((width * height, value))
        if not candidates:
            raise LiveSmokeError(f"no visible game window titled {self.title!r}")
        return max(candidates)[1]

    def wait_for_window(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.game_process.poll() is not None:
                raise LiveSmokeError(f"Wine exited before creating a window; see {self.output / 'wine.log'}")
            try:
                return self.window_id()
            except LiveSmokeError:
                time.sleep(1)
        raise LiveSmokeError("timed out waiting for the game window")

    def activate(self):
        window = self.window_id()
        self.command(["xdotool", "windowactivate", window])
        time.sleep(1)
        return window

    def key(self, key, repeat=1):
        window = self.activate()
        self.command(["xdotool", "key", "--window", window, "--repeat", str(repeat), "--delay", "100", key])
        time.sleep(1)

    def text(self, value):
        window = self.activate()
        self.command(["xdotool", "type", "--window", window, "--delay", "100", value])
        time.sleep(1)

    def hold(self, key, seconds):
        window = self.activate()
        self.command(["xdotool", "keydown", "--window", window, key])
        try:
            time.sleep(seconds)
        finally:
            self.command(["xdotool", "keyup", "--window", window, key])
        time.sleep(1)

    def console(self, command, settle=3):
        self.key("asciitilde")
        time.sleep(2)
        window = self.activate()
        self.command(["xdotool", "type", "--window", window, "--delay", "100", command])
        self.command(["xdotool", "key", "--window", window, "Return"])
        time.sleep(settle)

    def close_console(self, settle=5):
        self.key("asciitilde")
        time.sleep(settle)

    def enter_map(self, wait):
        self.console("map west", settle=wait)
        self.close_console()

    def capture(self, name):
        self.require_alive()
        self.activate()
        destination = self.output / name
        temporary = self.output / ("." + name + ".new.png")
        temporary.unlink(missing_ok=True)
        self.require_alive()
        self.command(["scrot", "-z", str(temporary)])
        if not temporary.is_file():
            raise LiveSmokeError(f"scrot did not create {temporary}")
        os.replace(temporary, destination)
        self.screenshots.append(name)
        if self.truck_mem is not None:
            self.capture_truck_sample(name)
        return destination

    def game_log(self):
        return (self.game / "prism3d.log").read_text(encoding="utf-8", errors="replace")

    def finish(self, require_quick_setup=True, allow_headless_graphics=False):
        if self.game_process.poll() is not None:
            raise LiveSmokeError(f"Wine exited during smoke; see {self.output / 'wine.log'}")
        text = self.game_log()
        validate_game_log(
            text,
            require_quick_setup=require_quick_setup,
            allow_headless_graphics=allow_headless_graphics,
        )
        truck_summary = None
        if self.truck_address is not None:
            validate_truck_samples(self.truck_samples)
            (self.output / "truck-samples.json").write_text(
                json.dumps(self.truck_samples, indent=2) + "\n", encoding="utf-8"
            )
            truck_summary = {
                "samples": len(self.truck_samples),
                "physical_transform_unchanged": True,
            }
        summary = {
            "status": "process-and-log-checks-passed",
            "game": str(self.game),
            "prefix": str(self.prefix),
            "display": self.display,
            "resolution": self.resolution,
            "screenshots": self.screenshots,
            "visual_review": "required; this harness does not judge image content",
            "quick_setup_ok_pressed": False,
            "headless_graphics_error_allowed": allow_headless_graphics,
            "truck_probe": truck_summary,
        }
        (self.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        if self.truck_mem is not None:
            self.truck_mem.close()
        if self.prefix.exists():
            try:
                subprocess.run(
                    ["wineserver", "-k"],
                    env=self.environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    continue
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def run_quick_setup(session, args):
    time.sleep(args.boot_wait)
    session.capture("01-main-menu.png")
    session.enter_map(args.map_wait)
    session.capture("02-quick-setup.png")
    session.finish(require_quick_setup=True)


def run_camera(session, args):
    time.sleep(args.boot_wait)
    session.capture("00-main-menu.png")
    session.enter_map(args.map_wait)
    session.console("ui t cosetup")
    session.close_console(settle=10)
    session.start_truck_probe()

    def reset(camera):
        session.key("3")
        session.key(camera)

    session.key("3")
    session.capture("01-truck-before.png")
    session.key("1")
    session.capture("02-cab-stock-level.png")
    session.hold("k", 8)
    session.capture("03-cab-stock-down-ignored.png")
    reset("1")
    session.hold("i", 8)
    session.capture("04-cab-stock-up-ignored.png")
    reset("1")
    session.hold("y", 2)
    session.capture("05-cab-stock-left.png")
    session.hold("k", 8)
    session.capture("06-cab-stock-left-down-ignored.png")
    session.key("3")
    session.capture("07-truck-after-stock-cab-left.png")
    reset("1")
    session.hold("o", 2)
    session.capture("08-cab-stock-right.png")
    session.hold("i", 8)
    session.capture("09-cab-stock-right-up-ignored.png")
    session.key("3")
    session.capture("10-truck-after-stock-cab-right.png")
    session.key("2")
    session.capture("11-orbit-level.png")
    session.hold("k", 8)
    session.capture("12-orbit-down.png")
    reset("2")
    session.hold("i", 8)
    session.capture("13-orbit-up.png")
    reset("2")
    session.capture("14-orbit-level-reset.png")
    for camera in range(1, 8):
        session.key(str(camera))
        session.capture(f"15-camera-{camera}.png")
    session.finish(require_quick_setup=True)


def run_accessibility(session, args):
    time.sleep(args.boot_wait)
    session.capture("01-main-normal.png")
    session.key("Tab")
    session.capture("02-main-forward.png")
    session.key("Tab", repeat=4)
    session.capture("03-main-options-focused.png")
    session.key("shift+Tab")
    session.capture("04-main-backward.png")
    session.key("Tab")
    session.capture("05-main-options-refocused.png")
    session.key("Return")
    time.sleep(5)
    session.capture("06-options-enter.png")
    session.capture("07-graphics-selected.png")

    session.console("ui t graphics")
    session.close_console(settle=8)
    session.key("Tab")
    session.capture("08-graphics-radio-focused.png")
    session.key("Tab")
    session.capture("09-sound-radio-focused.png")
    session.key("Return")
    time.sleep(5)
    session.capture("10-sound-selected.png")
    session.key("Tab")
    session.capture("11-sound-persistent.png")

    session.console("ui t sound")
    session.close_console(settle=8)
    session.key("Tab", repeat=3)
    session.capture("13-controls-radio-focused.png")
    session.key("Return")
    time.sleep(5)
    session.capture("14-controls-selected.png")

    session.console("ui t controls")
    session.close_console(settle=8)
    session.console("ui t options")
    session.close_console(settle=8)
    session.enter_map(args.map_wait)
    session.text("A")
    session.capture("15-quick-company.png")
    session.key("Tab")
    session.text("B")
    session.capture("16-quick-owner.png")
    session.key("Tab")
    session.capture("17-quick-ok-focused.png")
    session.key("shift+Tab")
    session.key("shift+Tab")
    session.key("Tab")
    session.key("Tab")
    session.capture("18-quick-ok-unpressed.png")
    session.finish(require_quick_setup=True, allow_headless_graphics=True)


def main(argv=None):
    args = parse_args(argv)
    session = WineSession(**{key: value for key, value in vars(args).items() if key in {
        "game", "prefix", "output", "display", "resolution", "title",
    }})
    try:
        if args.smoke == "camera":
            validate_camera_bindings(args.game)
        with session:
            if args.smoke == "quick-setup":
                run_quick_setup(session, args)
            elif args.smoke == "camera":
                run_camera(session, args)
            else:
                run_accessibility(session, args)
    except (LiveSmokeError, OSError, subprocess.SubprocessError) as error:
        print(f"live smoke failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
