import os
import argparse
import logging
import numpy as np
from osim.env import ProstheticsEnv
import ray
import ray.rllib.agents.ppo as ppo
from ray.tune.registry import register_env
from evaluator import Evaluator

MAX_STEPS_PER_ITERATION = 300
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def env_creator(env_config):
    from custom_env import CustomEnv
    env = CustomEnv(action_repeat=args.action_repeat)
    return env


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RLlib version AI for Prosthetics Challenge")
    # hyperparameters
    parser.add_argument("--action-repeat", default=1, type=int, help="repeat time for each action")
    parser.add_argument("--binary-action", default=False, action="store_true", help="action can only be 0 or 1")
    # checkpoint
    parser.add_argument("--checkpoint-dir", default="output", type=str, help="checkpoint output directory")
    parser.add_argument("--checkpoint-id", default=None, type=str, help="id of checkpoint file")
    parser.add_argument("--no-render", default=False, action="store_true", help="no visualization for evaluation")
    
    args = parser.parse_args()

    ray.init(num_cpus=2)

    register_env("CustomEnv", env_creator)
    config = ppo.DEFAULT_CONFIG.copy()

    evaluator = Evaluator(args.action_repeat, render=True if args.no_render is False else False)

    agent = ppo.PPOAgent(env="CustomEnv", config=config)
    checkpoint_path = os.path.join(args.checkpoint_dir, "checkpoint-" + str(args.checkpoint_id))
    agent.restore(checkpoint_path=checkpoint_path)

    evaluation_reward, evaluation_steps = evaluator(agent)
    logger.info('score: {}'.format(evaluation_reward))
    logger.info('steps: {}'.format(evaluation_steps))

    evaluator.close()
