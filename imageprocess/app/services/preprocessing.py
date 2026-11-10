import io
import warnings
from dataclasses import dataclass
import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from app.core.config import Settings
from app.core.errors import ServiceError


@dataclass
class PreparedImage:
    image: Image.Image
    warnings: list[str]


def preprocess(content: bytes, settings: Settings) -> PreparedImage:
    # Inspect dimensions before allocating a decoded OpenCV image.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                if source.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ServiceError('INVALID_IMAGE', 'Only JPEG, PNG and WEBP images are supported.')
                if source.width * source.height > settings.max_image_pixels:
                    raise ServiceError('IMAGE_TOO_LARGE', 'Image exceeds the decoded pixel limit.', 413)
                source.verify()
            with Image.open(io.BytesIO(content)) as source:
                oriented = ImageOps.exif_transpose(source).convert('RGB')
                rgb = np.asarray(oriented)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ServiceError('IMAGE_TOO_LARGE', 'Image exceeds the safe pixel limit.', 413) from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise ServiceError('INVALID_IMAGE', 'File is not a valid supported image.') from None
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    if max(height, width) > settings.max_image_size:
        scale = settings.max_image_size / max(height, width)
        bgr = cv2.resize(bgr, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    flags = []
    if float(cv2.Laplacian(gray, cv2.CV_64F).var()) < settings.blur_threshold:
        flags.append('IMAGE_BLURRY')
    if float(gray.mean()) < settings.darkness_threshold:
        flags.append('IMAGE_DARK')
    if flags and settings.reject_low_quality:
        raise ServiceError('IMAGE_LOW_QUALITY', 'Image is blurry or dark; please retake the photograph.')
    return PreparedImage(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)), flags)
