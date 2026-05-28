## Prerequisites (GPU)

- NVIDIA Container Toolkit
- Docker

# Simulation & Teleop Container

## Build

From the repo root

```bash
docker build -t teleop-docker -f docker/sim/Dockerfile .
```

## Run

```bash
xhost +
export LEROBOT_CALIB=.cache/huggingface/lerobot/calibration
docker run --name teleop -it --privileged --gpus all -e "ACCEPT_EULA=Y" --rm --network=host \
   -e "PRIVACY_CONSENT=Y" \
   -e DISPLAY \
   -v /dev:/dev \
   -v /run/udev:/run/udev:ro \
   -v $HOME/.Xauthority:/root/.Xauthority \
   -v ~/docker/isaac-sim/cache/kit:/isaac-sim/kit/cache:rw \
   -v ~/docker/isaac-sim/cache/ov:/root/.cache/ov:rw \
   -v ~/docker/isaac-sim/cache/pip:/root/.cache/pip:rw \
   -v ~/docker/isaac-sim/cache/glcache:/root/.cache/nvidia/GLCache:rw \
   -v ~/docker/isaac-sim/cache/computecache:/root/.nv/ComputeCache:rw \
   -v ~/docker/isaac-sim/logs:/root/.nvidia-omniverse/logs:rw \
   -v ~/docker/isaac-sim/data:/root/.local/share/ov/data:rw \
   -v ~/docker/isaac-sim/documents:/root/Documents:rw \
   -v ~/$LEROBOT_CALIB:/root/$LEROBOT_CALIB \
   -v ./docker/env:/root/env \
   -v $(pwd)/source:/workspace/Sim-to-Real-SO-101-Workshop/source \
   -v $(pwd)/outputs:/workspace/Sim-to-Real-SO-101-Workshop/outputs \
   -v $(pwd)/datasets:/workspace/Sim-to-Real-SO-101-Workshop/datasets \
   teleop-docker:latest
```

### Calibrate 

```bash
lerobot-calibrate \
    --teleop.type=so101_leader \
    --teleop.port=$TELEOP_PORT \
    --teleop.id=TELEOP_ID
```

```bash
lerobot-calibrate \
    --robot.type=so101_follower \
    --robot.port=$ROBOT_PORT \
    --robot.id=$ROBOT_ID
```

```bash
lerobot-teleoperate \
    --robot.type=so101_follower \
    --robot.port=$ROBOT_PORT \
    --robot.id=$ROBOT_ID \
    --teleop.type=so101_leader \
    --teleop.port=$TELEOP_PORT \
    --teleop.id=$TELEOP_ID
```


# Real Robot Container

### Build the image
This will take a while!

```bash
./docker/real/build.sh <arch> (ada | blackwell)
```

We are running the image and opening it in two terminals.

### First terminal - Serving the model

```bash
export MODELS_DIR=~/models
export LEROBOT_CALIB=.cache/huggingface/lerobot/calibration
docker run -it --rm --name real-robot --network host --privileged --gpus all \
    -e DISPLAY \
    -v /dev:/dev \
    -v /run/udev:/run/udev:ro \
    -v $HOME/.Xauthority:/root/.Xauthority \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -v ~/$LEROBOT_CALIB:/root/$LEROBOT_CALIB \
    -v ./docker/env:/root/env \
    -v $MODELS_DIR:/workspace/models \
    -v $(pwd)/docker/real/scripts:/workspace/Isaac-GR00T/gr00t/eval/real_robot/SO100 \
    real-robot \
    /bin/bash

# inside the container (required: set MODEL so --model-path points at the model folder)
export MODEL=aravindhs-NV/grootn16-finetune_sreetz-so101_teleop_vials_rack_left_sim_and_real/checkpoint-10000
cd Isaac-GR00T
python3 gr00t/eval/run_gr00t_server.py \
    --model-path /workspace/models/$MODEL
```

### Second terminal - Robot evaluation rollout

```bash
# in a new terminal
docker exec  -it real-robot /bin/bash

# inside the container

###############################
# steps below assume robot is already calibrated and env vars are set
###############################

uv run python gr00t/eval/real_robot/SO100/so101_eval.py \
  --robot.type=so101_follower \
  --robot.port="$ROBOT_PORT" \
  --robot.id="$ROBOT_ID" \
  --robot.cameras="{
      wrist:  {type: opencv, index_or_path: $CAMERA_GRIPPER, width: 640, height: 480, fps: 30},
      front:  {type: opencv, index_or_path: $CAMERA_EXTERNAL, width: 640, height: 480, fps: 30}
  }" \
  --policy_host=localhost \
  --policy_port=5555 \
  --lang_instruction="Pick up the vial and place it in the yellow rack"

```

---

## Attach a shell to the running container

```bash
docker exec -it teleop bash
```

---

## Manual Robot Control (`so101_control.py`)

Set env vars before running. `FRONT_CAM_IDX` is optional — omit it to use only the wrist camera.

