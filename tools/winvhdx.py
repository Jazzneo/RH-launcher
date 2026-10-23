#!/usr/bin/env python3
"""Read files out of a Windows VHDX disk image, READ-ONLY, with nothing but the Python standard library.

Used by the RH Launcher installer to pull the Windows database drivers (ADO / OLE DB / SQL client)
out of the "RustyHearts WS" server VM disk, so players need no libguestfs, no root and no Windows.

    winvhdx.py ls      DISK.vhdx '/Program Files/Common Files/System/ado'
    winvhdx.py drivers DISK.vhdx OUT_ROOT     # -> OUT_ROOT/runtime/linux/windows-{components,dlls}

Supports: dynamic/fixed VHDX (no parent disk), GPT or MBR, NTFS (plain, non-compressed files).
"""
import io, os, struct, sys, tarfile, uuid

# ----------------------------------------------------------------------------------------- VHDX
BAT_GUID = uuid.UUID("2DC27766-F623-4200-9D64-115E9BFD4A08")
META_GUID = uuid.UUID("8B7CA206-4790-4B9A-B8FE-575F050F886E")
FILE_PARAMS = uuid.UUID("CAA16737-FA36-4D43-B3B6-33F0AA44E76B")
DISK_SIZE = uuid.UUID("2FA54224-CD1B-4876-B211-5DBED83BF4B8")
LOGICAL_SECTOR = uuid.UUID("8141BF1D-A96F-4709-BA47-F233A8FAAB5F")
MB = 1 << 20


class DiskError(Exception):
    pass


