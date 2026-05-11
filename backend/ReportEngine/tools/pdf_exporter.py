"""Optional PDF exporter."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class PdfExporter:
    def export(self, html: str, target_path: Path) -> Optional[Path]:
        try:
            from weasyprint import HTML
        except Exception:
            return None
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            HTML(string=html).write_pdf(str(target_path))
            return target_path
        except Exception:
            return None

