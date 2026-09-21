"""Source registry: board kind -> adapter class."""
from ..resolve import Board
from .ats import Amazon, Ashby, Eightfold, Greenhouse, Jibe, Lever, Oracle, SmartRecruiters, Workday
from .base import Adapter, Job
from .jsonld import JsonLD
from .lists import GitHubList

ADAPTERS = {cls.kind: cls for cls in (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Oracle, Eightfold, Jibe,
                                     Amazon, JsonLD)}


def make_adapter(board: Board) -> Adapter:
    return ADAPTERS[board.kind](board)


__all__ = ["ADAPTERS", "Adapter", "GitHubList", "Job", "make_adapter"]
