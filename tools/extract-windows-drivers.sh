#!/usr/bin/env bash
# Extract the Windows database-driver files the game servers need (ADO / OLE DB / SQL client)
# from the "RustyHearts WS" Hyper-V server VM disk (server-vm.7z on RaGEZONE). The disk is opened
# READ-ONLY with libguestfs; nothing in it is changed.
#
#   tools/extract-windows-drivers.sh "/path/to/RustyHearts WS/Virtual Hard Disks/<disk>.vhdx"
#
# Needs libguestfs (guestfish). Bazzite / Fedora Atomic: run it inside a toolbox:
#   toolbox create && toolbox enter && sudo dnf install -y libguestfs-tools
# Debian/Ubuntu/SteamOS-distrobox: sudo apt install libguestfs-tools
#
# Output (used by docker/compose.yaml):
#   runtime/linux/windows-components/{ado,msadc,oledb}.tar   full copies of those Windows folders
#   runtime/linux/windows-dlls/*                               the 18 individual files
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DISK="${1:-}"
[[ -n "$DISK" && -f "$DISK" ]] || { echo "Usage: $0 /path/to/RustyHearts-server-disk.vhdx" >&2; exit 1; }
GF="${GUESTFISH:-guestfish}"
command -v "$GF" >/dev/null || { echo "guestfish not found: install libguestfs-tools (see the top of this script)." >&2; exit 1; }
export LIBGUESTFS_BACKEND="${LIBGUESTFS_BACKEND:-direct}"

COMP="$ROOT/runtime/linux/windows-components"; DLLS="$ROOT/runtime/linux/windows-dlls"
mkdir -p "$COMP" "$DLLS"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

echo "Opening the VM disk read-only (this takes a little while)..."
# Windows paths are case-insensitive; find the real spelling on the NTFS volume first.
SYSFILES=(dbnetlib.dll dpapi.dll msdadiag.dll msdart.dll msvcrt.dll sqlsrv32.dll)
look=(echo "D sys" : case-sensitive-path "/Windows/System32"
      : echo "D ado" : case-sensitive-path "/Program Files/Common Files/System/ado"
      : echo "D msadc" : case-sensitive-path "/Program Files/Common Files/System/msadc"
      : echo "D oledb" : case-sensitive-path "/Program Files/Common Files/System/Ole DB")
for f in "${SYSFILES[@]}"; do
  look+=(: echo "F $f" : -case-sensitive-path "/Windows/System32/$f")   # '-' = keep going if missing
done
out="$("$GF" --ro -a "$DISK" -i "${look[@]}")" || {
  echo "Could not find the Windows folders on that disk (is it the RustyHearts server VM?)." >&2; exit 1; }
declare -A DIR=() SYSPATH=()
key=
while IFS= read -r line; do
  case "$line" in
    "D "*|"F "*) key="$line" ;;
    /*) [[ "$key" == "D "* ]] && DIR[${key#D }]="$line"; [[ "$key" == "F "* ]] && SYSPATH[${key#F }]="$line"; key= ;;
  esac
done <<< "$out"
for d in sys ado msadc oledb; do
  [[ -n "${DIR[$d]:-}" ]] || { echo "Folder for '$d' not found on that disk." >&2; exit 1; }
done

mkdir -p "$work/sys"
cmds=(tar-out "${DIR[ado]}" "$work/ado.tar" : tar-out "${DIR[msadc]}" "$work/msadc.tar"
      : tar-out "${DIR[oledb]}" "$work/oledb.tar")
for f in "${SYSFILES[@]}"; do
  if [[ -n "${SYSPATH[$f]:-}" ]]; then
    cmds+=(: copy-out "${SYSPATH[$f]}" "$work/sys")
  fi
done
echo "Copying the driver folders and files..."
"$GF" --ro -a "$DISK" -i "${cmds[@]}"

for n in ado msadc oledb; do
  [[ -s "$work/$n.tar" ]] || { echo "Extraction of $n failed." >&2; exit 1; }
  mkdir -p "$work/x/$n"; tar -xf "$work/$n.tar" -C "$work/x/$n"
done

# Which file comes from where (matches the working setup).
declare -A FROM=(
  [msado15.dll]=ado [msadrh15.dll]=ado [msader15.dll]=ado [msado28.tlb]=ado [msado60.tlb]=ado
  [msadce.dll]=msadc
  [msdaps.dll]=oledb [msdatl3.dll]=oledb [oledb32.dll]=oledb [oledb32r.dll]=oledb [sqloledb.dll]=oledb [sqloledb.rll]=oledb
  [dbnetlib.dll]=sys [dpapi.dll]=sys [msdadiag.dll]=sys [msdart.dll]=sys [msvcrt.dll]=sys [sqlsrv32.dll]=sys
)
missing=()
for f in "${!FROM[@]}"; do
  src="${FROM[$f]}"; dir="$work/x/$src"; [[ "$src" == sys ]] && dir="$work/sys"
  hit="$(find "$dir" -maxdepth 1 -type f -iname "$f" | head -n1 || true)"
  if [[ -n "$hit" ]]; then cp -f "$hit" "$DLLS/$f"; else missing+=("$f"); fi
done
if (( ${#missing[@]} )); then
  echo "Missing from the disk: ${missing[*]}" >&2
  echo "Nothing was installed into windows-components; check that this is the RustyHearts server VM disk." >&2
  exit 1
fi
cp -f "$work/ado.tar" "$work/msadc.tar" "$work/oledb.tar" "$COMP/"
echo "Done: 3 folders -> $COMP"
echo "      18 files  -> $DLLS"
