# Offline files for the Docker build

Optional. If the machine that runs `docker compose build` cannot reach github.com or fuel.gazebosim.org quickly,
download the files below on another machine (or through a proxy) and put them here. See "Offline build" in
`docker/README.md` for the details.

- The five files that `robot_console/install/install.sh` fetches from GitHub, under the names the installer expects
  (the SHAs are in `robot_console/install/versions.env`; the file list and URLs are in the main README, section
  "Without GitHub access"). The build copies every `*.tar.gz` and `*.deb` file in this folder into the installer's
  download cache, so `install.sh` finds them there and downloads nothing.
- The Gazebo Fuel zip files listed in `sim_robot/tools/fetch_fuel_models.sh`, in a `fuel/` subfolder with the layout
  of the Fuel server: `fuel/<owner>/<kind>/<name>/<version>/<name>.zip`. If `fuel/` exists, the build reads the models
  from it instead of downloading them.

To use another folder, set `PREFETCH_DIR` before building.

The files are not committed to the repository (see `.gitignore`).
