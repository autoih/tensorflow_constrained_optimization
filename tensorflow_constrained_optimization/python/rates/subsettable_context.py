# Copyright 2018 The TensorFlow Constrained Optimization Authors. All Rights
# Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
# ==============================================================================
"""Contains the `rate_context` and `split_rate_context` functions.

A rate constraints problem is constructed as an objective function and set of
constraints, all of which are represented as `Expression`s, each of which
represents a linear combination of `Term`s (which represent rates) and
`Tensor`s.

Rates are things like the error rate, the false positive rate, and so on. But we
can't just say "minimize the error rate" without qualification. The question is:
"the error rate *of what*". A "context" is the "what".

A context represents the model predictions, and optional labels and example
weights for a certain (subset of a) dataset. One can take subsets of contexts
using the "subset" method.

Subsetting is convenient, but it comes at a cost. You should use subsetting
*with great caution*. If, for example, you wish to create a rate only on the set
of "blue" examples, then it will almost always be better (but more complicated)
to create an entirely separate dataset containing only "blue" examples (e.g.
using the "filter" method of a `tf.data.Dataset`), rather than taking the "blue"
subset of a dataset that also contains "red" and "green" examples.

The reason for this is that, if using subsetting, each minibatch will contain
varying numbers of "blue" examples during training. As a consequence, we'll
sometimes perform too-small updates, and sometimes overcorrect with extremely
large updates. This problem is less serious if "blue" examples are common, but
can be fatal if "blue" examples are extremely rare.

If, instead of subsetting, we were to create an entirely separate "blue"
dataset, then every minibatch would contain the same number of "blue" examples,
and optimization would proceed more smoothly.

One can also create what we call a "split context", which has separate
predictions, labels, weights and subset for the "penalty" and "constraint"
portions of the problem. This is an advanced option, and is not needed in most
circumstances.

The `rate_context` helper function is used to create a (non-split) context, and
`split_rate_context` to create a split context.

Example
=======

Suppose that "examples_tensor" and "labels_tensor" are placeholder `Tensor`s
containing training examples and their associated labels, and the "model"
function evaluates the model you're trying to train, returning a `Tensor` of
model evaluations. Then the following code will create a list of constraints
forcing the error rate on "blue" examples to be between 90% and 110% of the
error rate on non-"blue" examples:

>>> ctx = rate_context(model(examples_tensor), labels_tensor)
>>> blue_ctx = ctx.subset(examples_tensor[:, is_blue_idx] > 0)
>>> non_blue_ctx = ctx.subset(examples_tensor[:, is_blue_idx] <= 0)
>>> constraints = [
>>>     error_rate(blue_ctx) >= 0.9 * error_rate(non_blue_ctx),
>>>     error_rate(blue_ctx) <= 1.1 * error_rate(non_blue_ctx)
>>> ]

Here, "error_rate" is as defined in rates.py. The list of constraints can be
passed on to the "RateMinimizationProblem" class in
rate_minimization_problem.py.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from tensorflow_constrained_optimization.python.rates import helpers


class _RawContext(object):
  """Helper class containing model predictions, example labels and weights.

  Every rate in a rate constraints problem is based on six quantities: the model
  predictions f(x_i), along with the associated labels y_i and weights w_i, for
  two sets of examples, one associated with the penalty portion of the problem,
  and the other with the constraint portion (usually, the penalty and constraint
  portions will be the same, but this is not always the case).

  A `_RawContext` is just a convenience wrapper around these six quantities, and
  is used by a `SubsettableContext', which adds the ability to choose a *subset*
  of the examples (separately for the penalty and constraint portions, if
  necessary).
  """

  def __init__(self, penalty_predictions, penalty_labels, penalty_weights,
               constraint_predictions, constraint_labels, constraint_weights):
    """Creates a new `_RawContext`.

    Args:
      penalty_predictions: rank-1 floating-point `Tensor`, for which the ith
        element is the output of the model on the ith training example, for the
        training dataset associated with the penalties.
      penalty_labels: optional rank-1 `Tensor`, for which the ith element is the
        label of the ith training example, for the training dataset associated
        with the penalties.
      penalty_weights: rank-1 floating-point `Tensor`, for which the ith element
        is the weight of the ith training example, for the training dataset
        associated with the penalties.
      constraint_predictions: rank-1 floating-point `Tensor`, for which the ith
        element is the output of the model on the ith training example, for the
        training dataset associated with the constraints.
      constraint_labels: optional rank-1 `Tensor`, for which the ith element is
        the label of the ith training example, for the training dataset
        associated with the constraints.
      constraint_weights: rank-1 floating-point `Tensor`, for which the ith
        element is the weight of the ith training example, for the training
        dataset associated with the constraints.
    """
    self._penalty_predictions = penalty_predictions
    self._penalty_labels = penalty_labels
    self._penalty_weights = penalty_weights
    self._constraint_predictions = constraint_predictions
    self._constraint_labels = constraint_labels
    self._constraint_weights = constraint_weights

  def __eq__(self, other):
    """Returns True if two `_RawContext`s are equal."""
    if not isinstance(other, _RawContext):
      return False
    attr_names = [
        "penalty_predictions", "penalty_labels", "penalty_weights",
        "constraint_predictions", "constraint_labels", "constraint_weights"
    ]
    return all(
        helpers.tensors_equal(
            getattr(self, attr_name), getattr(other, attr_name))
        for attr_name in attr_names)

  @property
  def penalty_predictions(self):
    """Accessor for the predictions `Tensor` associated with the penalties."""
    return self._penalty_predictions

  @property
  def penalty_labels(self):
    """Accessor for the labels `Tensor` associated with the penalties.

    The labels are permitted to be absent, in which case this method will return
    None.

    Returns:
      `Tensor` of labels associated with the penalties, or None if there are no
      such labels.
    """
    return self._penalty_labels

  @property
  def penalty_weights(self):
    """Accessor for the weights `Tensor` associated with the penalties."""
    return self._penalty_weights

  @property
  def constraint_predictions(self):
    """Accessor for the predictions `Tensor` associated with the constraints."""
    return self._constraint_predictions

  @property
  def constraint_labels(self):
    """Accessor for the labels `Tensor` associated with the constraints.

    The labels are permitted to be absent, in which case this method will return
    None.

    Returns:
      `Tensor` of labels associated with the constraints, or None if there are
      no such labels.
    """
    return self._constraint_labels

  @property
  def constraint_weights(self):
    """Accessor for the weights `Tensor` associated with the constraints."""
    return self._constraint_weights


class SubsettableContext(object):
  """Represents a subset of model predictions, example labels and weights.

  Every rate in a rate constraints problem is calculated over a (weighted) set
  of model predictions. Each such set of predictions is evaluated on a set of
  training examples, each of which (optionally) has an associated label and
  weight. This class represents a set of such predictions, labels and weights.

  Many commonly-used rates (e.g. the false positive rate) are actually
  calculated only on a *subset* of the examples (for the false positive rate,
  this subset is the set of negatively-labeled examples). For this reason,
  `SubsettableContext`s support subsetting (via the "subset") method, along with
  logical operators for combining subsets. Subsets can *only* be combined if
  they are themselves subsets of the same base context.

  In addition, a `SubsettableContext` supports using *different* sets of
  predictions, labels and weights for the portion of the problem associated with
  the penalty, and that associated with the constraints. Such a context is
  called a "split context". It can be used just like a normal context, except
  that the "subset" method expects *two* arguments: one for the penalty portion,
  and one for the constraint portion.
  """

  def _check_compatibility(self, other):
    """Raises if two contexts cannot be combined using logical operations.

    Two contexts can be combined with a logical operation (AND or OR) only if
    they are based on the same `_RawContext` (i.e. have the same predictions,
    labels and weights).

    Args:
      other: `SubsettableContext` that we wish to combine with "self" using a
        logical operation.

    Raises:
      TypeError: if "other" is not a `SubsettableContext`.
      ValueError: if "other" cannot be combined with "self" using a logical
        operation.
    """
    if not isinstance(other, SubsettableContext):
      raise TypeError("logical operations can only be used to combine "
                      "SubsettableContexts with other SubsettableContexts")
    if self._raw_context != other.raw_context:
      raise ValueError("contexts can only be combined if they're based on the "
                       "same predictions, labels and weights")

  def __init__(self, raw_context, penalty_predicate, constraint_predicate):
    """Creates a new `SubsettableContext`.

    Args:
      raw_context: `_RawContext`, the raw context containing the predictions,
        labels and weights.
      penalty_predicate: `Predicate`, the predicate representing the subset
        associated with the penalty.
      constraint_predicate: `Predicate`, the predicate representing the subset
        associated with the constraints.
    """
    self._raw_context = raw_context
    self._penalty_predicate = penalty_predicate
    self._constraint_predicate = constraint_predicate

  @property
  def raw_context(self):
    """Accessor for `_RawContext` object subsetted by this object."""
    return self._raw_context

  @property
  def penalty_predicate(self):
    """Accessor for the penalty `Predicate`."""
    return self._penalty_predicate

  @property
  def constraint_predicate(self):
    """Accessor for the constraint `Predicate`."""
    return self._constraint_predicate

  def subset(self, penalty_predicate, constraint_predicate=None):
    """Returns a subset of this context.

    The two predicates should be boolean `Tensor`s of the same size as the
    predictions `Tensor` from which the top-level context was constructed. If an
    element of the predicate `Tensor` is True, and the corresponding example is
    included in this context, then the example will be included in the resulting
    context. Otherwise, it will not.

    A "split context" contains two sets of predictions (and optionally labels
    and weights). When subsetting a split context, two predicates must be
    provided to this method: the first for the penalty portion, and the second
    for the constraint portion. Alternatively, if you want to create a split
    context from a non-split one, then you can do so by providing both predicate
    arguments explicitly.

    This method is here for convenience, but it comes at a cost. You should use
    subsetting *with great caution*. If, for example, you wish to create a rate
    only on the set of "blue" examples, then it will almost always be better
    (but more complicated) to create an entirely separate dataset containing
    only "blue" examples (e.g. using the "filter" method of a
    `tf.data.Dataset`), rather than taking the "blue" subset of a dataset that
    also contains "red" and "green" examples.

    The reason for this is that, if using subsetting, each minibatch will
    contain varying numbers of "blue" examples during training. As a
    consequence, we'll sometimes perform too-small updates, and sometimes
    overcorrect with extremely large updates. This problem is less serious if
    "blue" examples are common, but can be fatal if "blue" examples are
    extremely rare.

    If, instead of subsetting, we were to create an entirely separate "blue"
    dataset, then every minibatch would contain the same number of "blue"
    examples, and optimization would proceed more smoothly.

    Args:
      penalty_predicate: boolean `Tensor` with the size as the underlying
        predictions tensor (or broadcastable to it), each element of which
        indicates whether the corresponding example should be included in the
        subset.
      constraint_predicate: optional boolean `Tensor`, playing the same role as
        "penalty_predicate", but for the constraints portion of the context.

    Returns:
      `SubsettableContext` representing the subset of this context on which
      penalty_predicate (and constraint_predicate, if applicable) are True.

    Raises:
      ValueError: if no constraint_predicate is provided, but this is a split
        context.
    """
    if constraint_predicate is None:
      # It's fine if the labels and/or weights are different.
      if (self._raw_context.penalty_predictions !=
          self._raw_context.constraint_predictions or
          self._penalty_predicate != self._constraint_predicate):
        raise ValueError("constraint_predicate must be provided when "
                         "subsetting a split context")
      constraint_predicate = penalty_predicate

    # Convert the boolean predicates to Predicate objects. Make sure that we
    # don't change from a non-split context (both predicates are the same
    # object) to a split context (the predicates are different objects) unless
    # it's necessary.
    if helpers.tensors_equal(penalty_predicate, constraint_predicate):
      penalty_predicate = helpers.Predicate(penalty_predicate)
      constraint_predicate = penalty_predicate
    else:
      penalty_predicate = helpers.Predicate(penalty_predicate)
      constraint_predicate = helpers.Predicate(constraint_predicate)

    return SubsettableContext(
        raw_context=self._raw_context,
        penalty_predicate=self._penalty_predicate & penalty_predicate,
        constraint_predicate=self._constraint_predicate & constraint_predicate)

  def __and__(self, other):
    """Returns a context representing the result of ANDing the arguments.

    Args:
      other: `SubsettableContext` to AND with this context.

    Returns:
      `SubsettableContext` resulting from ANDing the arguments.
    """
    self._check_compatibility(other)

    # AND the predicates together for "self" and "other". Make sure that we
    # don't change from a non-split context (both predicates are the same
    # object) to a split context (the predicates are different objects) unless
    # it's necessary.
    if (self._penalty_predicate == self._constraint_predicate and
        other.penalty_predicate == other.constraint_predicate):
      penalty_predicate = self._penalty_predicate & other.penalty_predicate
      constraint_predicate = penalty_predicate
    else:
      penalty_predicate = self._penalty_predicate & other.penalty_predicate
      constraint_predicate = (
          self._constraint_predicate & other.constraint_predicate)

    return SubsettableContext(
        raw_context=self._raw_context,
        penalty_predicate=penalty_predicate,
        constraint_predicate=constraint_predicate)

  def __or__(self, other):
    """Returns a context representing the result of ORing the arguments.

    Args:
      other: `SubsettableContext` to OR with this context.

    Returns:
      `SubsettableContext` resulting from ORing the arguments.
    """
    self._check_compatibility(other)

    # OR the predicates together for "self" and "other". Make sure that we don't
    # change from a non-split context (both predicates are the same object) to a
    # split context (the predicates are different objects) unless it's
    # necessary.
    if (self._penalty_predicate == self._constraint_predicate and
        other.penalty_predicate == other.constraint_predicate):
      penalty_predicate = self._penalty_predicate | other.penalty_predicate
      constraint_predicate = penalty_predicate
    else:
      penalty_predicate = self._penalty_predicate | other.penalty_predicate
      constraint_predicate = (
          self._constraint_predicate | other.constraint_predicate)

    return SubsettableContext(
        raw_context=self._raw_context,
        penalty_predicate=penalty_predicate,
        constraint_predicate=constraint_predicate)


def rate_context(predictions, labels=None, weights=1.0):
  """Creates a new context.

  Args:
    predictions: rank-1 floating-point `Tensor`, for which the ith element is
      the output of the model on the ith training example.
    labels: optional rank-1 `Tensor`, for which the ith element is the label of
      the ith training example.
    weights: optional rank-1 floating-point `Tensor`, for which the ith element
      is the weight of the ith training example. If not specified, the weights
      default to being all-one.

  Returns:
    `SubsettableContext` representing the given predictions, labels and weights.
  """
  raw_context = _RawContext(
      penalty_predictions=predictions,
      penalty_labels=labels,
      penalty_weights=weights,
      constraint_predictions=predictions,
      constraint_labels=labels,
      constraint_weights=weights)
  true_predicate = helpers.Predicate(True)
  return SubsettableContext(raw_context, true_predicate, true_predicate)


def split_rate_context(penalty_predictions,
                       constraint_predictions,
                       penalty_labels=None,
                       constraint_labels=None,
                       penalty_weights=1.0,
                       constraint_weights=1.0):
  """Creates a new split context.

  A "split context", unlike a normal context, has separate predictions, labels,
  weights and subset for the "penalty" and "constraint" portions of the problem.
  This is an advanced option, and is not needed in most circumstances.

  Args:
    penalty_predictions: rank-1 floating-point `Tensor`, for which the ith
      element is the output of the model on the ith training example, for the
      training dataset associated with the penalties.
    constraint_predictions: rank-1 floating-point `Tensor`, for which the ith
      element is the output of the model on the ith training example, for the
      training dataset associated with the constraints.
    penalty_labels: optional rank-1 `Tensor`, for which the ith element is the
      label of the ith training example, for the training dataset associated
      with the penalties.
    constraint_labels: optional rank-1 `Tensor`, for which the ith element is
      the label of the ith training example, for the training dataset associated
      with the constraints.
    penalty_weights: optional rank-1 floating-point `Tensor`, for which the ith
      element is the weight of the ith training example, for the training
      dataset associated with the penalties. If not specified, the weights
      default to being all-one.
    constraint_weights: optional rank-1 floating-point `Tensor`, for which the
      ith element is the weight of the ith training example, for the training
      dataset associated with the constraints. If not specified, the weights
      default to being all-one.

  Returns:
    `SubsettableContext` representing the given predictions, labels and weights.
  """
  raw_context = _RawContext(
      penalty_predictions=penalty_predictions,
      penalty_labels=penalty_labels,
      penalty_weights=penalty_weights,
      constraint_predictions=constraint_predictions,
      constraint_labels=constraint_labels,
      constraint_weights=constraint_weights)
  true_predicate = helpers.Predicate(True)
  return SubsettableContext(raw_context, true_predicate, true_predicate)
