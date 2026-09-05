from dataclasses import dataclass, field
from uuid import uuid4

TERMINAL_STATES = {"Completed", "Failed", "Cancelled"}


@dataclass
class DownloadItem:
    url: str
    format: str
    quality: str
    output_dir: str
    id: str = field(default_factory=lambda: uuid4().hex)
    title: str = "YouTube video"
    state: str = "Waiting"
    stage: str = "Ready when you are"
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0
    eta: float | None = None
    error: str = ""
    warning: str = ""
    filename: str = ""
    actual_quality: str = ""
