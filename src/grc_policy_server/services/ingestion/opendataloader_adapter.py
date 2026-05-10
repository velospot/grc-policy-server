from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

import opendataloader_pdf

logger = logging.getLogger(__name__)


class OpenDataLoaderAdapter:
    """Wraps opendataloader-pdf's Java CLI to convert PDF bytes → element list.

    OPD's API is file-path only, so we write to a temp file, run the JAR,
    read the JSON output, then clean up.
    """

    def __init__(
        self,
        *,
        hybrid_url: str | None = None,
        timeout_sec: float = 180.0,
        hybrid_timeout_sec: float = 30.0,
    ) -> None:
        self.hybrid_url = hybrid_url
        self.timeout_sec = timeout_sec
        self.hybrid_timeout_sec = hybrid_timeout_sec
        if self.hybrid_url:
            if self._check_hybrid_reachability(self.hybrid_url):
                logger.info("opendataloader hybrid backend reachable at %s", self.hybrid_url)
            else:
                logger.warning(
                    "opendataloader hybrid backend at %s is not reachable — "
                    "hybrid mode disabled; using standard OPD extraction",
                    self.hybrid_url,
                )
                self.hybrid_url = None

    @staticmethod
    def _check_hybrid_reachability(url: str) -> bool:
        import urllib.request
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:
            return False

    def convert_bytes(self, *, filename: str, content: bytes) -> list[dict]:
        """Convert PDF bytes to a flat list of OPD element dicts.

        The list is the ``kids`` array from the OPD JSON output, flattened
        from any wrapper containers (text blocks, headers, footers).

        Raises RuntimeError if OPD produces no usable elements.
        """
        stem = Path(filename).stem or "document"

        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, f"{stem}.pdf")
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir)

            Path(in_path).write_bytes(content)

            kwargs: dict = {
                "input_path": in_path,
                "output_dir": out_dir,
                "format": "json",
                "quiet": True,
                "image_output": "off",
            }
            if self.hybrid_url:
                kwargs["hybrid"] = "docling-fast"
                kwargs["hybrid_url"] = self.hybrid_url
                kwargs["hybrid_fallback"] = True
                kwargs["hybrid_timeout"] = str(int(self.hybrid_timeout_sec))

            logger.info(
                "opendataloader converting filename=%s hybrid=%s",
                filename,
                bool(self.hybrid_url),
            )
            opendataloader_pdf.convert(**kwargs)

            out_json = os.path.join(out_dir, f"{stem}.json")
            if not os.path.exists(out_json):
                # OPD may produce the file under a sanitized name; find any .json
                found = [f for f in os.listdir(out_dir) if f.endswith(".json")]
                if not found:
                    raise RuntimeError(
                        f"opendataloader produced no JSON output for {filename}"
                    )
                out_json = os.path.join(out_dir, found[0])

            data = json.loads(Path(out_json).read_text(encoding="utf-8"))

        elements = data.get("kids") or []
        if not elements:
            raise RuntimeError(
                f"opendataloader returned empty element list for {filename}"
            )

        logger.info(
            "opendataloader finished filename=%s pages=%s elements=%s",
            filename,
            data.get("number of pages"),
            len(elements),
        )
        return elements
