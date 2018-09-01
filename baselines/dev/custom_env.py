import gym
from gym.spaces import Box
from osim.env import ProstheticsEnv

OBSERVATION_SPACE = 224


class CustomEnv(ProstheticsEnv):
    def __init__(self, visualize=True, integrator_accuracy=5e-5):
        super(CustomEnv, self).__init__(visualize, integrator_accuracy)
        self.observation_space = Box(low=-10, high=+10, shape=[OBSERVATION_SPACE])
        self._episode_length = 0
        self._episode_original_reward = 0.0
        self._episode_shaped_reward = 0.0
        self._penalty = None

    def get_observation_space_size(self):
        return OBSERVATION_SPACE

    def step(self, action):
        super(CustomEnv, self).step(action)
        self._episode_length += 1

        original_reward = super(CustomEnv, self).reward()
        self._episode_original_reward += original_reward
        shaped_reward, penalty = self.reward()
        self._episode_shaped_reward += shaped_reward

        obs = self._get_observation()
        done = self.is_done() or (self.osim_model.istep >= self.spec.timestep_limit)

        info = {}
        info['penalty'] = penalty
        if done:
            info['episode'] = {
                'r': self._episode_original_reward,
                'l': self._episode_length,
                'sr': self._episode_shaped_reward
            }
            info['episode']['penalty'] = {}
            for key in self._penalty.keys():
                info['episode']['penalty'][key] = self._penalty[key] / self._episode_length

        return obs, shaped_reward, done, info

    def reward(self):
        state_desc = self.get_state_desc()
        prev_state_desc = self.get_prev_state_desc()
        if not prev_state_desc:
            return 0

        penalty = {}

        pelvis_vx = state_desc['body_vel']['pelvis'][0]

        pelvis_x = state_desc['body_pos']['pelvis'][0]
        head_x = state_desc['body_pos']['head'][0]
        penalty['lean_back'] = 10 * min(0.3, max(0, pelvis_x - head_x))

        left_foot_y = min(state_desc['body_pos']['toes_l'][1], state_desc['body_pos']['talus_l'][1])
        right_foot_y = state_desc['body_pos']['pros_foot_r'][1]
        penalty['foot_too_high'] = 10 * max(0, min(left_foot_y, right_foot_y) - 0.3)

        left_tibia_y = state_desc['body_pos']['tibia_l'][1]
        right_tibia_y = state_desc['body_pos']['pros_tibia_r'][1]
        penalty['tibia_too_high'] = 10 * (max(0, left_tibia_y - 0.6) + max(0, right_tibia_y - 0.6))

        pelvis_y = state_desc['body_pos']['pelvis'][1]
        penalty['pelvis_too_low'] = 10 * max(0, 0.75 - pelvis_y)

        torso_rot = state_desc['body_pos_rot']['torso'][2]
        penalty['rotation'] = 10 * max(0, abs(torso_rot) - 0.5)

        reward = pelvis_vx * 2 + 2
        for key in penalty.keys():
            reward -= penalty[key]

        if self._penalty is None:
            self._penalty = penalty
        else:
            for key in penalty.keys():
                self._penalty[key] += penalty[key]

        return reward, penalty

    def reset(self):
        super(CustomEnv, self).reset()
        self._episode_length = 0
        self._episode_original_reward = 0.0
        self._episode_shaped_reward = 0.0
        self._penalty = None
        return self._get_observation()

    def _get_observation(self):
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


class RepeatActionEnv(gym.ActionWrapper):
    def __init__(self, env, repeat=1):
        super(RepeatActionEnv, self).__init__(env)
        self._repeat = repeat

    def step(self, action):
        total_reward = 0.0
        for _ in range(self._repeat):
            obs, reward, done, info = self.env.step(action)
            total_reward += reward
            if done:
                break

        # episode done information
        state_desc = self.env.get_state_desc()
        if done:
            info['episode']['pelvis_x'] = state_desc['body_pos']['pelvis'][0]
            info['episode']['done'] = []
            # fall forward
            if state_desc['body_pos']['head'][0] - state_desc['body_pos']['pelvis'][0] > 0.5:
                info['episode']['done'].append('forward')
            # fall behind
            if state_desc['body_pos']['pelvis'][0] - state_desc['body_pos']['head'][0] > 0.5:
                info['episode']['done'].append('backward')
            # fall side
            if abs(state_desc['body_pos']['head'][2] - state_desc['body_pos']['pelvis'][2]) > 0.5:
                info['episode']['done'].append('side')

        return obs, total_reward, done, info


def make_env():
    env = CustomEnv(visualize=True, integrator_accuracy=1e-3)
    env = RepeatActionEnv(env=env, repeat=2)
    return env