```bash
setenv ROBOT_PORT=/dev/ttyACM0
setenv ROBOT_ID=follower_arm_1
setenv CAMERA_GRIPPER=0
# setenv CAMERA_EXTERNAL=1  # optional
```

Run from inside the `teleop` container:

```bash
docker exec teleop bash -c "
  setenv ROBOT_PORT=/dev/ttyACM0
  setenv ROBOT_ID=follower_arm_1
  setenv CAMERA_GRIPPER=0
  cd /workspace/lerobot_so101_teleop
  /workspace/isaaclab/_isaac_sim/python.sh real_robot/so101_control.py
"
```

### Passive mode (free-drive / hand-guiding)

Disables torque so you can manually move the arm. Prints the final joint positions on exit (useful for recording poses).

```bash
docker exec teleop bash -c "
  setenv ROBOT_PORT=/dev/ttyACM0
  setenv ROBOT_ID=follower_arm_1
  setenv CAMERA_GRIPPER=0
  cd /workspace/lerobot_so101_teleop
  /workspace/isaaclab/_isaac_sim/python.sh real_robot/so101_control.py --passive_mode
"
```

### Reset to home

Moves the robot to the initial pose on start, and back to the home pose on exit (Ctrl+C).

```bash
docker exec teleop bash -c "
  setenv ROBOT_PORT=/dev/ttyACM0
  setenv ROBOT_ID=follower_arm_1
  setenv CAMERA_GRIPPER=0
  cd /workspace/lerobot_so101_teleop
  /workspace/isaaclab/_isaac_sim/python.sh real_robot/so101_control.py
"
```

To skip the Rerun visualizer:

```bash
/workspace/isaaclab/_isaac_sim/python.sh real_robot/so101_control.py --visualize false
```

---

## Keyboard Joint Control (`so101_manual_control.py`)

Interactively control individual joints via keyboard arrows. Controls:
- `LEFT` / `RIGHT` — decrease / increase current joint position
- `UP` / `DOWN` — switch between joints
- `z` — send all joints to 0
- `q` or `ESC` — quit

```bash
docker exec -it teleop bash -c "
  cd /workspace/lerobot_so101_teleop
  /workspace/isaaclab/_isaac_sim/python.sh real_robot/so101_manual_control.py \
    --robot.type=so101_follower \
    --robot.port=/dev/ttyACM0 \
    --robot.id=follower_arm_1
"
```

Optional flags:
- `--step-size 5.0` — larger steps per keypress (default: 1.0)
- `--use-degrees` — show positions in degrees instead of normalized range

---

## Calibration Quality Check (`so101_check_calibration.py`)

Verifies a calibration file against a statistical baseline derived from known-good calibrations,
and optionally reads live encoder positions to confirm the robot is within its calibrated range.

### Step 1 — Build the statistical baseline

Run once (from outside the container) after collecting several good calibration files into
`real_robot/sample_callibrations/`. Use `--exclude` to omit known-bad files:

```bash
python real_robot/so101_calibration_stats.py \
    --calib-dir real_robot/sample_callibrations \
    --output-json real_robot/calibration_stats.json \
    --output-plot real_robot/calibration_stats.png \
    --exclude follower_arm_1
```

This writes `calibration_stats.json` (mean, std, min, max of motion range per joint) and
saves a box-and-whisker plot for visual inspection.

### Step 2 — Run the calibration check

```bash
docker exec -it teleop bash -c "
  setenv ROBOT_PORT=/dev/ttyACM0
  setenv ROBOT_ID=follower_arm_1
  cd /workspace/lerobot_so101_teleop
  /workspace/isaaclab/_isaac_sim/python.sh docker/real/scripts/so101_check_calibration.py
"
```

Override the stats file location with `STATS_JSON` if needed:

```bash
docker exec -it teleop bash -c "
  setenv ROBOT_PORT=/dev/ttyACM0
  setenv ROBOT_ID=follower_arm_1
  setenv STATS_JSON=/workspace/lerobot_so101_teleop/docker/real/scripts/calibration_stats.json
  cd /workspace/lerobot_so101_teleop
  /workspace/isaaclab/_isaac_sim/python.sh docker/real/scripts/so101_check_calibration.py
"
```

The script reports:
1. **Motion range vs baseline** — each joint's `abs(range_max − range_min)` compared against
   `mean ± 2σ` from the stats file (raw encoder counts — no degree conversion)
2. **Live positions** — current encoder values vs. the calibrated range (requires robot connected)

Warnings are raised if:
- A joint's motion range deviates more than ±2σ from the baseline mean (sweep was incomplete or over-extended)
- The homing offset is very large (>2048 counts) — arm was likely not near its midpoint during calibration
- A live joint position is outside its calibrated range


> **Joint Range Statistics (Visual Reference)**
>
> The following plot summarizes the baseline calibration statistics across joints.
> This is the data used by `so101_check_calibration.py` for comparison:
>
> ![Calibration stats box-and-whisker plot](./calibration_stats.png)
>
> *Figure: Per-joint motion range (encoder counts) — median, spread, and outliers across calibration files.*
