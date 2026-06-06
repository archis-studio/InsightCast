import logging
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def get_job_logger(job_id: str, output_dir: Path) -> logging.Logger:
    resolved_output_dir = output_dir.expanduser().resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolved_output_dir / "pipeline.log"
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
