"""The common shape every source returns."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..resolve import Board


@dataclass
class Job:
    key: str                      # stable id, e.g. "gh:8171041" or "wd:nvidia:jr1234"
    company: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    posted: str | None = None     # YYYY-MM-DD
    description: str | None = None
    needs_detail: bool = False    # description/locations need a second request
    extra: dict = field(default_factory=dict)


class Adapter:
    kind = ""
    direct = True                 # a company's own board (vs. a community list)

    def __init__(self, board: Board):
        self.board = board

    @property
    def label(self) -> str:
        return self.board.name

    def list_jobs(self) -> list[Job]:
        raise NotImplementedError

    def detail(self, job: Job) -> tuple[str, list[str]]:
        """-> (plain-text description, extra locations). Default: nothing more to fetch."""
        return job.description or "", []


def queries(board: Board, default=("intern", "co-op")) -> list[str]:
    q = board.opts.get("queries")
    return list(q) if q else list(default)
