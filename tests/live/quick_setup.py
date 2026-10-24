#!/usr/bin/env python3
import sys

from run_wine import main


if __name__ == "__main__":
    raise SystemExit(main(["--smoke", "quick-setup", *sys.argv[1:]]))
