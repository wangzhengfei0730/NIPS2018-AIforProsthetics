import os
import time
import joblib
import numpy as np
import os.path as osp
import tensorflow as tf
from baselines import logger
from collections import deque
from baselines.common import explained_variance
from baselines.common.runners import AbstractEnvRunner

import pickle
import copy

class Model(object):
    def __init__(self, *, policy, ob_space, ac_space, nbatch_act, nbatch_train,
                nsteps, ent_coef, vf_coef, max_grad_norm):
        sess = tf.get_default_session()

        act_model = policy(sess, ob_space, ac_space, nbatch_act, 1, reuse=False)
        train_model = policy(sess, ob_space, ac_space, nbatch_train, nsteps, reuse=True)

        A = train_model.pdtype.sample_placeholder([None])
        ADV = tf.placeholder(tf.float32, [None])
        R = tf.placeholder(tf.float32, [None])
        OLDNEGLOGPAC = tf.placeholder(tf.float32, [None])
        OLDVPRED = tf.placeholder(tf.float32, [None])
        LR = tf.placeholder(tf.float32, [])
        CLIPRANGE = tf.placeholder(tf.float32, [])

        neglogpac = train_model.pd.neglogp(A)
        entropy = tf.reduce_mean(train_model.pd.entropy())

        vpred = train_model.vf
        vpredclipped = OLDVPRED + tf.clip_by_value(train_model.vf - OLDVPRED, - CLIPRANGE, CLIPRANGE)
        vf_losses1 = tf.square(vpred - R)
        vf_losses2 = tf.square(vpredclipped - R)
        vf_loss = .5 * tf.reduce_mean(tf.maximum(vf_losses1, vf_losses2))
        ratio = tf.exp(OLDNEGLOGPAC - neglogpac)
        pg_losses = -ADV * ratio
        pg_losses2 = -ADV * tf.clip_by_value(ratio, 1.0 - CLIPRANGE, 1.0 + CLIPRANGE)
        pg_loss = tf.reduce_mean(tf.maximum(pg_losses, pg_losses2))
        approxkl = .5 * tf.reduce_mean(tf.square(neglogpac - OLDNEGLOGPAC))
        clipfrac = tf.reduce_mean(tf.to_float(tf.greater(tf.abs(ratio - 1.0), CLIPRANGE)))
        loss = pg_loss - entropy * ent_coef + vf_loss * vf_coef
        with tf.variable_scope('model'):
            params = tf.trainable_variables()
        grads = tf.gradients(loss, params)
        if max_grad_norm is not None:
            grads, _grad_norm = tf.clip_by_global_norm(grads, max_grad_norm)
        grads = list(zip(grads, params))
        trainer = tf.train.AdamOptimizer(learning_rate=LR, epsilon=1e-5)
        _train = trainer.apply_gradients(grads)

        def train(lr, cliprange, obs, returns, masks, actions, values, neglogpacs, states=None):
            advs = returns - values
            advs = (advs - advs.mean()) / (advs.std() + 1e-8)
            td_map = {train_model.X:obs, A:actions, ADV:advs, R:returns, LR:lr,
                    CLIPRANGE:cliprange, OLDNEGLOGPAC:neglogpacs, OLDVPRED:values}
            if states is not None:
                td_map[train_model.S] = states
                td_map[train_model.M] = masks
            return sess.run(
                [pg_loss, vf_loss, entropy, approxkl, clipfrac, _train],
                td_map
            )[:-1]
        self.loss_names = ['policy_loss', 'value_loss', 'policy_entropy', 'approxkl', 'clipfrac']

        def save(save_path):
            ps = sess.run(params)
            joblib.dump(ps, save_path)

        def load(load_path):
            loaded_params = joblib.load(load_path)
            restores = []
            for p, loaded_p in zip(params, loaded_params):
                restores.append(p.assign(loaded_p))
            sess.run(restores)
            # If you want to load weights, also save/load observation scaling inside VecNormalize

        self.train = train
        self.train_model = train_model
        self.act_model = act_model
        self.step = act_model.step
        self.value = act_model.value
        self.initial_state = act_model.initial_state
        self.save = save
        self.load = load
        tf.global_variables_initializer().run(session=sess) #pylint: disable=E1101

