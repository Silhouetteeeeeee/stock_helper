from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Stock:
    code: str
    name: str
    prefix: str
    roe3: Optional[np.float64] = None
    zcfzl: Optional[np.float64] = None
    ldbl: Optional[np.float64] = None
    sdbl: Optional[np.float64] = None
    xjl_jlr_ratio: Optional[np.float64] = None
