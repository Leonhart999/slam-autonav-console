#!/usr/bin/env bash
# Build the HKUST 4F west building model (models/bim_hkust_4f_west) from the SLABIM dataset.
#
# The model is derived from the HKUST main building BIM of the SLABIM dataset (Huang et al., ICRA 2025,
# https://github.com/HKUST-Aerial-Robotics/SLABIM). The dataset states no licence for its BIM files, so the
# model is not shipped: download SLABIM yourself (BIM/4F/mesh/{walls,columns}.ply is enough) and run
#   bash tools/make_hkust_models.sh /path/to/SLABIM        (or set SLABIM_DIR)
#   colcon build --packages-select sim_robot
# The world file worlds/hkust_4f_west.sdf is shipped; only the building mesh is generated. The result is
# checked against the SHA-256 of the mesh used in the thesis experiments.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SLABIM=${1:-${SLABIM_DIR:-}}
[ -n "$SLABIM" ] || { sed -n '2,11p' "$0"; exit 2; }
for f in walls columns; do
    [ -f "$SLABIM/BIM/4F/mesh/$f.ply" ] || { echo "missing $SLABIM/BIM/4F/mesh/$f.ply" >&2; exit 1; }
done
# Region and start pose of the thesis scene (BIM coordinates); see worlds/hkust_4f_west.sdf
python3 -I "$HERE/make_bim_world.py" --slabim "$SLABIM" --storey 4F --box -32 22 -22 34 \
    --name hkust_4f_west --start 0 23.3 0 --model-only
want=7ed4de1f55227e527e6e40721563d5860e7525ddbab1401903122c462845e275
got=$(sha256sum "$HERE/../models/bim_hkust_4f_west/meshes/building.stl" | cut -d' ' -f1)
if [ "$got" = "$want" ]; then
    echo "ok     building.stl matches the thesis model"
else
    echo "WARN   building.stl differs from the thesis model (other SLABIM version?): $got"
fi
echo "Rebuild to install it: colcon build --packages-select sim_robot"
