"""Carve the embedded files out of a Unity WebGL `*.data` (UnityWebData1.0) container.

Layout (little endian, no count field):
    char   magic[16]      "UnityWebData1.0\\0"
    uint32 headerSize     size of the header block in bytes
    repeat until headerSize:
        uint32 offset, uint32 size, uint32 pathLength, char path[pathLength]

Extraction is read-only; the container itself is never rewritten.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'inputs', 'ntd.data')
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'inputs', 'extracted')


def main():
    data = open(SRC, 'rb').read()
    if data[:16] != b'UnityWebData1.0\x00':
        sys.exit(f'{SRC}: not a UnityWebData1.0 container')
    header_size = struct.unpack_from('<I', data, 16)[0]
    os.makedirs(OUT, exist_ok=True)

    p = 20
    files = []
    while p < header_size:
        off, size, nlen = struct.unpack_from('<III', data, p)
        name = data[p + 12:p + 12 + nlen].decode('utf-8')
        files.append((name, off, size))
        p += 12 + nlen
    if p != header_size:
        sys.exit(f'header parse overrun: {p} != {header_size}')

    for name, off, size in files:
        dst = os.path.join(OUT, os.path.basename(name))
        with open(dst, 'wb') as fh:
            fh.write(data[off:off + size])
        print(f'{name:45s} off={off:<10d} size={size:<10d} -> {dst}')


if __name__ == '__main__':
    main()
