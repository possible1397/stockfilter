"""
統一 logging 設定模組
用法:
    from utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("訊息")
"""
import logging
import logging.handlers
import os
import sys
from typing import Optional


def setup_logging(log_dir: str = "logs", level: str = "INFO",
                  max_bytes: int = 10 * 1024 * 1024, backup_count: int = 5):
    """
    初始化全域 logging 設定 (console + rotating file handler)。
    應在程式進入點呼叫一次。
    """
    os.makedirs(log_dir, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    if root.handlers:
        return  # 已初始化，不重複設定

    root.setLevel(numeric_level)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Rotating file handler
    log_file = os.path.join(log_dir, "scanner.log")
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh.setLevel(numeric_level)
    fh.setFormatter(fmt)
    root.addHandler(fh)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """取得指定名稱的 logger，若 logging 尚未初始化則以預設值初始化。"""
    root = logging.getLogger()
    if not root.handlers:
        setup_logging()
    return logging.getLogger(name)
