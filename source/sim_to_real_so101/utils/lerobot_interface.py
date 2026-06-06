# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from collections import deque

import numpy as np
import torch
import uuid

from lerobot.teleoperators.so_leader.config_so_leader import SO101LeaderConfig
from lerobot.robots.so_follower.config_so_follower import SO101FollowerConfig

from lerobot.cameras.opencv import OpenCVCameraConfig
from lerobot.configs.policies import PreTrainedConfig
from lerobot.policies.factory import make_policy, make_pre_post_processors
from lerobot.robots import make_robot_from_config
from lerobot.processor import make_default_processors
from lerobot.datasets.pipeline_features import (
    aggregate_pipeline_dataset_features,
    create_initial_features,
)
from lerobot.datasets.feature_utils import build_dataset_frame, combine_feature_dicts
from lerobot.utils.constants import OBS_STR
from lerobot.utils.control_utils import predict_action
from lerobot.utils.device_utils import get_safe_torch_device
from lerobot.policies.utils import make_robot_action
from lerobot.utils.visualization_utils import init_rerun, log_rerun_data


# Ideally, we should make a base class for all robot interfaces,
# and inherit from it for each robot type.
class LeRobotSO101Interface:

    # USD Robot has joint ranges that are not ranging from -100 to 100
    SO101_USD_MAPPING = {
        "shoulder_pan": {"joint_min": -110, "joint_max": 110},
        "shoulder_lift": {"joint_min": -100, "joint_max": 100},
        "elbow_flex": {"joint_min": -100, "joint_max": 90},
        "wrist_flex": {"joint_min": -95, "joint_max": 95},
        "wrist_roll": {"joint_min": -160, "joint_max": 160},
        "gripper": {"joint_min": -10, "joint_max": 100},
    }

    # Joint order is the order of the joints in the USD articulation
    SO101_JOINT_ORDER = [
        "shoulder_pan.pos",
        "shoulder_lift.pos",
        "elbow_flex.pos",
        "wrist_flex.pos",
        "wrist_roll.pos",
        "gripper.pos",
    ]

    def __init__(
        self,
        device: str,
        port: str,
        id: str,
        cameras: dict,
        fps: int,
        kind: str = "leader",
        rename_map: dict = None,
    ):

        self.port = port
        self.id = id
        self.cameras = cameras
        self.device = device
        self.fps = fps
        self.kind = kind
        self.rename_map = rename_map

        self.joint_names = [joint.split(".")[0] for joint in self.SO101_JOINT_ORDER]
        self.joint_mins = torch.tensor(
            [self.SO101_USD_MAPPING[name]["joint_min"] for name in self.joint_names],
            dtype=torch.float32,
            device=self.device,
        )
        self.joint_maxs = torch.tensor(
            [self.SO101_USD_MAPPING[name]["joint_max"] for name in self.joint_names],
            dtype=torch.float32,
            device=self.device,
        )

    def make_cameras_cfg(self):
        cameras = {}
        # need to rename cameras to match policy/feature config
        for camera in self.cameras.keys():
            camera_name = camera
            if self.rename_map:
                camera_name = self.rename_map[camera]
            cameras[camera_name] = OpenCVCameraConfig(
                index_or_path="null",  # we are in simulation :)
                fps=self.fps,
                width=self.cameras[camera]["width"],
                height=self.cameras[camera]["height"],
            )

        return cameras

    def make_cfg(self):
        if self.kind == "leader":
            return SO101LeaderConfig(port=self.port, id=self.id)
        elif self.kind == "follower":
            cameras = self.make_cameras_cfg()
            return SO101FollowerConfig(port=self.port, id=self.id, cameras=cameras)

    def init_device(self, visualize: bool = False):
        self.cfg = self.make_cfg()
        self.robot = make_robot_from_config(self.cfg)

        random_session_name = f"eval_{uuid.uuid4().hex[:8]}"
        if visualize:
            init_rerun(session_name=random_session_name)

        print(f"[INFO]: Connected to the Arm at {self.port} with id {self.id}")

    def connect(self):
        self.robot.connect()

    def get_raw_actions_tensor(self, real_action):
        return torch.tensor(
            [real_action[joint] for joint in self.SO101_JOINT_ORDER],
            dtype=torch.float32,
            device=self.device,
        )

    def get_mapped_actions_vectorized(self, raw_values):
        normalized = torch.zeros_like(raw_values)
        normalized[:-1] = (
            raw_values[:-1] + 100
        ) / 200.0  # first 5 joints: -100-100 -> 0-1
        normalized[-1] = raw_values[-1] / 100.0  # gripper: 0-100 -> 0-1

        # Map to joint ranges (degrees)
        mapped_deg = self.joint_mins + normalized * (self.joint_maxs - self.joint_mins)

        # Convert to radians
        return mapped_deg * torch.pi / 180

    def get_raw_actions_from_radians(self, raw_values):
        # Convert from radians to degrees
        mapped_deg = raw_values * 180 / torch.pi

        # Reverse the joint range mapping
        normalized = (mapped_deg - self.joint_mins) / (
            self.joint_maxs - self.joint_mins
        )

        # Reverse the normalization
        raw_degrees = torch.zeros_like(normalized)
        raw_degrees[:-1] = (
            normalized[:-1] * 200.0 - 100
        )  # first 5 joints: 0-1 -> -100-100
        raw_degrees[-1] = normalized[-1] * 100.0  # gripper: 0-1 -> 0-100

        return raw_degrees

    def make_policy(
        self,
        name_or_path: str,
    ):

        _, self.robot_action_processor, self.robot_observation_processor = (
            make_default_processors()
        )

        self.dataset_features = combine_feature_dicts(
            # Observation features (joint positions + camera images)
            aggregate_pipeline_dataset_features(
                pipeline=self.robot_observation_processor,
                initial_features=create_initial_features(
                    observation=self.robot.observation_features
                ),
                use_videos=True,  # MUST be True to include cameras!
            ),
            # Action features (target joint positions)
            aggregate_pipeline_dataset_features(
                pipeline=self.robot_action_processor,
                initial_features=create_initial_features(
                    action=self.robot.action_features
                ),
                use_videos=True,
            ),
        )

        policy_config = PreTrainedConfig.from_pretrained(name_or_path)
        policy_config.pretrained_path = name_or_path
        policy_config.device = self.device

        self.dataset_meta = DummyDatasetMeta(self.dataset_features, self.robot.name)

        self.policy = make_policy(policy_config, ds_meta=self.dataset_meta)

        print(f"[INFO]: Policy loaded")

        self.preprocessor, self.postprocessor = make_pre_post_processors(
            policy_cfg=policy_config,
            pretrained_path=name_or_path,
            dataset_stats={},  # No normalization stats (policy handles it)
            preprocessor_overrides={
                "device_processor": {"device": policy_config.device},
            },
        )

        print(f"[INFO]: Preprocessor and postprocessor loaded")

    def sim_obs_to_policy_processor(self, sim_observation: torch.Tensor, visual_obs: dict) -> dict:
        # TODO: makes no sense to copy to host here, but this is whay predict_action expects

        state: torch.Tensor = self.get_raw_actions_from_radians(sim_observation)

        state_np = state.cpu().numpy()

        sim_observation = {}
        sim_observation["shoulder_pan.pos"] = state_np[0]
        sim_observation["shoulder_lift.pos"] = state_np[1]
        sim_observation["elbow_flex.pos"] = state_np[2]
        sim_observation["wrist_flex.pos"] = state_np[3]
        sim_observation["wrist_roll.pos"] = state_np[4]
        sim_observation["gripper.pos"] = state_np[5]

        for camera in self.cameras.keys():
            img: torch.Tensor = (
                visual_obs[f"rgb_{camera}"][0].clone()
            )
            # need to rename camera to match policy/feature config
            camera_name = camera
            if self.rename_map:
                camera_name = self.rename_map[camera]
            sim_observation[camera_name] = img.cpu().detach().numpy()

        obs_processed = self.robot_observation_processor(sim_observation)
        observation_frame = build_dataset_frame(
            self.dataset_features,  # Defines structure of observations/actions
            obs_processed,  # Raw observation data
            prefix=OBS_STR,  # Adds "observation." prefix to keys
        )

        return observation_frame

    def predict_action(self, observation_frame: dict) -> dict:
        action_values = predict_action(
            observation=observation_frame,
            policy=self.policy,
            device=get_safe_torch_device(self.policy.config.device),
            preprocessor=self.preprocessor,
            postprocessor=self.postprocessor,
            use_amp=self.policy.config.use_amp,
            task="Pick up the vial and place it in the tray",
            robot_type=self.robot.robot_type,
        )
        return action_values

    def prediction_to_sim_processor(
        self, action_values: dict, observation_frame: dict, log: bool = False
    ) -> dict:
        robot_action = make_robot_action(action_values, self.dataset_features)
        robot_action_to_send = self.robot_action_processor((robot_action, None))

        motor_actions = {
            k: v for k, v in robot_action_to_send.items() if k.endswith(".pos")
        }
        sim_motor_actions: torch.Tensor = self.get_raw_actions_tensor(motor_actions)

        if log:
            log_rerun_data(observation=observation_frame, action=robot_action)

        mapped_sim_motor_actions: torch.Tensor = self.get_mapped_actions_vectorized(
            sim_motor_actions
        )
        return mapped_sim_motor_actions

    # TODO: we should return a single object instead of a tuple
    def real_to_sim_obs_processor(
        self, sim_obs: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        real_action: torch.Tensor = self.get_raw_actions_tensor(sim_obs)
        mapped_action: torch.Tensor = self.get_mapped_actions_vectorized(real_action)
        return real_action, mapped_action

    # TODO: we should return a single object instead of a tuple
    def sim_to_real_dataset_processor(
        self, policy_obs: torch.Tensor, visual_obs: dict
    ) -> tuple[torch.Tensor, dict]:
        real_obs = self.get_raw_actions_from_radians(policy_obs)

        visual_buffers = {}
        depth_buffers = {}
        instance_id_seg_buffers = {}

        for camera in self.cameras.keys():
            visual_buffers[camera] = visual_obs[f"rgb_{camera}"][0]
            depth_buffers[camera] = visual_obs[f"depth_{camera}"][0]
            instance_id_seg_buffers[camera] = visual_obs[f"instance_id_seg_{camera}"][0][..., :3]

        return real_obs, visual_buffers, depth_buffers, instance_id_seg_buffers


class GR00TRemotePolicy:
    def __init__(
        self,
        robot_iface: LeRobotSO101Interface,
        host: str = "localhost",
        port: int = 5555,
        action_horizon: int = 8,
        lang_instruction: str = "Pick up the vial and place it in the tray",
    ):
        self._iface = robot_iface
        self._host = host
        self._port = port
        self._action_horizon = action_horizon
        self._lang_instruction = lang_instruction
        self._action_queue: deque = deque()
        self._client = None

    def connect(self):
        """Connect to the GR00T policy server."""
        from sim_to_real_so101.gr00t_client.server_client import PolicyClient

        print(f"[INFO]: Connecting to GR00T policy server at {self._host}:{self._port}...")
        self._client = PolicyClient(host=self._host, port=self._port)
        if not self._client.ping():
            raise RuntimeError("Cannot connect to GR00T policy server!")
        print("[INFO]: Policy server connected")

    def reset(self):
        """Reset server-side policy state and clear the local action buffer."""
        self._client.reset()
        self._action_queue.clear()

    # ------------------------------------------------------------------
    # Observation conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _add_batch_time_dims(obs: dict) -> dict:
        """Recursively add one leading dimension to every leaf array/scalar.

        GR00T expects (batch=1, time=1, ...).  Call twice to get both dims.
        """
        for key, val in obs.items():
            if isinstance(val, np.ndarray):
                obs[key] = val[np.newaxis, ...]
            elif isinstance(val, dict):
                obs[key] = GR00TRemotePolicy._add_batch_time_dims(val)
            else:
                obs[key] = [val]
        return obs

    def _sim_obs_to_groot_inputs(
        self, joint_positions: torch.Tensor, visual_obs: dict
    ) -> dict:
        """Convert Isaac Lab sim observations into the GR00T VLA input format.

        Args:
            joint_positions: Joint positions in sim radians, shape (6,).
            visual_obs: Visual observation dict from env (e.g. rgb_ego, rgb_external_D455).

        Returns:
            Dict with ``video``, ``state``, and ``language`` keys,
            each with (B=1, T=1) leading dimensions.
        """
        state = self._iface.get_raw_actions_from_radians(joint_positions)
        state_np = state.cpu().numpy().astype(np.float32)

        rename = self._iface.rename_map
        model_obs = {}

        # Cameras → video dict
        model_obs["video"] = {}
        for camera in self._iface.cameras.keys():
            img = visual_obs[f"rgb_{camera}"][0].cpu().numpy()
            camera_name = rename[camera] if rename else camera
            model_obs["video"][camera_name] = img

        # Joint state (arm + gripper, matching GR00T convention)
        model_obs["state"] = {
            "single_arm": state_np[:5],
            "gripper": state_np[5:6],
        }

        # Language instruction
        model_obs["language"] = {
            "annotation.human.task_description": self._lang_instruction,
        }

        # Add (B=1, T=1) leading dimensions
        model_obs = self._add_batch_time_dims(model_obs)
        model_obs = self._add_batch_time_dims(model_obs)

        return model_obs

    # ------------------------------------------------------------------
    # Action decoding
    # ------------------------------------------------------------------

    def _decode_action_chunk(self, action_chunk: dict) -> list[dict[str, float]]:
        """Decode a GR00T action chunk into per-timestep joint-name dicts.

        Args:
            action_chunk: Dict with ``single_arm`` (B, T, 5) and
                ``gripper`` (B, T, 1) numpy arrays.

        Returns:
            List of dicts mapping joint name → float (real-robot degree space).
        """
        any_key = next(iter(action_chunk.keys()))
        T = action_chunk[any_key].shape[1]
        horizon = min(T, self._action_horizon)

        joint_order = self._iface.SO101_JOINT_ORDER
        actions_list = []
        for t in range(horizon):
            single_arm = action_chunk["single_arm"][0][t]  # (5,)
            gripper = action_chunk["gripper"][0][t]          # (1,)
            full = np.concatenate([single_arm, gripper], axis=0)  # (6,)
            actions_list.append(
                {name: float(full[i]) for i, name in enumerate(joint_order)}
            )

        return actions_list

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_action(
        self, joint_positions: torch.Tensor, visual_obs: dict, log: bool = False
    ) -> torch.Tensor:
        """Return the next sim action as a radians tensor.

        Manages the action buffer internally — queries the server only
        when the buffer is empty, then pops one action per call.

        Args:
            joint_positions: Current joint positions in sim radians (6,).
            visual_obs: Visual observation dict from env.
            log: If True, send observation and action data to Rerun.

        Returns:
            Tensor of mapped joint actions in sim radians, ready
            for ``env.step()``.
        """
        if len(self._action_queue) == 0:
            model_input = self._sim_obs_to_groot_inputs(joint_positions, visual_obs)
            action_chunk, _info = self._client.get_action(model_input)
            decoded = self._decode_action_chunk(action_chunk)
            self._action_queue.extend(decoded)

        action_dict = self._action_queue.popleft()
        raw_tensor = self._iface.get_raw_actions_tensor(action_dict)
        sim_action = self._iface.get_mapped_actions_vectorized(raw_tensor)

        if log:
            state = self._iface.get_raw_actions_from_radians(joint_positions)
            state_np = state.cpu().numpy()
            rename = self._iface.rename_map

            obs_log = {}
            for camera in self._iface.cameras.keys():
                name = rename[camera] if rename else camera
                obs_log[name] = visual_obs[f"rgb_{camera}"][0].cpu().numpy()
            for i, joint in enumerate(self._iface.SO101_JOINT_ORDER):
                obs_log[joint] = float(state_np[i])

            log_rerun_data(observation=obs_log, action=action_dict)

        return sim_action


class DummyDatasetMeta:
    def __init__(self, features, robot_type):
        self.features = features
        self.stats = {}
        self.robot_type = robot_type


if __name__ == "__main__":
    lerobot_cfg = {"port": "/dev/ttyACM0", "id": "leader_arm_1"}
    lerobot_interface = LeRobotSO101Interface(cfg=lerobot_cfg)
    while True:
        real_action = lerobot_interface.teleop_dev.get_action()
        print(type(real_action))
        print(real_action)
        print(list(real_action.keys()))
