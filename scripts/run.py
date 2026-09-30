#!/usr/bin/env python3
"""Pass make variables to the converter as arguments, without shell interpolation."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    action = sys.argv[1]
    source = os.environ.get('INPUT')
    if not source:
        sys.exit(f'Usage: make {action} INPUT=path [OUTPUT=path]')
    arguments = [sys.executable, str(Path(__file__).resolve().parents[1] / 'misphere.py'), action, source]
    if action != 'inspect':
        output = os.environ.get('OUTPUT')
        if not output:
            path = Path(source).resolve()
            output = str(path.with_name(path.stem + '_s.mp4') if action == 'convert'
                         else path.with_name(path.name + '_s'))
        arguments.append(output)
        for variable, option in [('WIDTH', '--width'), ('SECONDS', '--seconds'),
                                 ('SEAM_DEGREES', '--seam-degrees')]:
            value = os.environ.get(variable)
            if value:
                arguments.extend((option, value))
    return subprocess.call(arguments)


if __name__ == '__main__':
    sys.exit(main())
