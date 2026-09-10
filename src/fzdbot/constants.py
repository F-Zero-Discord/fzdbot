from typing import Final
from functools import lru_cache

class TimeConstants():
    def __init__(self):
        self.MIN_MINUTES: Final[int] = 0
        self.MAX_MINUTES: Final[int] = 5
        self.MIN_SECONDS: Final[int] = 0
        self.MAX_SECONDS: Final[int] = 59
        self.MIN_CENTISECONDS: Final[int] = 0
        self.MAX_CENTISECONDS: Final[int] = 99


class ScoreConstants():
    def __init__(self):
        self.MIN_SCORE: Final[int] = 0
        self.MAX_SCORE: Final[int] = 1000000  # arbitrarily set for now


class RankConstants():
    def __init__(self):
        self.MIN_RANK: Final[int] = 1
        self.MAX_RANK: Final[int] = 99

AUTOCOMPLETE_CACHE_SECONDS: Final[int] = 10 # time in seconds
AUTOASSIGN_LINEUP_WINDOW: Final[int] = 2100 # time in seconds. Note 2100/60 = 35 min
    
@lru_cache
def get_time_constants() -> TimeConstants:
    return TimeConstants()  # type: ignore


@lru_cache
def get_score_constants() -> TimeConstants:
    return ScoreConstants()  # type: ignore


@lru_cache
def get_rank_constants() -> TimeConstants:
    return RankConstants()  # type: ignore