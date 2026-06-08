import logging
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def get_job_log_path(job_id: str, output_dir: Path) -> Path:
    resolved_output_dir = output_dir.expanduser().resolve()
    for candidate in (resolved_output_dir, *resolved_output_dir.parents):
        if (candidate / "video.json").is_file():
            return candidate / "logs" / f"{job_id}.log"
    return resolved_output_dir / "pipeline.log"


def get_job_logger(job_id: str, output_dir: Path) -> logging.Logger:
    log_path = get_job_log_path(job_id, output_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"insightcast.job.{job_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    target_path = str(log_path)
    matching_handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler)
        and str(Path(handler.baseFilename).resolve()) == target_path
    ]
    for existing_handler in list(logger.handlers):
        if (
            isinstance(existing_handler, logging.FileHandler)
            and existing_handler not in matching_handlers
        ):
            logger.removeHandler(existing_handler)
            existing_handler.close()
    if not matching_handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)
    return logger
