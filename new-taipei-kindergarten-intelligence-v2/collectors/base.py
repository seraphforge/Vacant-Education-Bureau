from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CrawlResult:
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)


class BaseCollector(ABC):
    """Base class for all data collectors."""

    @property
    @abstractmethod
    def crawler_name(self) -> str:
        pass

    @abstractmethod
    def collect(self, **kwargs) -> CrawlResult:
        pass

    def log_start(self, district: Optional[str] = None) -> None:
        from utils.logging import get_logger

        logger = get_logger(self.__class__.__name__)
        msg = f"Starting {self.crawler_name}"
        if district:
            msg += f" district={district}"
        logger.info(msg)

    def log_finish(self, result: CrawlResult, district: Optional[str] = None) -> None:
        from utils.logging import get_logger

        logger = get_logger(self.__class__.__name__)
        msg = (
            f"Finished {self.crawler_name} "
            f"total={result.total} success={result.success} "
            f"failed={result.failed} skipped={result.skipped}"
        )
        if district:
            msg += f" district={district}"
        logger.info(msg)
