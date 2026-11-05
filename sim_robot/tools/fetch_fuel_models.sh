#!/usr/bin/env bash
# Download the Gazebo Fuel models used by the warehouse world into models/fuel/.
#
# The models are licensed by their authors under CC BY-NC 4.0 / CC BY-NC-ND 4.0 (see models/fuel/README.md),
# so they are not stored in this repository. Each download is pinned to a Fuel version and checked
# against a SHA-256 hash, so every machine gets exactly the files the experiments were run with.
#
# Usage: bash tools/fetch_fuel_models.sh [--proxy http://host:port] [--force]
#   --proxy  HTTP(S) proxy for curl (curl also honours https_proxy / HTTPS_PROXY)
#   --force  download again even if a model folder already exists
# After downloading, rebuild the package so the models are installed: colcon build --packages-select sim_robot
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
DEST="$HERE/models/fuel"
BASE=${FUEL_BASE:-https://fuel.gazebosim.org/1.0}   # FUEL_BASE: a mirror with the same layout
PROXY=()
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --proxy) PROXY=(-x "$2"); shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

# owner | kind | Fuel name | version | SHA-256 of the zip | target in models/fuel
ITEMS=(
    "OpenRobotics|models|Warehouse|1|74b3008d5a4a6c8797da10b21a86779b2638182307d5c9e6a27a04ff6eddd024|Warehouse"
    "MovAi|models|shelf|1|a33a204af036b5f389af11a1c514aa76fc368750c6dd73868bd0500a9813e679|shelf"
    "MovAi|models|shelf_big|1|d27d7fe1cc7a2886e44cf3d2bbe4190a619153f3263ca04969fe60ddf7a5ff92|shelf_big"
    "MovAi|models|pallet_box_mobile|1|eb70aee8c7c286c7328df5fb4593f1063690cfdc96d35ad077b1170eb45b1854|pallet_box_mobile"
    "MovAi|models|pallet|1|11cb3ae6ed5fb45f52c9274bef34503d1139c38504e5cb04fab77a3748ad75d6|pallet"
    "MovAi|models|cart_model_2|1|a3b1ec6f453703f0fcb80a967af2fa1223a6f33495e738d0ac6bdba6a67e2819|cart_model_2"
    # Only needed to regenerate worlds/warehouse.sdf with tools/make_warehouse_world.py
    "OpenRobotics|worlds|Tugbot in Warehouse|1|fb983046eb27affa81ea9a51fded62c189a4ee57c1da555ffc905224fb8c7047|tugbot_warehouse_orig.sdf"
)

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$DEST"

for item in "${ITEMS[@]}"; do
    IFS='|' read -r owner kind name version sha target <<<"$item"
    if [ -e "$DEST/$target" ] && [ "$FORCE" -eq 0 ]; then
        echo "have   $target"
        continue
    fi
    enc=${name// /%20}
    zip="$TMP/$target.zip"
    echo "fetch  $owner/$name (v$version)"
    curl -fsSL --retry 3 -m 600 "${PROXY[@]}" -o "$zip" "$BASE/$owner/$kind/$enc/$version/$enc.zip"
    got=$(sha256sum "$zip" | cut -d' ' -f1)
    if [ "$got" != "$sha" ]; then
        echo "FAIL   $target: SHA-256 mismatch (got $got)" >&2
        exit 1
    fi
    if [ "$kind" = models ]; then
        rm -rf "$DEST/$target"
        python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$zip" "$DEST/$target"
    else
        # A world zip holds one .sdf plus thumbnails; keep only the .sdf under the target name
        python3 - "$zip" "$DEST/$target" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
sdf = [n for n in z.namelist() if n.endswith('.sdf')]
if len(sdf) != 1:
    sys.exit(f'expected one .sdf in the world zip, found {sdf}')
with open(sys.argv[2], 'wb') as f:
    f.write(z.read(sdf[0]))
PY
    fi
    echo "ok     $target"
done
echo "Models are in $DEST. Rebuild to install them: colcon build --packages-select sim_robot"
