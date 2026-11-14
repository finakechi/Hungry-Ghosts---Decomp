#!/usr/bin/env python3
"""Read-only PCSX2 v2.8.2 PINE client. Implements no mutation opcodes."""
import argparse
import hashlib
import json
import socket
import struct
from datetime import datetime, timezone
from pathlib import Path


class Pine:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(str(path))

    def close(self):
        self.sock.close()

    def _receive(self, count):
        result = bytearray()
        while len(result) < count:
            part = self.sock.recv(count - len(result))
            if not part:
                raise ValueError('PINE closed connection')
            result.extend(part)
        return bytes(result)

    def request(self, payload):
        self.sock.sendall(struct.pack('<I', 4 + len(payload)) + payload)
        count = struct.unpack('<I', self._receive(4))[0]
        if not 5 <= count <= 450000:
            raise ValueError('invalid PINE response length')
        result = self._receive(count - 4)
        if result[0] != 0:
            raise ValueError('PINE error response')
        return result[1:]

    def metadata(self):
        result = {}
        for key, opcode in [('version', 8), ('title', 11), ('serial', 12), ('uuid', 13)]:
            data = self.request(bytes([opcode]))
            if len(data) < 5 or struct.unpack_from('<I', data)[0] != len(data)-4 or data[-1] != 0:
                raise ValueError('invalid PINE string')
            result[key] = data[4:-1].decode('utf-8')
        result['status'] = struct.unpack('<I', self.request(b'\x0f'))[0]
        return result

    def read(self, address, size):
        if address < 0 or size < 0 or address + size > 0x02000000:
            raise ValueError('only physical EE RAM reads allowed')
        output = bytearray()
        # Read8 at the tail avoids crossing a caller's requested boundary.
        while size:
            width = 8 if size >= 8 else 1
            count = min(size // width, 4096)
            payload = b''.join(bytes([3 if width == 8 else 0]) + struct.pack('<I',address+i*width)
                               for i in range(count))
            data = self.request(payload)
            if len(data) != count * width:
                raise ValueError('short PINE read')
            output.extend(data)
            address += len(data)
            size -= len(data)
        return bytes(output)


def capture(path, ranges):
    pine = Pine(path)
    try:
        before = pine.metadata()
        if before['serial'] != 'SCPS-15046' or before['uuid'].lower() != '91aa4b28':
            raise ValueError('expected original Japanese runtime')
        samples = []
        for address, size in ranges:
            data = pine.read(address, size)
            samples.append(dict(address=f'0x{address:08x}', size_bytes=size,
                                sha256=hashlib.sha256(data).hexdigest(), bytes=data.hex()))
        return dict(schema='hg-runtime/v1', timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    metadata_before=before, metadata_after=pine.metadata(), samples=samples,
                    limitation='Running memory reads are sequential, not an atomic snapshot; CRC is not a full build hash.')
    finally:
        pine.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--range', nargs=2, action='append', required=True, metavar=('ADDRESS','SIZE'))
    a = p.parse_args()
    result = capture(a.socket, [(int(at,0),int(size,0)) for at,size in a.range])
    with a.output.open('x') as fp:
        json.dump(result, fp, indent=2)
        fp.write('\n')
    print(result['metadata_before'])


if __name__ == '__main__':
    main()