class Vhdx:
    def __init__(self, path):
        self.f = open(path, "rb")
        if self._at(0, 8) != b"vhdxfile":
            raise DiskError("not a VHDX file")
        heads = []
        for off in (64 * 1024, 128 * 1024):
            h = self._at(off, 80)
            if h[:4] == b"head":
                seq = struct.unpack_from("<Q", h, 8)[0]
                heads.append((seq, h))
        if not heads:
            raise DiskError("VHDX has no valid header")
        h = max(heads)[1]
        self.log_pending = uuid.UUID(bytes_le=h[48:64]).int != 0
        regions = {}
        rt = self._at(192 * 1024, 64 * 1024)
        if rt[:4] != b"regi":
            rt = self._at(256 * 1024, 64 * 1024)
        if rt[:4] != b"regi":
            raise DiskError("VHDX region table missing")
        for i in range(struct.unpack_from("<I", rt, 8)[0]):
            e = rt[16 + i * 32: 48 + i * 32]
            regions[uuid.UUID(bytes_le=e[:16])] = struct.unpack_from("<QI", e, 16)
        meta_off, meta_len = regions[META_GUID]
        mh = self._at(meta_off, meta_len)
        if mh[:8] != b"metadata":
            raise DiskError("VHDX metadata missing")
        items = {}
        for i in range(struct.unpack_from("<H", mh, 10)[0]):
            e = mh[32 + i * 32: 64 + i * 32]
            o, n = struct.unpack_from("<II", e, 16)
            items[uuid.UUID(bytes_le=e[:16])] = mh[o:o + n]
        self.block_size, flags = struct.unpack("<II", items[FILE_PARAMS][:8])
        if flags & 2:
            raise DiskError("this is a differencing disk (it needs its parent)")
        self.size = struct.unpack("<Q", items[DISK_SIZE][:8])[0]
        self.sector = struct.unpack("<I", items[LOGICAL_SECTOR][:4])[0]
        self.chunk_ratio = ((1 << 23) * self.sector) // self.block_size
        bat_off, bat_len = regions[BAT_GUID]
        self.bat = self._at(bat_off, bat_len)

    def _at(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    def _block(self, idx):
        e = struct.unpack_from("<Q", self.bat, (idx + idx // self.chunk_ratio) * 8)[0]
        state, off = e & 7, (e >> 20) * MB
        return off if state in (6, 7) and off else None   # 6 = fully present (7 = partial: diff only)

    def read(self, off, n):
        out = bytearray()
        while n > 0:
            b, inner = divmod(off, self.block_size)
            take = min(n, self.block_size - inner)
            fo = self._block(b)
            if fo is None:
                out += bytes(take)
            else:
                chunk = self._at(fo + inner, take)
                out += chunk + bytes(take - len(chunk))
            off += take
            n -= take
        return bytes(out)


def partitions(disk):
    """[(offset, length)] of the data partitions, largest first."""
    mbr = disk.read(0, 512)
    parts = []
    gpt = disk.read(disk.sector, 92)
    if gpt[:8] == b"EFI PART":
        lba, count, esz = struct.unpack_from("<QII", gpt, 72)
        tab = disk.read(lba * disk.sector, count * esz)
        for i in range(count):
            e = tab[i * esz:(i + 1) * esz]
            if e[:16] == bytes(16):
                continue
            first, last = struct.unpack_from("<QQ", e, 32)
            parts.append((first * disk.sector, (last - first + 1) * disk.sector))
    elif mbr[510:512] == b"\x55\xaa":
        for i in range(4):
            e = mbr[446 + i * 16: 462 + i * 16]
            if e[4] in (0x07,):
                first, cnt = struct.unpack_from("<II", e, 8)
                parts.append((first * 512, cnt * 512))
    return sorted(parts, key=lambda p: -p[1])


# ----------------------------------------------------------------------------------------- NTFS
class Ntfs:
    def __init__(self, disk, offset):
        self.d, self.base = disk, offset
        bs = disk.read(offset, 512)
        if bs[3:11] != b"NTFS    ":
            raise DiskError("no NTFS volume here")
        self.bps = struct.unpack_from("<H", bs, 0x0B)[0]
        spc = bs[0x0D]
        spc = 1 << (256 - spc) if spc > 0x80 else spc
        self.cluster = self.bps * spc
        mft_lcn = struct.unpack_from("<Q", bs, 0x30)[0]
        c = struct.unpack_from("<b", bs, 0x40)[0]
        self.rec_size = (1 << -c) if c < 0 else c * self.cluster
        rec0 = self._fixup(self._raw(mft_lcn * self.cluster, self.rec_size))
        self.mft_runs = None
        self.mft_runs = self._data_runs(0, rec0)[0]

    def _raw(self, off, n):
        return self.d.read(self.base + off, n)

    def _fixup(self, rec):
        if rec[:4] != b"FILE":
            return None
        rec = bytearray(rec)
        uo, uc = struct.unpack_from("<HH", rec, 4)
        usn = rec[uo:uo + 2]
        for i in range(1, uc):
            p = i * self.bps - 2
            if p + 2 > len(rec):
                break
            if rec[p:p + 2] != usn:
                return None
            rec[p:p + 2] = rec[uo + i * 2: uo + i * 2 + 2]
        return bytes(rec)

    @staticmethod
    def _runs(buf):
        runs, lcn, i = [], 0, 0
        while i < len(buf) and buf[i]:
            h = buf[i]; ln, ofs = h & 15, h >> 4
            length = int.from_bytes(buf[i + 1:i + 1 + ln], "little")
            if ofs:
                lcn += int.from_bytes(buf[i + 1 + ln:i + 1 + ln + ofs], "little", signed=True)
                runs.append((lcn, length))
            else:
                runs.append((None, length))       # sparse
            i += 1 + ln + ofs
        return runs

    @staticmethod
    def attrs(rec):
        off = struct.unpack_from("<H", rec, 0x14)[0]
        while off + 8 <= len(rec):
            t, ln = struct.unpack_from("<II", rec, off)
            if t == 0xFFFFFFFF or ln == 0:
                break
            a = rec[off:off + ln]
            nl, no, fl = a[9], struct.unpack_from("<H", a, 10)[0], struct.unpack_from("<H", a, 12)[0]
            name = a[no:no + nl * 2].decode("utf-16-le", "replace")
            yield t, name, a, fl
            off += ln

    def _data_runs(self, num, rec, stream=""):
        """Return (runs, real_size, flags) for the $DATA attribute, following $ATTRIBUTE_LIST."""
        pieces, size, flags = [], None, 0
        recs = [rec]
        for t, _, a, _ in self.attrs(rec):
            if t == 0x20:                                  # attribute list
                data = self._value(a)
                i = 0
                while i + 26 <= len(data):
                    at, ln = struct.unpack_from("<IH", data, i)
                    ref = struct.unpack_from("<Q", data, i + 16)[0] & 0xFFFFFFFFFFFF
                    if at == 0x80 and ref != num and self.mft_runs is not None:
                        r = self.record(ref)
                        if r and r not in recs:
                            recs.append(r)
                    if ln == 0:
                        break
                    i += ln
        for r in recs:
            for t, name, a, fl in self.attrs(r):
                if t != 0x80 or name != stream:
                    continue
                flags |= fl
                if a[8] == 0:
                    vl, vo = struct.unpack_from("<IH", a, 0x10)
                    return ("resident", a[vo:vo + vl]), vl, fl
                start = struct.unpack_from("<Q", a, 0x10)[0]
                if start == 0:
                    size = struct.unpack_from("<Q", a, 0x30)[0]
                pieces.append((start, self._runs(a[struct.unpack_from("<H", a, 0x20)[0]:])))
        if not pieces:
            return None, 0, 0
        pieces.sort()
        runs = [r for _, rs in pieces for r in rs]
        return runs, size, flags

    def _value(self, a):
        if a[8] == 0:
            vl, vo = struct.unpack_from("<IH", a, 0x10)
            return a[vo:vo + vl]
        size = struct.unpack_from("<Q", a, 0x30)[0]
        return self._read_runs(self._runs(a[struct.unpack_from("<H", a, 0x20)[0]:]), size)

    def _read_runs(self, runs, size, start=0, n=None):
        if n is None:
            n = size - start
        out = bytearray()
        pos = 0
        for lcn, ln in runs:
            rlen = ln * self.cluster
            if pos + rlen > start and n > 0:
                s = max(0, start - pos)
                take = min(rlen - s, n)
                out += bytes(take) if lcn is None else self._raw(lcn * self.cluster + s, take)
                start += take; n -= take
            pos += rlen
            if n <= 0:
                break
        return bytes(out[:size])

    @property
    def record_count(self):
        return sum(ln for _, ln in self.mft_runs) * self.cluster // self.rec_size

    def record(self, num):
        return self._fixup(self._read_runs(self.mft_runs, (num + 1) * self.rec_size,
                                           num * self.rec_size, self.rec_size))

    def scan(self):
        """Yield (num, flags, [(parent, name)], mtime) for every in-use base record."""
        per = max(1, (4 * MB) // self.rec_size)
        total = self.record_count
        for first in range(0, total, per):
            cnt = min(per, total - first)
            blob = self._read_runs(self.mft_runs, (first + cnt) * self.rec_size, first * self.rec_size,
                                   cnt * self.rec_size)
            for k in range(cnt):
                rec = self._fixup(blob[k * self.rec_size:(k + 1) * self.rec_size])
                if not rec:
                    continue
                flags, = struct.unpack_from("<H", rec, 0x16)
                if not flags & 1 or struct.unpack_from("<Q", rec, 0x20)[0] & 0xFFFFFFFFFFFF:
                    continue
                names, mtime = [], 0
                for t, _, a, _ in self.attrs(rec):
                    if t == 0x10 and a[8] == 0:
                        mtime = struct.unpack_from("<Q", self._value(a), 8)[0]
                    elif t == 0x30 and a[8] == 0:
                        v = self._value(a)
                        if v[0x41] == 2:              # DOS 8.3 alias
                            continue
                        par = struct.unpack_from("<Q", v, 0)[0] & 0xFFFFFFFFFFFF
                        names.append((par, v[0x42:0x42 + v[0x40] * 2].decode("utf-16-le", "replace")))
                yield first + k, flags, names, mtime

    def read_file(self, num):
        rec = self.record(num)
        for t, _, a, _ in self.attrs(rec):
            if t == 0xC0:
                raise DiskError("file is a reparse point (Windows-compressed?) - not supported")
        runs, size, fl = self._data_runs(num, rec)
        if runs is None:
            return b""
        if isinstance(runs, tuple):
            return runs[1]
        if fl & 0x4001:
            raise DiskError("file is NTFS-compressed or encrypted - not supported")
        return self._read_runs(runs, size)


class Volume:
    """The Windows volume of a VHDX, with a path index built from one pass over the MFT."""

    def __init__(self, path):
        self.disk = Vhdx(path)
        self.fs = None
        for off, _ in partitions(self.disk):
            try:
                fs = Ntfs(self.disk, off)
            except DiskError:
                continue
            self.fs = fs
            if self._index_and_check():
                break
        if not self.fs or not self.children:
            raise DiskError("no Windows (NTFS) volume found on this disk")

    def _index_and_check(self):
        self.children, self.info = {}, {}
        for num, flags, names, mtime in self.fs.scan():
            self.info[num] = (bool(flags & 2), mtime)
            for par, name in names:
                if num != 5:
                    self.children.setdefault(par, {})[name.lower()] = (name, num)
        return "windows" in self.children.get(5, {})

    def lookup(self, path):
        num = 5                                         # root directory
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            hit = self.children.get(num, {}).get(part.lower())
            if not hit:
                return None
            num = hit[1]
        return num

    def listdir(self, num):
        return sorted(self.children.get(num, {}).values(), key=lambda x: x[0].lower())

    def is_dir(self, num):
        return self.info.get(num, (False, 0))[0]

    def mtime(self, num):
        ft = self.info.get(num, (False, 0))[1]
        return max(0, ft // 10_000_000 - 11644473600) if ft else 0

    def read(self, num):
        return self.fs.read_file(num)

    def tar_dir(self, num, dest):
        """Write a tar of a folder's contents (./...) like `guestfish tar-out`."""
        with tarfile.open(dest, "w", format=tarfile.GNU_FORMAT) as tf:
            def add(n, rel):
                ti = tarfile.TarInfo(rel + ("/" if self.is_dir(n) else ""))
                ti.mtime, ti.uname, ti.gname = self.mtime(n), "root", "root"
                if self.is_dir(n):
                    ti.type, ti.mode = tarfile.DIRTYPE, 0o777
                    tf.addfile(ti)
                    for name, child in self.listdir(n):
                        add(child, f"{rel}/{name}")
                else:
                    data = self.read(n)
                    ti.size, ti.mode = len(data), 0o777
                    tf.addfile(ti, io.BytesIO(data))
            add(num, ".")


# -------------------------------------------------------------------------------------- drivers
DIRS = {"ado": "/Program Files/Common Files/System/ado",
        "msadc": "/Program Files/Common Files/System/msadc",
        "oledb": "/Program Files/Common Files/System/Ole DB"}
FROM = {"msado15.dll": "ado", "msadrh15.dll": "ado", "msader15.dll": "ado", "msado28.tlb": "ado",
        "msado60.tlb": "ado", "msadce.dll": "msadc", "msdaps.dll": "oledb", "msdatl3.dll": "oledb",
        "oledb32.dll": "oledb", "oledb32r.dll": "oledb", "sqloledb.dll": "oledb", "sqloledb.rll": "oledb",
        "dbnetlib.dll": "sys", "dpapi.dll": "sys", "msdadiag.dll": "sys", "msdart.dll": "sys",
        "msvcrt.dll": "sys", "sqlsrv32.dll": "sys"}


def extract_drivers(disk_path, root, say=print):
    say("Reading the Windows disk (read-only)...")
    vol = Volume(disk_path)
    if vol.disk.log_pending:
        say("note: the disk was not shut down cleanly; reading it as-is")
    comp = os.path.join(root, "runtime", "linux", "windows-components")
    dlls = os.path.join(root, "runtime", "linux", "windows-dlls")
    found = {k: vol.lookup(p) for k, p in DIRS.items()}
    found["sys"] = vol.lookup("/Windows/System32")
    missing = [k for k, v in found.items() if v is None]
    if missing:
        raise DiskError("Windows folders not found on that disk: " + ", ".join(missing))
    files = {}
    for f, src in FROM.items():
        hit = vol.children.get(found[src], {}).get(f)
        if not hit:
            raise DiskError(f"{f} is missing from that disk - is it the RustyHearts server VM?")
        files[f] = vol.read(hit[1])
    os.makedirs(comp, exist_ok=True)
    os.makedirs(dlls, exist_ok=True)
    for k in ("ado", "msadc", "oledb"):
        say(f"Copying the {DIRS[k].rsplit('/', 1)[1]} folder...")
        tmp = os.path.join(comp, f".{k}.tar.part")
        vol.tar_dir(found[k], tmp)
        os.replace(tmp, os.path.join(comp, f"{k}.tar"))
    for f, data in files.items():
        with open(os.path.join(dlls, f), "wb") as fh:
            fh.write(data)
    say(f"Done: 3 folders and {len(files)} driver files installed.")


def main(argv):
    if len(argv) >= 3 and argv[0] == "ls":
        vol = Volume(argv[1])
        n = vol.lookup(argv[2])
        if n is None:
            print("not found"); return 1
        for name, child in vol.listdir(n):
            print(("D " if vol.is_dir(child) else "F ") + name)
        return 0
    if len(argv) >= 3 and argv[0] == "drivers":
        try:
            extract_drivers(argv[1], argv[2])
        except (DiskError, OSError, KeyError) as e:
            print(f"error: {e}", file=sys.stderr); return 1
        return 0
    print(__doc__.strip()); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
