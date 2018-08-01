import os
import time
import random
import argparse
import logging
import numpy as np
import tensorflow as tf
import ray
import ray.rllib.agents.ppo as ppo
from ray.tune.registry import register_env
from tensorboardX import SummaryWriter
from evaluator import Evaluator

MAX_STEPS_PER_EPISODE = 300
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def env_creator(env_config):
    from custom_env import CustomEnv
    env = CustomEnv(action_repeat=args.action_repeat, 
                    integrator_accuracy=args.integrator_accuracy,
                    reward_type=args.reward_type,
                    binary_action=args.binary_action)
    return env


def configure(args):
    config = ppo.DEFAULT_CONFIG.copy()

    # common
    config["horizon"] = MAX_STEPS_PER_EPISODE // args.action_repeat
    config["num_workers"] = args.num_workers
    config["model"]["squash_to_range"] = True # action clip

    # PPO specific
    config["timesteps_per_batch"] = args.num_workers * (MAX_STEPS_PER_EPISODE // args.action_repeat) # an episode per worker
    config["num_sgd_iter"] = args.epochs
    config["sgd_stepsize"] = args.stepsize
    config["sgd_batchsize"] = args.batch_size
    if args.gpu is True:
        config["num_gpus"] = args.num_gpus
    
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RLlib version AI for Prosthetics Challenge")
    # Ray
    parser.add_argument("--redis-address", default="192.168.1.137:16379", type=str, help="address of the Redis server")
    parser.add_argument("--num-workers", default=1, type=int, help="number of workers for parallelism")
    parser.add_argument("--num-cpus", default=1, type=int, help="number of local cpus")
    parser.add_argument("--cluster", default=False, action="store_true", help="whether use cluster or local computer")
    # hyperparameters
    parser.add_argument("--epochs", default=30, type=int, help="number of epoch")
    parser.add_argument("--batch-size", default=128, type=int, help="minibatch size")
    parser.add_argument("--stepsize", default=5e-5, type=float, help="stepsize for optimization")
    parser.add_argument("--action-repeat", default=1, type=int, help="repeat time for each action")
    parser.add_argument("--binary-action", default=False, action="store_true", help="action can only be 0 or 1")
    parser.add_argument("--reward-type", default="2018", type=str, help="reward type")
    # environment
    parser.add_argument("--integrator-accuracy", default=1e-3, type=float, help="simulator integrator accuracy")
    parser.add_argument("--gpu", default=False, action="store_true", help="use GPU for optimization")
    parser.add_argument("--num-gpus", default=None, type=int, help="number of gpus")
    # checkpoint and validation
    parser.add_argument("--checkpoint-dir", default="output", type=str, help="checkpoint output directory")
    parser.add_argument("--checkpoint-interval", default=5, type=int, help="iteration interval for checkpoint")
    parser.add_argument("--validation-interval", default=5, type=int, help="iteration interval for validation")
    # random seed
    parser.add_argument("--seed", default=-1, type=int, help="random seed")
    
    args = parser.parse_args()

    # set random seed
    if args.seed > 0:
        seed = args.seed
    else:
        seed = np.random.randint(0, 2**32)
    random.seed(seed)
    np.random.seed(seed)
    tf.set_random_seed(seed)
    logger.debug('random seed: {}'.format(seed))

    if args.cluster is True:
        ray.init(redis_address=args.redis_address)
    else:
        ray.init(num_cpus=args.num_cpus)

    register_env("CustomEnv", env_creator)
    config = configure(args)

    agent = ppo.PPOAgent(env="CustomEnv", config=config)

    # verify checkpoint directory
    if not os.path.exists(args.checkpoint_dir):
        os.mkdir(args.checkpoint_dir)
    
    # initialize evaluator
    evaluator = Evaluator(action_repeat=args.action_repeat, binary_action=args.binary_action)

    # tensorboard for validation reward
    timestamp = time.time()
    timestruct = time.localtime(timestamp)
    writer = SummaryWriter(os.path.join(args.checkpoint_dir, time.strftime('%Y-%m-%d_%H-%M-%S', timestruct)))

    # agent training
    while True:
        train_result = agent.train()

        # log out useful information
        logger.info('training iteration: #{}'.format(train_result.training_iteration))
        logger.info('time this iteration: {}'.format(train_result.time_this_iter_s))
        if train_result.timesteps_this_iter > 0:
            logger.debug('timestep number this iteration: {}'.format(train_result.timesteps_this_iter))
            logger.debug('total timesteps: {}'.format(train_result.timesteps_total))
            logger.debug('episode number this iteration: {}'.format(train_result.episodes_total))
            logger.debug('episode mean length: {} (x{})'.format(train_result.episode_len_mean, args.action_repeat))
            logger.debug('episode reward:')
            logger.debug('  [mean] {}'.format(train_result.episode_reward_mean))
            logger.debug('  [max] {}'.format(train_result.episode_reward_max))
            logger.debug('  [min] {}'.format(train_result.episode_reward_min))
            # record mean reward and episode length in private tensorboard
            writer.add_scalar('train/mean_reward', train_result.episode_reward_mean, train_result.training_iteration)
            writer.add_scalar('train/mean_steps', train_result.episode_len_mean, train_result.training_iteration)
        else:
            logger.debug('<no timestep this iteration>')
        logger.debug('--------------------------------------------------')

        # record train time this iteration in private tensorboard
        writer.add_scalar('train/time', train_result.time_this_iter_s, train_result.training_iteration)

        # validation
        if train_result.training_iteration % args.validation_interval == 0:
            validation_reward, validation_steps = evaluator(agent)
            logger.info(' > validation at iteration: #{}: reward = {}, episode length = {}'.format(
                train_result.training_iteration, validation_reward, validation_steps
            ))
            # record validation score/steps in private tensorboard
            writer.add_scalar('validation/reward', validation_reward, train_result.training_iteration)
            writer.add_scalar('validation/steps', validation_steps, train_result.training_iteration)

        # checkpoint
        if train_result.training_iteration % args.checkpoint_interval == 0:
            save_result = agent.save(args.checkpoint_dir)
            logger.info('[checkpoint] iteration #{} at {}'.format(train_result.training_iteration, save_result))
