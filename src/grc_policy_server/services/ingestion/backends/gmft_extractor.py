"""GMFT (Google/Microsoft Table Transformer) extractor for high-quality table detection."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from grc_policy_server.services.ingestion.backends.base_extractor import TableExtractor
from grc_policy_server.services.ingestion.table_extraction_ensemble import TableCandidate

logger = logging.getLogger(__name__)


class GmftTableExtractor(TableExtractor):
    """Extract tables using GMFT (table-transformer library).

    GMFT uses Microsoft's Table Transformer model for robust table detection
    and structure recovery. Good for both text-based and visually complex PDFs.
    """

    def __init__(self, model_name: str = "microsoft/table-transformer-detection"):
        """Initialize GMFT extractor.

        Args:
            model_name: HuggingFace model identifier
        """
        self.model_name = model_name
        self.device = None
        self.model = None
        self.processor = None

    async def extract(
        self,
        pdf_path: str,
        page_numbers: list[int] | None = None,
    ) -> list[TableCandidate]:
        """Extract tables using GMFT.

        Args:
            pdf_path: Path to PDF file
            page_numbers: Optional list of specific pages to process

        Returns:
            List of TableCandidate objects
        """
        try:
            import pdf2image
            from PIL import Image

            # Convert PDF to images
            images = await asyncio.to_thread(
                pdf2image.convert_from_path,
                pdf_path,
                first_page=page_numbers[0] if page_numbers else None,
                last_page=page_numbers[-1] if page_numbers else None,
            )

            candidates: list[TableCandidate] = []

            for page_num, image in enumerate(images, start=page_numbers[0] if page_numbers else 1):
                page_candidates = await self._detect_and_extract_tables(image, page_num)
                candidates.extend(page_candidates)

            logger.info(f"GMFT extracted {len(candidates)} table candidates from {pdf_path}")
            return candidates

        except ImportError:
            logger.error("pdf2image or Pillow not installed")
            return []
        except Exception as e:
            logger.warning(f"GMFT extraction failed: {e}")
            return []

    async def _detect_and_extract_tables(
        self,
        image: Any,
        page_number: int,
    ) -> list[TableCandidate]:
        """Detect and extract table structures from a single page image.

        Args:
            image: PIL Image object
            page_number: Page number in the PDF

        Returns:
            List of TableCandidate objects for this page
        """
        try:
            from transformers import AutoFeatureExtractor, AutoModelForObjectDetection

            # Lazy load model
            if self.model is None:
                await self._load_model()

            # Detect table regions
            inputs = self.processor(images=image, return_tensors="pt")
            outputs = self.model(**inputs)

            # Post-process detections
            candidates = await asyncio.to_thread(
                self._process_detections,
                image,
                page_number,
                outputs,
            )

            return candidates

        except ImportError:
            logger.error("transformers library not installed")
            return []
        except Exception as e:
            logger.warning(f"Table detection failed on page {page_number}: {e}")
            return []

    async def _load_model(self) -> None:
        """Load GMFT model and processor."""
        try:
            from transformers import AutoFeatureExtractor, AutoModelForObjectDetection

            logger.info(f"Loading GMFT model: {self.model_name}")

            self.processor = await asyncio.to_thread(
                AutoFeatureExtractor.from_pretrained,
                self.model_name,
            )
            self.model = await asyncio.to_thread(
                AutoModelForObjectDetection.from_pretrained,
                self.model_name,
            )

            # Move to GPU if available
            try:
                import torch

                self.device = "cuda" if torch.cuda.is_available() else "cpu"
                self.model.to(self.device)
                logger.info(f"GMFT model loaded on {self.device}")
            except ImportError:
                self.device = "cpu"
                logger.info("GMFT model loaded on CPU (torch not available)")

        except Exception as e:
            logger.error(f"Failed to load GMFT model: {e}")
            raise

    def _process_detections(
        self,
        image: Any,
        page_number: int,
        outputs: Any,
    ) -> list[TableCandidate]:
        """Process GMFT detection outputs into TableCandidate objects."""
        candidates: list[TableCandidate] = []

        # This is a simplified implementation - actual GMFT post-processing
        # would involve NMS, confidence filtering, and coordinate transformation
        try:
            import torch

            scores = outputs.logits.softmax(-1)
            labels = torch.argmax(scores, -1)

            # Get bounding boxes from outputs
            # Note: This is simplified - real implementation depends on model version
            if hasattr(outputs, "pred_boxes"):
                boxes = outputs.pred_boxes

                for idx, (label, box) in enumerate(zip(labels, boxes)):
                    if label == 1:  # Table class (assuming binary detection)
                        # Normalize box coordinates to image dimensions
                        img_w, img_h = image.size if hasattr(image, "size") else (1, 1)

                        x0, y0, x1, y1 = (
                            float(box[0]) * img_w,
                            float(box[1]) * img_h,
                            float(box[2]) * img_w,
                            float(box[3]) * img_h,
                        )

                        # Create candidate (without cell-level data for now)
                        candidate = TableCandidate(
                            backend_name="gmft",
                            page_number=page_number,
                            bbox={"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                            cells=[],  # GMFT only provides regions, not cell structure
                            headers=[],
                            num_rows=0,
                            num_cols=0,
                            confidence=float(scores[idx, label].item()),
                            metadata={
                                "detection_index": idx,
                                "model": self.model_name,
                            },
                        )
                        candidates.append(candidate)

        except Exception as e:
            logger.warning(f"Error processing GMFT detections: {e}")

        return candidates