class Runner(AbstractEnvRunner):

    def __init__(self, *, env, model, nsteps, gamma, lam, writer, num_casks=0):
        super().__init__(env=env, model=model, nsteps=nsteps)
        self.lam = lam
        self.gamma = gamma

        self.nenvs = env.num_envs
        self.good = set()
        self.valid = self.nenvs - num_casks
        self.casks = set()

        # tensorboard
        self.writer = writer
        self.num_episode = 0

    def run(self):
        mb_obs, mb_rewards, mb_actions, mb_values, mb_dones, mb_neglogpacs = [], [], [], [], [], []
        for i in range(self.nenvs):
            mb_obs.append([])
            mb_rewards.append([])
            mb_actions.append([])
            mb_values.append([])
            mb_dones.append([])
            mb_neglogpacs.append([])

        mb_states = self.states
        epinfos = []

        # not initialize cask agents last run
        # self.good = set([i for i in range(self.nenvs)])
        self.good = set()
        for i in range(self.nenvs):
            if i not in self.casks:
                self.good.add(i)
        while True:
            actions, values, self.states, neglogpacs = self.model.step(self.obs, self.states, self.dones)

            tmp_obs = self.obs.copy()
            for i in range(self.nenvs):
                if i in self.good or i in self.casks:
                    mb_obs[i].append(tmp_obs[i])
                    # mb_actions.append(actions)
                    mb_values[i].append(values[i])
                    mb_neglogpacs[i].append(neglogpacs[i])
                    mb_dones[i].append(self.dones[i])
                if i not in self.good:
                    actions[i] = False
            self.casks = set()
            self.obs[:], rewards, self.dones, infos = self.env.step(actions)

            self.good = set()
            for i in range(self.nenvs):
                if not infos[i].get("bad", True):
                    self.good.add(i)

            for i in range(self.nenvs):
                if i in self.good:
                    action = infos[i].get("action", actions[i])
                    mb_actions[i].append(action)
                    mb_rewards[i].append(rewards[i])

                    if len(mb_rewards[i]) >= self.nsteps:
                        self.good.remove(i)

            print([len(mb_rewards[i]) for i in range(self.nenvs)])
            print(self.good)

            # when done, add episodic information to tensorboard
            for i in range(self.nenvs):
                if self.dones[i] and i in self.good:
                    epinfos.append({'r': infos[i]['episode']['r'], 'l': infos[i]['episode']['l']})
                    self.num_episode += 1
                    summary = tf.Summary()
                    summary.value.add(tag='episode/original_reward', simple_value=infos[i]['episode']['r'])
                    summary.value.add(tag='episode/shaped_reward', simple_value=infos[i]['episode']['shaped_reward'])
                    summary.value.add(tag='episode/length', simple_value=infos[i]['episode']['l'])
                    summary.value.add(tag='episode/pelvis_x', simple_value=infos[i]['episode']['pelvis_x'])
                    self.writer.add_summary(summary, self.num_episode)

            # Cask Effect: top self.nenvs - num_casks is ready
            # if all([len(mb_rewards[i]) >= 128 for i in range(self.nenvs)]):
            if sum([len(mb_rewards[i]) >= self.nsteps for i in range(self.nenvs)]) >= self.valid:
                for i in range(self.nenvs):
                    if len(mb_rewards[i]) < self.nsteps:
                        self.casks.add(i)
                break

        # remove casks' sample
        cask_list = sorted(list(self.casks))
        print('casks:', cask_list)
        cask_list.reverse()
        for i in cask_list:
            mb_obs.pop(i)
            mb_rewards.pop(i)
            mb_actions.pop(i)
            mb_values.pop(i)
            mb_neglogpacs.pop(i)
            mb_dones.pop(i)

        #batch of steps to batch of rollouts
        mb_obs = np.asarray(mb_obs, dtype=self.obs.dtype).transpose((1, 0, 2))
        mb_rewards = np.asarray(mb_rewards, dtype=np.float32).transpose((1, 0))
        mb_actions = np.asarray(mb_actions).transpose((1, 0, 2))
        mb_values = np.asarray(mb_values, dtype=np.float32).transpose((1, 0))
        mb_neglogpacs = np.asarray(mb_neglogpacs, dtype=np.float32).transpose((1, 0))
        mb_dones = np.asarray(mb_dones, dtype=np.bool).transpose((1, 0))
        last_values = self.model.value(self.obs, self.states, self.dones)

        last_values = last_values.tolist()
        copy_dones = copy.deepcopy(self.dones)
        self.dones = self.dones.tolist()
        for i in cask_list:
            last_values.pop(i)
            self.dones.pop(i)
        last_values = np.array(last_values)
        self.dones = np.array(self.dones)

        #discount/bootstrap off value fn
        mb_returns = np.zeros_like(mb_rewards)
        mb_advs = np.zeros_like(mb_rewards)
        lastgaelam = 0
        for t in reversed(range(self.nsteps)):
            if t == self.nsteps - 1:
                nextnonterminal = 1.0 - self.dones
                nextvalues = last_values
            else:
                nextnonterminal = 1.0 - mb_dones[t+1]
                nextvalues = mb_values[t+1]
            delta = mb_rewards[t] + self.gamma * nextvalues * nextnonterminal - mb_values[t]
            mb_advs[t] = lastgaelam = delta + self.gamma * self.lam * nextnonterminal * lastgaelam
        mb_returns = mb_advs + mb_values

        # revert valid + cask dimension dones
        self.dones = copy_dones

        return (*map(sf01, (mb_obs, mb_returns, mb_dones, mb_actions, mb_values, mb_neglogpacs)),
            mb_states, epinfos)
