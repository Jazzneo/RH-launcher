#!/usr/bin/env python3
"""Wine compatibility fix: make DBC database worker threads initialize COM.

Problem (see agent.MD): the DBC's DB worker threads (thread proc 0x140002750 ->
worker loop 0x140002780) never call CoInitialize. Under Wine there is no MTA
in the process, so their first ADO/COM call fails with
"err:ole:com_get_class_object apartment not initialised" and every game-DB
request (character list, guild load, ...) is silently dropped.

Fix: a 23-byte helper in unused space at the end of .text:
    push rcx ; sub rsp,20h ; xor ecx,ecx ; call [CoInitialize] ;
    add rsp,20h ; pop rcx ; jmp 0x140002780
and the thread proc's 'call 0x140002780' (at 0x140002761, inside its original
__try retry block) is redirected to the helper. .text VirtualSize is raised to
cover the helper (same page count, no layout change). Every original byte is
verified first. Originals are kept in runtime/linux/server/_original_exe/.

  patch-dbc-com.py status | apply | restore
"""
from pathlib import Path
import hashlib, shutil, struct, sys

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / 'runtime/linux/server'
BACKUP = SERVER / '_original_exe'
EXES = ['DBCServer_Release_x64.exe', 'DBCServer_LOG_Release_x64.exe']
CALL_SITE = 0x140002761
LOOP = 0x140002780
EXPECT = {  # address: original bytes
    0x140002750: bytes.fromhex('48894c2408' '4883ec38' '0f1f8000000000' '90' 'e81a000000'),
}


class PE:
    def __init__(self, data):
        self.d = bytearray(data)
        pe = struct.unpack_from('<I', self.d, 0x3c)[0]
        self.opt = pe + 24
        self.base = struct.unpack_from('<Q', self.d, self.opt + 24)[0]
        nsec = struct.unpack_from('<H', self.d, pe + 6)[0]
        so = self.opt + struct.unpack_from('<H', self.d, pe + 20)[0]
        self.sec = []
        for i in range(nsec):
            h = so + 40 * i
            name, vs, va, rs, ra = struct.unpack_from('<8sIIII', self.d, h)
            self.sec.append(dict(name=name.rstrip(b'\0'), vs=vs, va=va, rs=rs, ra=ra, hdr=h))
        self.text = next(s for s in self.sec if s['name'] == b'.text')

    def off(self, addr):
        rva = addr - self.base
        for s in self.sec:
            if s['va'] <= rva < s['va'] + max(s['vs'], s['rs']):
                return rva - s['va'] + s['ra']
        raise ValueError(hex(addr))

    def iat(self, dll, func):
        imp = struct.unpack_from('<I', self.d, self.opt + 112 + 8)[0]
        i = self.off(self.base + imp)
        while True:
            ilt, ts, fc, name, iat = struct.unpack_from('<IIIII', self.d, i)
            if not name:
                break
            dn = bytes(self.d[self.off(self.base + name):]).split(b'\0')[0].lower()
            j = 0
            while True:
                e = struct.unpack_from('<Q', self.d, self.off(self.base + (ilt or iat)) + 8 * j)[0]
                if not e:
                    break
                if not e >> 63:
                    fn = bytes(self.d[self.off(self.base + (e & 0x7fffffff)) + 2:]).split(b'\0')[0]
                    if dn == dll and fn == func:
                        return self.base + iat + 8 * j
                j += 1
            i += 20
        raise LookupError(func)


def plan(pe):
    t = pe.text
    cave = pe.base + t['va'] + ((t['vs'] + 15) & ~15)
    co = pe.iat(b'ole32.dll', b'CoInitialize')
    code = bytearray()
    code += b'\x51'                              # push rcx
    code += b'\x48\x83\xec\x20'                  # sub rsp,0x20
    code += b'\x31\xc9'                          # xor ecx,ecx
    nxt = cave + len(code) + 6
    code += b'\xff\x15' + struct.pack('<i', co - nxt)   # call [CoInitialize]
    code += b'\x48\x83\xc4\x20'                  # add rsp,0x20
    code += b'\x59'                              # pop rcx
    nxt = cave + len(code) + 5
    code += b'\xe9' + struct.pack('<i', LOOP - nxt)     # jmp worker loop
    new_call = b'\xe8' + struct.pack('<i', cave - (CALL_SITE + 5))
    end_rva = cave - pe.base + len(code)
    if end_rva > t['va'] + t['rs']:
        raise SystemExit('not enough unused space at end of .text')
    return cave, code, new_call, end_rva - t['va']


def state(pe):
    o = pe.off(0x140002750)
    cur = bytes(pe.d[o:o + 22])
    if cur == EXPECT[0x140002750]:
        return 'original'
    cave, code, new_call, _ = plan_from_patched(pe)
    return 'patched' if cave else 'unknown'


def plan_from_patched(pe):
    o = pe.off(CALL_SITE)
    if pe.d[o] != 0xe8:
        return None, None, None, None
    tgt = CALL_SITE + 5 + struct.unpack_from('<i', pe.d, o + 1)[0]
    co = pe.d[pe.off(tgt):pe.off(tgt) + 7]
    return (tgt, co, None, None) if co[:5] == b'\x51\x48\x83\xec\x20' else (None, None, None, None)


def apply(path):
    pe = PE(path.read_bytes())
    o = pe.off(0x140002750)
    if bytes(pe.d[o:o + 22]) != EXPECT[0x140002750]:
        if plan_from_patched(pe)[0]:
            print(f'{path.name}: already patched'); return
        raise SystemExit(f'{path.name}: unexpected bytes at 0x140002750 - not patching')
    cave, code, new_call, new_vs = plan(pe)
    co = pe.off(cave)
    if any(pe.d[co:co + len(code)]):
        raise SystemExit(f'{path.name}: space at {hex(cave)} is not empty - not patching')
    BACKUP.mkdir(exist_ok=True)
    bk = BACKUP / path.name
    if not bk.exists():
        shutil.copy2(path, bk)
    pe.d[co:co + len(code)] = code
    pe.d[pe.off(CALL_SITE):pe.off(CALL_SITE) + 5] = new_call
    struct.pack_into('<I', pe.d, pe.text['hdr'] + 8, max(pe.text['vs'], new_vs))
    tmp = path.with_suffix('.exe.tmp'); tmp.write_bytes(pe.d); tmp.replace(path)
    print(f'{path.name}: patched (helper at {hex(cave)}, {len(code)} bytes; backup {bk})')


def restore(path):
    bk = BACKUP / path.name
    if not bk.exists():
        print(f'{path.name}: no backup, nothing to restore'); return
    shutil.copy2(bk, path); print(f'{path.name}: restored original')


def main():
    act = (sys.argv[1:] or ['status'])[0]
    for exe in EXES:
        p = SERVER / exe
        if act == 'apply':
            apply(p)
        elif act == 'restore':
            restore(p)
        else:
            pe = PE(p.read_bytes())
            o = pe.off(0x140002750)
            s = 'original' if bytes(pe.d[o:o + 22]) == EXPECT[0x140002750] else ('patched' if plan_from_patched(pe)[0] else 'UNKNOWN')
            print(f'{exe}: {s}  sha256 {hashlib.sha256(pe.d).hexdigest()[:16]}')


if __name__ == '__main__':
    main()
