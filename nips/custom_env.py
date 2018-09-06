from osim.env import ProstheticsEnv
import gym
from gym.spaces import Box

OBSERVATION_SPACE = 224


class CustomEnv(ProstheticsEnv):

    def __init__(self, visualize=True, integrator_accuracy=5e-5):
        super().__init__(visualize, integrator_accuracy, difficulty=0)
        self.episode_length = 0
        self.episode_original_reward = 0.0
        self.episode_shaped_reward = 0.0

        self.observation_space = Box(low=-10, high=+10, shape=[OBSERVATION_SPACE])

    def step(self, action, project=True):
        obs, r, done, info = super(CustomEnv, self).step(action)
        self.episode_length += 1

        original_reward = super(CustomEnv, self).reward()
        self.episode_original_reward += original_reward
        self.episode_shaped_reward += r

        state_desc = self.get_state_desc()

        if done:
            info['episode'] = {
                'r': self.episode_original_reward,
                'l': self.episode_length,
                "pelvis_x": state_desc["body_pos"]["pelvis"][0],
                "shaped_reward": self.episode_shaped_reward
            }

        return obs, r, done, info

    def reset(self, project=True):
        super().reset(project=project)
        self.episode_length = 0
        self.episode_original_reward = 0.0
        self.episode_shaped_reward = 0.0
        obs = self.get_observation()
        return obs

    def get_observation_space_size(self):
        return OBSERVATION_SPACE

    def get_observation(self):
        state_desc = self.get_state_desc()

        res = []
        pelvis = None

        for body_part in ["pelvis", "head", "torso", "toes_l", "talus_l", "pros_foot_r", "pros_tibia_r"]:
            cur = []
            cur += state_desc["body_pos"][body_part]
            cur += state_desc["body_vel"][body_part]
            cur += state_desc["body_acc"][body_part]
            cur += state_desc["body_pos_rot"][body_part]
            cur += state_desc["body_vel_rot"][body_part]
            cur += state_desc["body_acc_rot"][body_part]
            if body_part == "pelvis":
                pelvis = cur
                res += cur[1:]  # make sense, pelvis.x is not important
            else:
                cur[0] -= pelvis[0]
                cur[2] -= pelvis[2]     # relative position work for x / z axis
                res += cur

        for joint in ["ankle_l", "ankle_r", "back", "hip_l", "hip_r", "knee_l", "knee_r"]:
            res += state_desc["joint_pos"][joint]
            res += state_desc["joint_vel"][joint]
            res += state_desc["joint_acc"][joint]

        for muscle in sorted(state_desc["muscles"].keys()):
            res += [state_desc["muscles"][muscle]["activation"]]
            res += [state_desc["muscles"][muscle]["fiber_length"]]
            res += [state_desc["muscles"][muscle]["fiber_velocity"]]

        cm_pos = state_desc["misc"]["mass_center_pos"]  # relative x / z axis center of mass position
        cm_pos[0] -= pelvis[0]
        cm_pos[2] -= pelvis[0]
        res = res + cm_pos + state_desc["misc"]["mass_center_vel"] + state_desc["misc"]["mass_center_acc"]

        return res

    def reward(self):
        state_desc = self.get_state_desc()
        prev_state_desc = self.get_prev_state_desc()
        if not prev_state_desc:
            return 0

        pelvis_vx = state_desc["body_vel"]["pelvis"][0]
        if pelvis_vx < 1.0:
            reward = -1
        else:
            reward = 9.0 - (pelvis_vx - 3.0) ** 2

        front_foot = state_desc["body_pos"]["pros_foot_r"][0]
        back_foot = state_desc["body_pos"]["toes_l"][0]
        dist = max(0.0, front_foot - back_foot - 0.9)
        reward -= dist * 40

        lean_back = max(0, state_desc["body_pos"]["pelvis"][0] - state_desc["body_pos"]["head"][0] - 0.2)
        reward -= lean_back * 40

        pelvis = state_desc["body_pos"]["pelvis"][1]
        reward -= max(0, 0.7 - pelvis) * 100

        pelvis_z = abs(state_desc["body_pos"]["pelvis"][2])
        reward -= max(0, pelvis_z - 0.6) * 100

        pros_vz = abs(state_desc["body_vel"]["pros_foot_r"][2])
        reward -= max(0, pros_vz - 0.75) * 100

        return reward * 0.05


class CustomActionWrapper(gym.ActionWrapper):

    def step(self, action):
        action = self.action(action)
        rew = 0
        for i in range(2):
            obs, r, done, info = self.env.step(action)
            rew += r
            if done:
                break
        info["action"] = action
        return obs, rew, done, info

    def action(self, action):
        return action


def make_env():
    env = CustomEnv(visualize=True, integrator_accuracy=1e-4)
    env = CustomActionWrapper(env)
    return env
