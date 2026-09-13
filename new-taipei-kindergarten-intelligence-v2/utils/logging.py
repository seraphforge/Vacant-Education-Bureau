import logging
import sys
from pathlib import Path
from typing import Optional

_initialized = False

def setup_logging(log_dir: Optional[Path] = None) -> None:
    global _initialized
    if _initialized:
        return
    
    if log_dir is None:
        from config.settings import settings
        log_dir = settings.LOGS_DIR
    
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # Console handler (INFO+)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root_logger.addHandler(console)
    
    # pipeline.log (INFO+)
    fh_pipeline = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
    fh_pipeline.setLevel(logging.INFO)
    fh_pipeline.setFormatter(fmt)
    root_logger.addHandler(fh_pipeline)
    
    # errors.log (ERROR+)
    fh_errors = logging.FileHandler(log_dir / "errors.log", encoding="utf-8")
    fh_errors.setLevel(logging.ERROR)
    fh_errors.setFormatter(fmt)
    root_logger.addHandler(fh_errors)
    
    # matching.log (INFO+) for matching logger
    matching_logger = logging.getLogger("matching")
    fh_matching = logging.FileHandler(log_dir / "matching.log", encoding="utf-8")
    fh_matching.setLevel(logging.DEBUG)
    fh_matching.setFormatter(fmt)
    matching_logger.addHandler(fh_matching)
    
    _initialized = True

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
