"""Read-only raster inspection for engineering drawing callouts and previews."""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from PIL import ImageDraw

from .protocol import ErrorCode, ProtocolError


MAX_PIXELS = 32_000_000
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
FORMATS = {"PNG", "JPEG", "WEBP"}


def _error(code: ErrorCode, message: str, *, details: dict[str, Any] | None = None) -> ProtocolError:
    return ProtocolError(code, message, details=details or {})


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def inspect_image(root: str | Path, relative: str, region: list[int] | tuple[int, ...] | None = None,
                  max_size: int = 1024, rotate: int = 0) -> dict[str, Any]:
    """Inspect a raster image without modifying it.

    ``region`` uses displayed source pixels as ``[left, top, right, bottom]``.
    The returned preview is always a PNG on a white background, with the source
    hash and coordinate transform recorded for downstream visual reasoning.
    """
    project_root = Path(root).resolve()
    source = (project_root / relative).resolve()
    if not _inside(project_root, source):
        raise _error(ErrorCode.PATH_OUTSIDE_ROOT, "image path is outside project root", details={"path": str(relative)})
    if not source.is_file():
        raise _error(ErrorCode.RESOURCE_NOT_FOUND, f"image not found: {relative}", details={"path": str(relative)})
    if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise _error(ErrorCode.BAD_IMAGE_FORMAT, "expected a PNG, JPEG or WebP image", details={"path": str(relative)})
    if isinstance(max_size, bool) or not isinstance(max_size, int) or not 64 <= max_size <= 2048:
        raise _error(ErrorCode.BAD_IMAGE_SIZE, "max_size must be an integer from 64 to 2048")
    if isinstance(rotate, bool) or not isinstance(rotate, int) or rotate not in (0, 90, 180, 270):
        raise _error(ErrorCode.BAD_IMAGE_ROTATION, "rotate must be 0, 90, 180 or 270 clockwise degrees")
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise _error(ErrorCode.IMAGE_TOO_LARGE, "source image exceeds the byte budget", details={"bytes": source.stat().st_size})

    raw = source.read_bytes()
    try:
        with Image.open(BytesIO(raw)) as opened:
            if opened.format not in FORMATS:
                raise _error(ErrorCode.BAD_IMAGE_FORMAT, "expected a PNG, JPEG or WebP image")
            if opened.width * opened.height > MAX_PIXELS:
                raise _error(ErrorCode.IMAGE_TOO_LARGE, f"source image exceeds {MAX_PIXELS} pixels")
            # EXIF orientation is applied before coordinates are reported so
            # the agent works in the same orientation it sees on screen.
            image = ImageOps.exif_transpose(opened).convert("RGBA")
            width, height = image.size
            box = [0, 0, width, height] if region is None else list(region)
            if (len(box) != 4 or any(isinstance(value, bool) or not isinstance(value, int) for value in box)):
                raise _error(ErrorCode.BAD_IMAGE_REGION, "region must be [left, top, right, bottom] in source pixels")
            left, top, right, bottom = box
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise _error(ErrorCode.BAD_IMAGE_REGION, f"region must be nonempty and inside {width} x {height}")
            rgba = image.crop((left, top, right, bottom))
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            crop = background.convert("RGB")
            if rotate:
                crop = crop.transpose({90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                                       270: Image.Transpose.ROTATE_90}[rotate])
            scale = max_size / max(crop.size)
            preview_size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
            preview = crop.resize(preview_size, Image.Resampling.LANCZOS)
            stream = BytesIO()
            preview.save(stream, format="PNG")
            png = stream.getvalue()
    except ProtocolError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise _error(ErrorCode.IMAGE_READ_FAILED, f"cannot read image: {exc}") from exc

    if len(png) > MAX_PREVIEW_BYTES:
        raise _error(ErrorCode.IMAGE_PREVIEW_TOO_LARGE, "preview exceeds 2 MB", details={"bytes": len(png)})
    return {
        "source": source.relative_to(project_root).as_posix(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_size": [width, height],
        "region": box,
        "rotate": rotate,
        "preview_size": list(preview_size),
        "pixel_scale": scale,
        "mime_type": "image/png",
        "image_base64": base64.b64encode(png).decode("ascii"),
        "schema_version": "simloom-ai/image-inspection/v1",
    }


def measure_image(root: str | Path, relative: str, *, region: list[int] | tuple[int, ...] | None = None,
                  target_color: list[int] | tuple[int, ...] | None = None, tolerance: int = 16,
                  alpha_threshold: int = 1, max_size: int = 1024, overlay: bool = True) -> dict[str, Any]:
    """Measure a color region in source pixel coordinates without writing files.

    This is a deterministic, safe foundation for drawing callout checks: the
    caller supplies a target RGB color and tolerance, and SimLoom returns the
    matching-pixel count, bounds, centroid and an optional annotated preview.
    """
    project_root = Path(root).resolve()
    source = (project_root / relative).resolve()
    if not _inside(project_root, source):
        raise _error(ErrorCode.PATH_OUTSIDE_ROOT, "image path is outside project root", details={"path": str(relative)})
    if not source.is_file():
        raise _error(ErrorCode.RESOURCE_NOT_FOUND, f"image not found: {relative}", details={"path": str(relative)})
    if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise _error(ErrorCode.BAD_IMAGE_FORMAT, "expected a PNG, JPEG or WebP image", details={"path": str(relative)})
    if target_color is None or len(target_color) != 3 or any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255 for value in target_color):
        raise _error(ErrorCode.BAD_IMAGE_COLOR, "target_color must be [r, g, b] with values from 0 to 255")
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or not 0 <= tolerance <= 255:
        raise _error(ErrorCode.BAD_IMAGE_TOLERANCE, "tolerance must be an integer from 0 to 255")
    if isinstance(alpha_threshold, bool) or not isinstance(alpha_threshold, int) or not 0 <= alpha_threshold <= 255:
        raise _error(ErrorCode.BAD_IMAGE_TOLERANCE, "alpha_threshold must be an integer from 0 to 255")
    if isinstance(max_size, bool) or not isinstance(max_size, int) or not 64 <= max_size <= 2048:
        raise _error(ErrorCode.BAD_IMAGE_SIZE, "max_size must be an integer from 64 to 2048")
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise _error(ErrorCode.IMAGE_TOO_LARGE, "source image exceeds the byte budget", details={"bytes": source.stat().st_size})
    raw = source.read_bytes()
    try:
        with Image.open(BytesIO(raw)) as opened:
            if opened.format not in FORMATS:
                raise _error(ErrorCode.BAD_IMAGE_FORMAT, "expected a PNG, JPEG or WebP image")
            if opened.width * opened.height > MAX_PIXELS:
                raise _error(ErrorCode.IMAGE_TOO_LARGE, f"source image exceeds {MAX_PIXELS} pixels")
            image = ImageOps.exif_transpose(opened).convert("RGBA")
            width, height = image.size
            box = [0, 0, width, height] if region is None else list(region)
            if len(box) != 4 or any(isinstance(value, bool) or not isinstance(value, int) for value in box):
                raise _error(ErrorCode.BAD_IMAGE_REGION, "region must be [left, top, right, bottom] in source pixels")
            left, top, right, bottom = box
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise _error(ErrorCode.BAD_IMAGE_REGION, f"region must be nonempty and inside {width} x {height}")
            pixels = image.load()
            target = tuple(target_color)
            matches: list[tuple[int, int]] = []
            for y in range(top, bottom):
                for x in range(left, right):
                    red, green, blue, alpha = pixels[x, y]
                    if alpha >= alpha_threshold and max(abs(red - target[0]), abs(green - target[1]), abs(blue - target[2])) <= tolerance:
                        matches.append((x, y))
            if matches:
                min_x, max_x = min(x for x, _ in matches), max(x for x, _ in matches)
                min_y, max_y = min(y for _, y in matches), max(y for _, y in matches)
                centroid = [sum(x for x, _ in matches) / len(matches), sum(y for _, y in matches) / len(matches)]
                match_bbox = [min_x, min_y, max_x + 1, max_y + 1]
            else:
                centroid, match_bbox = None, None
            preview = Image.new("RGB", (right - left, bottom - top), "white")
            preview.paste(image.crop((left, top, right, bottom)).convert("RGB"))
            if overlay and match_bbox:
                draw = ImageDraw.Draw(preview)
                draw.rectangle((match_bbox[0] - left, match_bbox[1] - top, match_bbox[2] - left - 1, match_bbox[3] - top - 1), outline="#e11d48", width=max(1, min(5, round(max(preview.size) / 200))))
            scale = max_size / max(preview.size)
            preview_size = (max(1, round(preview.width * scale)), max(1, round(preview.height * scale)))
            preview = preview.resize(preview_size, Image.Resampling.LANCZOS)
            stream = BytesIO()
            preview.save(stream, format="PNG")
            png = stream.getvalue()
    except ProtocolError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise _error(ErrorCode.IMAGE_READ_FAILED, f"cannot read image: {exc}") from exc
    if len(png) > MAX_PREVIEW_BYTES:
        raise _error(ErrorCode.IMAGE_PREVIEW_TOO_LARGE, "preview exceeds 2 MB", details={"bytes": len(png)})
    region_area = (right - left) * (bottom - top)
    return {
        "source": source.relative_to(project_root).as_posix(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_size": [width, height], "region": box,
        "target_color": list(target_color), "tolerance": tolerance, "alpha_threshold": alpha_threshold,
        "match_count": len(matches), "match_fraction": len(matches) / region_area,
        "match_bbox": match_bbox, "centroid": centroid, "preview_size": list(preview_size),
        "pixel_scale": scale, "mime_type": "image/png", "image_base64": base64.b64encode(png).decode("ascii"),
        "schema_version": "simloom-ai/image-measurement/v1",
    }
