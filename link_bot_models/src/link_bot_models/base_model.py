import json
import os

import numpy as np
import tensorflow as tf
from colorama import Fore
from tensorflow.python import debug as tf_debug

import link_bot_pycommon.experiments_util
from link_bot_models.exceptions import FinishSetupNotCalledInConstructor


class BaseModel:

    def __init__(self, args_dict, N):
        """
        args_dict: the argsparse args but as a dict
        N: dimensionality of the full state
        """
        self.args_dict = args_dict
        self.N = N

        # A bunch of variables we assume will be defined by subclasses
        self.sess = None
        self.saver = None
        self.global_step = None
        self.loss = None
        self.opt = None
        self.train_summary = None
        self.validation_summary = None

        self.finish_setup_called = False

        # add some default arguments
        # FIXME: add these to the command line parsers
        if 'gpu_memory_fraction' not in self.args_dict:
            self.args_dict['gpu_memory_fraction'] = 0.2

        self.seed = self.args_dict['seed']
        np.random.seed(self.seed)
        tf.random.set_random_seed(self.seed)

    def finish_setup(self):
        self.train_summary = tf.summary.merge_all('train')
        self.validation_summary = tf.summary.merge_all('validation')

        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=self.args_dict['gpu_memory_fraction'])
        self.sess = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))
        if self.args_dict['debug']:
            self.sess = tf_debug.LocalCLIDebugWrapperSession(self.sess)
        self.saver = tf.train.Saver(max_to_keep=None)

        self.finish_setup_called = True

    def setup(self):
        if not self.finish_setup_called:
            raise FinishSetupNotCalledInConstructor(type(self).__name__)

        if self.args_dict['checkpoint']:
            self.sess.run([tf.local_variables_initializer()])
            self.load()
        else:
            self.init()

    def init(self):
        self.sess.run([tf.global_variables_initializer(), tf.local_variables_initializer()])

    def train(self, train_x, train_y, validation_x, validation_y, epochs, log_path, **kwargs):
        """

        :param train_x: a numpy ndarray where each row looks like [sdf_data, rope_configuration]
        :param train_y: a 2d numpy array where each is binary indicating constraint violation
        :param validation_x: ''
        :param validation_y: ''
        :param epochs: number of times to run through the full training set
        :param log_path:
        :param kwargs:
        :return: whether the training process was interrupted early (by Ctrl+C)
        """
        if not self.finish_setup_called:
            raise FinishSetupNotCalledInConstructor(type(self).__name__)

        interrupted = False

        writer = None
        loss = None
        full_log_path = None
        if self.args_dict['log'] is not None:
            full_log_path = os.path.join("log_data", log_path)

            link_bot_pycommon.experiments_util.make_log_dir(full_log_path)

            metadata_path = os.path.join(full_log_path, "metadata.json")
            metadata_file = open(metadata_path, 'w')
            metadata = self.metadata()
            metadata['log path'] = full_log_path
            metadata_file.write(json.dumps(metadata, indent=2))

            writer = tf.summary.FileWriter(full_log_path)
            writer.add_graph(self.sess.graph)

        try:
            train_ops = [self.global_step, self.train_summary, self.loss, self.opt]
            validation_ops = [self.validation_summary, self.loss]

            self.start_train_hook()

            if self.args_dict['log'] is not None:
                self.save(full_log_path, self.args_dict['log'])

            # validation sets could be too big, so we randomly choose 1000 examples
            print(validation_x.shape[0])
            validation_indexes = np.random.choice(validation_x.shape[0], size=100)
            validation_x_sample = validation_x[validation_indexes]
            validation_y_sample = validation_y[validation_indexes]

            step = self.sess.run(self.global_step)
            for epoch in range(epochs):
                # shuffle indexes and then iterate over batches
                batch_size = self.args_dict['batch_size']
                indexes = np.arange(train_x.shape[0], dtype=np.int)
                np.random.shuffle(indexes)

                for batch_start in range(0, train_x.shape[0], batch_size):
                    batch_indexes = indexes[batch_start:batch_start + batch_size]
                    train_x_batch = train_x[batch_indexes]
                    train_y_batch = train_y[batch_indexes]

                    train_feed_dict = self.build_feed_dict(train_x_batch, train_y_batch, **kwargs)

                    self.train_feed_hook(step, train_x_batch, train_y_batch)

                    step, train_summary, train_loss, _ = self.sess.run(train_ops, feed_dict=train_feed_dict)

                    if step % self.args_dict['log_period'] == 0 or step == 1:
                        if self.args_dict['log'] is not None:
                            writer.add_summary(train_summary, step)

                    if step % self.args_dict['print_period'] == 0 or step == 1:
                        print('epoch {:4d}, step: {:4d}, train loss: {:8.4f}'.format(epoch, step, train_loss))

                if epoch % self.args_dict['val_period'] == 0 or epoch == 0:
                    validation_feed_dict = self.build_feed_dict(validation_x_sample, validation_y_sample, **kwargs)
                    validation_summary, validation_loss = self.sess.run(validation_ops, feed_dict=validation_feed_dict)

                    print('epoch {:4d}, step: {:4d}, validation loss: {:8.4f}'.format(epoch, step, validation_loss))
                    if self.args_dict['log'] is not None:
                        writer.add_summary(validation_summary, step)

                if epoch % self.args_dict['save_period'] == 0 and epoch > 0:
                    if self.args_dict['log'] is not None:
                        self.save(full_log_path, loss=validation_loss)

        except KeyboardInterrupt:
            print("stop!!!")
            interrupted = True
            pass
        finally:
            if self.args_dict['verbose']:
                print("Loss: {}".format(loss))

        return interrupted

    def build_feed_dict(self, x, y, **kwargs):
        raise NotImplementedError()

    def start_train_hook(self):
        pass

    def train_feed_hook(self, iteration, train_x_batch, train_y_batch):
        pass

    def load(self):
        self.saver.restore(self.sess, self.args_dict['checkpoint'])
        global_step = self.sess.run(self.global_step)
        print(
            Fore.CYAN + "Restored ckpt {} at step {:d}".format(self.args_dict['checkpoint'], global_step) + Fore.RESET)

    def save(self, log_path, log=True, loss=None):
        global_step = self.sess.run(self.global_step)
        if log:
            if loss is not None:
                print(Fore.CYAN + "Saving ckpt {} at step {:d} with loss {}".format(log_path, global_step,
                                                                                    loss) + Fore.RESET)
            else:
                print(Fore.CYAN + "Saving ckpt {} at step {:d}".format(log_path, global_step) + Fore.RESET)
        self.saver.save(self.sess, os.path.join(log_path, "nn.ckpt"), global_step=self.global_step)

    def metadata(self):
        raise NotImplementedError()

    def __str__(self):
        return "base_model"
