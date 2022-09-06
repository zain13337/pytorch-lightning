# Copyright The PyTorch Lightning team.
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
"""Utilities used for collections."""
from abc import ABC
from functools import partial
from typing import Any, Callable, List, Tuple, Union

import numpy as np
import torch
from lightning_utilities.core.apply_func import apply_to_collection
from torch import Tensor

_BLOCKING_DEVICE_TYPES = ("cpu", "mps")


def to_dtype_tensor(
    value: Union[int, float, List[Union[int, float]]], dtype: torch.dtype, device: Union[str, torch.device]
) -> Tensor:
    return torch.tensor(value, dtype=dtype, device=device)


def from_numpy(value: np.ndarray, device: Union[str, torch.device]) -> Tensor:
    return torch.from_numpy(value).to(device)


CONVERSION_DTYPES: List[Tuple[Any, Callable[[Any, Any], Tensor]]] = [
    # bool -> uint8 as bool -> torch.bool triggers RuntimeError: Unsupported data type for NCCL process group
    (bool, partial(to_dtype_tensor, dtype=torch.uint8)),
    (int, partial(to_dtype_tensor, dtype=torch.int)),
    (float, partial(to_dtype_tensor, dtype=torch.float)),
    (np.ndarray, from_numpy),
]


class TransferableDataType(ABC):
    """A custom type for data that can be moved to a torch device via ``.to(...)``.

    Example:

        >>> isinstance(dict, TransferableDataType)
        False
        >>> isinstance(torch.rand(2, 3), TransferableDataType)
        True
        >>> class CustomObject:
        ...     def __init__(self):
        ...         self.x = torch.rand(2, 2)
        ...     def to(self, device):
        ...         self.x = self.x.to(device)
        ...         return self
        >>> isinstance(CustomObject(), TransferableDataType)
        True
    """

    @classmethod
    def __subclasshook__(cls, subclass: Any) -> Union[bool, Any]:
        if cls is TransferableDataType:
            to = getattr(subclass, "to", None)
            return callable(to)
        return NotImplemented


def move_data_to_device(batch: Any, device: Union[str, torch.device]) -> Any:
    """Transfers a collection of data to the given device. Any object that defines a method ``to(device)`` will be
    moved and all other objects in the collection will be left untouched.

    Args:
        batch: A tensor or collection of tensors or anything that has a method ``.to(...)``.
            See :func:`apply_to_collection` for a list of supported collection types.
        device: The device to which the data should be moved

    Return:
        the same collection but with all contained tensors residing on the new device.

    See Also:
        - :meth:`torch.Tensor.to`
        - :class:`torch.device`
    """

    if isinstance(device, str):
        device = torch.device(device)

    def batch_to(data: Any) -> Any:
        kwargs = {}
        # Don't issue non-blocking transfers to CPU
        # Same with MPS due to a race condition bug: https://github.com/pytorch/pytorch/issues/83015
        if isinstance(data, Tensor) and isinstance(device, torch.device) and device.type not in _BLOCKING_DEVICE_TYPES:
            kwargs["non_blocking"] = True
        data_output = data.to(device, **kwargs)
        if data_output is not None:
            return data_output
        # user wrongly implemented the `TransferableDataType` and forgot to return `self`.
        return data

    return apply_to_collection(batch, dtype=TransferableDataType, function=batch_to)


def convert_to_tensors(data: Any, device: Union[str, torch.device]) -> Any:
    for src_dtype, conversion_func in CONVERSION_DTYPES:
        data = apply_to_collection(data, src_dtype, conversion_func, device=device)

    def _move_to_device_and_make_contiguous(t: Tensor, device: Union[str, torch.device]) -> Tensor:
        return t.to(device).contiguous()

    data = apply_to_collection(data, Tensor, _move_to_device_and_make_contiguous, device=device)
    return data