#!/usr/bin/env python3
"""HTB Agent Toolkit — run `python htb.py --help`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from htb_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
