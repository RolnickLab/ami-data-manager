from typing import Union

import pathlib
from dataclasses import dataclass


@dataclass
class CoordinateDMS:
    degrees: int
    minutes: int
    seconds: float


@dataclass
class Location:
    latitude: CoordinateDMS
    longitude: CoordinateDMS


# @dataclass
# class BoundingBox:
#     top_left: float
#     top_right: float
#     bottom_left: float
#     bottom_right: float

# [x1, y1, x2, y2] The origin is top-left corner; x1<x2; y1<y2; integer values in the list
BoundingBox = tuple[float, float, float, float]

SystemPath = Union[pathlib.Path, str]
OptionalSystemPath = Union[pathlib.Path, str, None]
