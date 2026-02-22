#!/usr/bin/env python3
from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,str(ROOT/"scripts"/"translate_book.py"),"--source-lang","en","--target-lang","zh-CN"],check=True)