# obs, returns, masks, actions, values, neglogpacs, states = runner.run()
def sf01(arr):
    """
    swap and then flatten axes 0 and 1
    """
    s = arr.shape
    return arr.swapaxes(0, 1).reshape(s[0] * s[1], *s[2:])

def constfn(val):
    def f(_):
        return val
    return f

def learn(*, policy, env, nsteps, total_timesteps, ent_coef, lr,
            vf_coef=0.5,  max_grad_norm=0.5, gamma=0.99, lam=0.95,
            log_interval=10, nminibatches=4, noptepochs=4, cliprange=0.2,
            save_interval=0, load_path=None, num_casks=0):

    if isinstance(lr, float): lr = constfn(lr)
    else: assert callable(lr)
    if isinstance(cliprange, float): cliprange = constfn(cliprange)
    else: assert callable(cliprange)
    total_timesteps = int(total_timesteps)

    nenvs = env.num_envs - num_casks
    ob_space = env.observation_space
    ac_space = env.action_space
    nbatch = nenvs * nsteps
    nbatch_train = nbatch // nminibatches

    make_model = lambda : Model(policy=policy, ob_space=ob_space, ac_space=ac_space, nbatch_act=env.num_envs, nbatch_train=nbatch_train,
                    nsteps=nsteps, ent_coef=ent_coef, vf_coef=vf_coef,
                    max_grad_norm=max_grad_norm)
    if save_interval and logger.get_dir():
        import cloudpickle
        with open(osp.join(logger.get_dir(), 'make_model.pkl'), 'wb') as fh:
            fh.write(cloudpickle.dumps(make_model))
    model = make_model()
    if load_path is not None:
        model.load(load_path)
        # load running mean std
        checkdir = load_path[0:-5]
        checkpoint = int(load_path.split('/')[-1])
        if osp.exists(osp.join(checkdir, '%.5i_ob_rms.pkl' % checkpoint)):
            with open(osp.join(checkdir, '%.5i_ob_rms.pkl' % checkpoint), 'rb') as ob_rms_fp:
                env.ob_rms = pickle.load(ob_rms_fp)
        # if osp.exists(osp.join(checkdir, '%.5i_ret_rms.pkl' % checkpoint)):
        #     with open(osp.join(checkdir, '%.5i_ret_rms.pkl' % checkpoint), 'rb') as ret_rms_fp:
        #         env.ret_rms = pickle.load(ret_rms_fp)
    # tensorboard
    writer = tf.summary.FileWriter(logger.get_dir(), tf.get_default_session().graph)
    runner = Runner(env=env, model=model, nsteps=nsteps, gamma=gamma, lam=lam, writer=writer, num_casks=num_casks)

    epinfobuf = deque(maxlen=100)
    tfirststart = time.time()

    nupdates = total_timesteps//nbatch
    for update in range(1, nupdates+1):
        assert nbatch % nminibatches == 0
        nbatch_train = nbatch // nminibatches
        tstart = time.time()
        frac = 1.0 - (update - 1.0) / nupdates
        lrnow = lr(frac)
        cliprangenow = cliprange(frac)
        obs, returns, masks, actions, values, neglogpacs, states, epinfos = runner.run() #pylint: disable=E0632
        epinfobuf.extend(epinfos)
        mblossvals = []
        if states is None: # nonrecurrent version
            inds = np.arange(nbatch)
            for _ in range(noptepochs):
                np.random.shuffle(inds)
                for start in range(0, nbatch, nbatch_train):
                    end = start + nbatch_train
                    mbinds = inds[start:end]
                    slices = (arr[mbinds] for arr in (obs, returns, masks, actions, values, neglogpacs))
                    mblossvals.append(model.train(lrnow, cliprangenow, *slices))
        else: # recurrent version
            assert nenvs % nminibatches == 0
            envsperbatch = nenvs // nminibatches
            envinds = np.arange(nenvs)
            flatinds = np.arange(nenvs * nsteps).reshape(nenvs, nsteps)
            envsperbatch = nbatch_train // nsteps
            for _ in range(noptepochs):
                np.random.shuffle(envinds)
                for start in range(0, nenvs, envsperbatch):
                    end = start + envsperbatch
                    mbenvinds = envinds[start:end]
                    mbflatinds = flatinds[mbenvinds].ravel()
                    slices = (arr[mbflatinds] for arr in (obs, returns, masks, actions, values, neglogpacs))
                    mbstates = states[mbenvinds]
                    mblossvals.append(model.train(lrnow, cliprangenow, *slices, mbstates))

        lossvals = np.mean(mblossvals, axis=0)
        tnow = time.time()
        fps = int(nbatch / (tnow - tstart))
        if update % log_interval == 0 or update == 1:
            ev = explained_variance(values, returns)
            logger.logkv("serial_timesteps", update*nsteps)
            logger.logkv("nupdates", update)
            logger.logkv("total_timesteps", update*nbatch)
            logger.logkv("fps", fps)
            logger.logkv("explained_variance", float(ev))
            logger.logkv('eprewmean', safemean([epinfo['r'] for epinfo in epinfobuf]))
            logger.logkv('eplenmean', safemean([epinfo['l'] for epinfo in epinfobuf]))
            logger.logkv('time_elapsed', tnow - tfirststart)
            for (lossval, lossname) in zip(lossvals, model.loss_names):
                logger.logkv(lossname, lossval)
            logger.dumpkvs()
            # tensorboard
            summary = tf.Summary()
            summary.value.add(tag='iteration/reward_mean', simple_value=safemean([epinfo['r'] for epinfo in epinfobuf]))
            summary.value.add(tag='iteration/length_mean', simple_value=safemean([epinfo['l'] for epinfo in epinfobuf]))
            summary.value.add(tag='iteration/fps', simple_value=fps)
            writer.add_summary(summary, update)
        if save_interval and (update % save_interval == 0 or update == 1) and logger.get_dir():
            checkdir = osp.join(logger.get_dir(), 'checkpoints')
            os.makedirs(checkdir, exist_ok=True)
            savepath = osp.join(checkdir, '%.5i'%update)
            print('Saving to', savepath)
            model.save(savepath)
            # save running mean std
            with open(osp.join(checkdir, '%.5i_ob_rms.pkl' % update), 'wb') as ob_rms_fp:
                pickle.dump(env.ob_rms, ob_rms_fp)
            with open(osp.join(checkdir, '%.5i_ret_rms.pkl' % update), 'wb') as ret_rms_fp:
                pickle.dump(env.ret_rms, ret_rms_fp)
    env.close()

def safemean(xs):
    return np.nan if len(xs) == 0 else np.mean(xs)
