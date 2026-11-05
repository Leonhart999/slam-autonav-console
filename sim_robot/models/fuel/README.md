# Warehouse models from Gazebo Fuel

The warehouse world uses these models from [Gazebo Fuel](https://app.gazebosim.org/fuel). Their licenses
do not allow redistribution in this repository, so they are downloaded on each machine:

```bash
bash tools/fetch_fuel_models.sh            # add --proxy http://host:port if Fuel is slow or blocked
colcon build --packages-select sim_robot   # install them into share/sim_robot/models/fuel
```

The script pins each model to a Fuel version and checks its SHA-256 hash.

| Model | Author | License |
|---|---|---|
| Warehouse | OpenRobotics | CC BY-NC-ND 4.0 |
| shelf, shelf_big | MovAi | CC BY-NC 4.0 |
| pallet_box_mobile, pallet, cart_model_2 | MovAi | CC BY-NC-ND 4.0 |

Non-commercial academic use only. Do not modify the files in these folders. Cite Gazebo Fuel
(https://app.gazebosim.org/fuel) when you use them.

The world layout comes from OpenRobotics' "Tugbot in Warehouse" (`tugbot_warehouse_orig.sdf`, also
downloaded by the script). It is only needed to regenerate `worlds/warehouse.sdf` with
`tools/make_warehouse_world.py`.

---

只用于非商业的学术研究；不要修改这些目录里的文件；论文中注明出处（Gazebo Fuel）。模型不放进仓库，每台电脑用上面的脚本下载。
