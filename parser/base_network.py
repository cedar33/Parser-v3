#!/usr/bin/env python
# -*- coding: UTF-8 -*-

# Copyright 2017 Timothy Dozat
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import re
import os
import pickle as pkl
import curses
import codecs

import numpy as np
import tensorflow as tf

from parser.neural import nn, nonlin, embeddings, recurrent, classifiers
from parser.graph_outputs import GraphOutputs, TrainOutputs, DevOutputs
from parser.structs import conllu_dataset 
from parser.structs import vocabs
from parser.neural.optimizers import AdamOptimizer, AMSGradOptimizer

#***************************************************************
class BaseNetwork(object):
  """"""
  
  _prefix_root = None
  _postfix_root = None
  
  #=============================================================
  # TODO the vocabs should be (ordered?) dicts rather than lists
  def __init__(self, id_vocab=None, input_vocabs=None, output_vocabs=None, extra_vocabs=None, config=None):
    """"""
    
    self._config = config
    
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
    if id_vocab is None:
      self._id_vocab = vocabs.IDIndexVocab(config=config)
    else:
      self._id_vocab = id_vocab
    
    if input_vocabs is None:
      input_vocabs = config.getlist(self, 'input_vocabs')
      self._input_vocabs = []
      for input_vocab in input_vocabs:
        VocabClass = getattr(vocabs, input_vocab)
        vocab = VocabClass(config=config)
        vocab.load() or vocab.count(self.train_conllus)
        self._input_vocabs.append(vocab)
    else:
      self._input_vocabs = input_vocabs
    
    if output_vocabs is None:
      output_vocabs = config.getlist(self, 'output_vocabs')
      self._output_vocabs = []
      for output_vocab in output_vocabs:
        VocabClass = getattr(vocabs, output_vocab)
        vocab = VocabClass(config=config)
        vocab.load() or vocab.count(self.train_conllus)
        self._output_vocabs.append(vocab)
    else:
      self._output_vocabs = output_vocabs
    
    if extra_vocabs is None:
      extra_vocabs = config.getlist(self, 'extra_vocabs')
      self._extra_vocabs = []
      for extra_vocab in extra_vocabs:
        VocabClass = getattr(vocabs, extra_vocab)
        vocab = VocabClass(config=config)
        vocab.load() or vocab.count(self.train_conllus)
        self._extra_vocabs.append(vocab)
    else:
      self._extra_vocabs = extra_vocabs
    
    self.global_step = tf.Variable(0., trainable=False, name='Global_step')
    self._vocabs = [self._id_vocab] + self._input_vocabs + self._extra_vocabs + self._output_vocabs
    return
  
  #=============================================================
  # TODO save the model
  def train(self, load=False):
    """"""
    
    trainset = conllu_dataset.CoNLLUTrainset(self.vocabs,
                                             prefix_root=self.prefix_root,
                                             postfix_root=self.postfix_root,
                                             config=self._config)
    devset = conllu_dataset.CoNLLUDevset(self.vocabs,
                                         prefix_root=self.prefix_root,
                                         postfix_root=self.postfix_root,
                                         config=self._config)
    testset = conllu_dataset.CoNLLUTestset(self.vocabs,
                                           prefix_root=self.prefix_root,
                                           postfix_root=self.postfix_root,
                                           config=self._config)
    
    factored_deptree = None
    factored_semgraph = None
    for vocab in self.output_vocabs:
      if vocab.field == 'deprel':
        factored_deptree = vocab.factorized
      elif vocab.field == 'semrel':
        factored_semgraph = vocab.factorized
    with tf.variable_scope('Network', reuse=False):
      train_outputs = TrainOutputs(*self.build_graph(reuse=False), load=load, factored_deptree=factored_deptree, factored_semgraph=factored_semgraph, config=self._config)
    with tf.variable_scope('Network', reuse=True):
      dev_outputs = DevOutputs(*self.build_graph(reuse=True), load=load, factored_deptree=factored_deptree, factored_semgraph=factored_semgraph, config=self._config)
    regularization_loss = self.l2_reg * tf.losses.get_regularization_loss() if self.l2_reg else 0
    
    update_step = tf.assign_add(self.global_step, 1)
    adam = AdamOptimizer(config=self._config)
    adam_op = adam.minimize(train_outputs.loss + regularization_loss) # returns the current step
    adam_train_tensors = [adam_op, train_outputs.accuracies]
    amsgrad = AMSGradOptimizer.from_optimizer(adam)
    amsgrad_op = amsgrad.minimize(train_outputs.loss + regularization_loss) # returns the current step
    amsgrad_train_tensors = [amsgrad_op, train_outputs.accuracies]
    dev_tensors = dev_outputs.accuracies
    
    screen_output = []
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    with tf.Session(config=config) as sess:
      sess.run(tf.global_variables_initializer())
      #---------------------------------------------------------
      def run(stdscr):
        current_optimizer = 'Adam'
        train_tensors = adam_train_tensors
        current_step = 0
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(6, curses.COLOR_BLUE, curses.COLOR_BLACK)
        curses.init_pair(7, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
        stdscr.clrtoeol()
        stdscr.addstr('\t')
        stdscr.addstr('{}\n'.format(self.save_dir), curses.A_STANDOUT)
        stdscr.clrtoeol()
        stdscr.addstr('\t')
        stdscr.addstr('GPU: {}\n'.format(self.cuda_visible_devices), curses.color_pair(1) | curses.A_BOLD)
        stdscr.clrtoeol()
        stdscr.addstr('\t')
        stdscr.addstr('Current optimizer: {}\n'.format(current_optimizer), curses.color_pair(1) | curses.A_BOLD)
        stdscr.clrtoeol()
        stdscr.addstr('\t')
        stdscr.addstr('Epoch: {:3d}'.format(0), curses.color_pair(1) | curses.A_BOLD)
        stdscr.addstr(' | ')
        stdscr.addstr('Step: {:5d}\n'.format(0), curses.color_pair(1) | curses.A_BOLD)
        stdscr.clrtoeol()
        stdscr.addstr('\t')
        stdscr.addstr('Moving acc: {:5.2f}'.format(0.), curses.color_pair(1) | curses.A_BOLD)
        stdscr.addstr(' | ')
        stdscr.addstr('Best moving acc: {:5.2f}\n'.format(0.), curses.color_pair(1) | curses.A_BOLD)
        stdscr.clrtoeol()
        stdscr.addstr('\t')
        stdscr.addstr('Steps since improvement: {:4d}\n'.format(0),  curses.color_pair(1) | curses.A_BOLD)
        stdscr.clrtoeol()
        stdscr.move(2,0)
        stdscr.refresh()
        try:
          best_accuracy = 0
          current_accuracy = 0
          steps_since_best = 0
          while current_step < self.max_steps and steps_since_best < self.max_steps_without_improvement:
            if steps_since_best > .1*self.max_steps_without_improvement and self.switch_optimizers:
              train_tensors = amsgrad_train_tensors
              current_optimizer = 'AMSGrad'
            for batch in trainset.shuffled_batch_iterator():
              train_outputs.restart_timer()
              feed_dict = trainset.set_placeholders(batch)
              _, train_scores = sess.run(train_tensors, feed_dict=feed_dict)
              train_outputs.update_history(train_scores)
              current_step += 1
              if current_step % self.print_every == 0:
                for batch in devset.shuffled_batch_iterator():
                  dev_outputs.restart_timer()
                  feed_dict = devset.set_placeholders(batch)
                  dev_scores = sess.run(dev_tensors, feed_dict=feed_dict)
                  dev_outputs.update_history(dev_scores)
                current_accuracy *= .75
                current_accuracy += .25*dev_outputs.get_current_accuracy()
                if current_accuracy >= best_accuracy:
                  steps_since_best = 0
                  best_accuracy = current_accuracy
                  self.parse_dataset(devset, dev_outputs, sess)
                  self.parse_dataset(testset, dev_outputs, sess)
                else:
                  steps_since_best += self.print_every
                current_epoch = sess.run(self.global_step)
                stdscr.addstr('\t')
                stdscr.addstr('Current optimizer: {}\n'.format(current_optimizer), curses.color_pair(1) | curses.A_BOLD)
                stdscr.clrtoeol()
                stdscr.addstr('\t')
                stdscr.addstr('Epoch: {:3d}'.format(int(current_epoch)), curses.color_pair(1) | curses.A_BOLD)
                stdscr.addstr(' | ')
                stdscr.addstr('Step: {:5d}\n'.format(int(current_step)), curses.color_pair(1) | curses.A_BOLD)
                stdscr.clrtoeol()
                stdscr.addstr('\t')
                stdscr.addstr('Moving acc: {:5.2f}'.format(current_accuracy), curses.color_pair(1) | curses.A_BOLD)
                stdscr.addstr(' | ')
                stdscr.addstr('Best moving acc: {:5.2f}\n'.format(best_accuracy), curses.color_pair(1) | curses.A_BOLD)
                stdscr.clrtoeol()
                stdscr.addstr('\t')
                stdscr.addstr('Steps since improvement: {:4d}\n'.format(int(steps_since_best)),  curses.color_pair(1) | curses.A_BOLD)
                stdscr.clrtoeol()
                train_outputs.print_recent_history(stdscr)
                dev_outputs.print_recent_history(stdscr)
                stdscr.move(2,0)
                stdscr.refresh()
            sess.run(update_step)
          with open(os.path.join(self.save_dir, 'SUCCESS'), 'w') as f:
            pass
        except KeyboardInterrupt:
          pass
        
        line = 0
        stdscr.move(line,0)
        instr = stdscr.instr().rstrip()
        while instr:
          screen_output.append(instr)
          line += 1
          stdscr.move(line,0)
          instr = stdscr.instr().rstrip()
      #---------------------------------------------------------
      curses.wrapper(run)
      
      with open(os.path.join(self.save_dir, 'scores.txt'), 'w') as f:
        f.write('\n'.join(screen_output))
      print('\n'.join(screen_output))
      
    return
  
  #=============================================================
  def parse_dataset(self, dataset, graph_outputs, sess):
    """"""
    
    probability_tensors = graph_outputs.probabilities
    filenames = dataset.filenames
    for file_index, filename in enumerate(filenames):
      for indices in dataset.file_batch_iterator(file_index):
        graph_outputs.restart_timer()
        feed_dict = dataset.set_placeholders(indices)
        probabilities = sess.run(probability_tensors, feed_dict=feed_dict)
        predictions = graph_outputs.probs_to_preds(probabilities)
        tokens = dataset.get_tokens(indices)
        tokens.update(dataset.preds_to_toks(predictions))
        graph_outputs.cache_predictions(tokens, indices)
      
      dirname, basename = os.path.split(filename)
      newdirname = os.path.join(self.save_dir, 'parsed', dirname)
      newfilename = os.path.join(newdirname, basename)
      if not os.path.exists(newdirname):
        os.makedirs(newdirname)
      with codecs.open(newfilename, 'w', encoding='utf-8') as f:
        graph_outputs.dump_current_predictions(f)
    return
  
  #=============================================================
  @property
  def train_conllus(self):
    return self._config.getfiles(self, 'train_conllus')
  @property
  def prefix_root(self):
    return self._prefix_root
  @property
  def postfix_root(self):
    return self._postfix_root
  @property
  def cuda_visible_devices(self):
    return os.getenv('CUDA_VISIBLE_DEVICES')
  @property
  def save_dir(self):
    return self._config.getstr(self, 'save_dir')
  @property
  def vocabs(self):
    return self._vocabs
  @property
  def id_vocab(self):
    return self._id_vocab
  @property
  def input_vocabs(self):
    return self._input_vocabs
  @property
  def output_vocabs(self):
    return self._output_vocabs
  @property
  def l2_reg(self):
    return self._config.getfloat(self, 'l2_reg')
  @property
  def recur_size(self):
    return self._config.getint(self, 'recur_size')
  @property
  def n_layers(self):
    return self._config.getint(self, 'n_layers')
  @property
  def first_layer_conv_width(self):
    return self._config.getint(self, 'first_layer_conv_width')
  @property
  def conv_width(self):
    return self._config.getint(self, 'conv_width')
  @property
  def conv_keep_prob(self):
    return self._config.getfloat(self, 'conv_keep_prob')
  @property
  def recur_keep_prob(self):
    return self._config.getfloat(self, 'recur_keep_prob')
  @property
  def recur_include_prob(self):
    return self._config.getfloat(self, 'recur_include_prob')
  @property
  def bidirectional(self):
    return self._config.getboolean(self, 'bidirectional')
  @property
  def hidden_func(self):
    hidden_func = self._config.getstr(self, 'hidden_func')
    if hasattr(nonlin, hidden_func):
      return getattr(nonlin, hidden_func)
    else:
      raise AttributeError("module '{}' has no attribute '{}'".format(nonlin.__name__, hidden_func))
  @property
  def recur_func(self):
    recur_func = self._config.getstr(self, 'recur_func')
    if hasattr(nonlin, recur_func):
      return getattr(nonlin, recur_func)
    else:
      raise AttributeError("module '{}' has no attribute '{}'".format(nonlin.__name__, recur_func))
  @property
  def highway_func(self):
    highway_func = self._config.getstr(self, 'highway_func')
    if hasattr(nonlin, highway_func):
      return getattr(nonlin, highway_func)
    else:
      raise AttributeError("module '{}' has no attribute '{}'".format(nonlin.__name__, highway_func))
  @property
  def recur_cell(self):
    recur_cell = self._config.getstr(self, 'recur_cell')
    if hasattr(recurrent, recur_cell):
      return getattr(recurrent, recur_cell)
    else:
      raise AttributeError("module '{}' has no attribute '{}'".format(recurrent.__name__, recur_cell))
  @property
  def cifg(self):
    return self._config.getboolean(self, 'cifg')
  @property
  def bilin(self):
    return self._config.getboolean(self, 'bilin')
  @property
  def switch_optimizers(self):
    return self._config.getboolean(self, 'switch_optimizers')
  @property
  def highway(self):
    return self._config.getboolean(self, 'highway')
  @property
  def print_every(self):
    return self._config.getint(self, 'print_every')
  @property
  def max_steps(self):
    return self._config.getint(self, 'max_steps')
  @property
  def max_steps_without_improvement(self):
    return self._config.getint(self, 'max_steps_without_improvement')