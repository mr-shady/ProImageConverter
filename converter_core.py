from __future__ import annotations

import io
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageColor, ImageOps, ImageSequence, UnidentifiedImageError

# ----------------------------- Optional format plugins -----------------------------

HEIF_ENABLED = False
SVG_ENABLED = False
RAW_ENABLED = False
OPTIONAL_IMPORT_ERRORS: dict[str, str] = {}

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_ENABLED = True
except Exception as exc:  # Optional dependency
    OPTIONAL_IMPORT_ERRORS["HEIC / HEIF / AVIF"] = str(exc)

try:
    import cairosvg

    SVG_ENABLED = True
except Exception as exc:  # Optional dependency
    OPTIONAL_IMPORT_ERRORS["SVG"] = str(exc)

try:
    import rawpy

    RAW_ENABLED = True
except Exception as exc:  # Optional dependency
    OPTIONAL_IMPORT_ERRORS["RAW"] = str(exc)


# Make Pillow load all installed plugins before format detection.
Image.init()


APP_NAME = "Pro Image Converter"
APP_VERSION = "1.3.0"

FORMAT_CONFIG = {
    "JPG": {"pil": "JPEG", "ext": ".jpg", "alpha": False, "animated": False},
    "PNG": {"pil": "PNG", "ext": ".png", "alpha": True, "animated": False},
    "WEBP": {"pil": "WEBP", "ext": ".webp", "alpha": True, "animated": True},
    "TIFF": {"pil": "TIFF", "ext": ".tiff", "alpha": True, "animated": True},
    "BMP": {"pil": "BMP", "ext": ".bmp", "alpha": False, "animated": False},
    "GIF": {"pil": "GIF", "ext": ".gif", "alpha": True, "animated": True},
    "ICO": {"pil": "ICO", "ext": ".ico", "alpha": True, "animated": False},
    "PDF": {"pil": "PDF", "ext": ".pdf", "alpha": False, "animated": True},
    "AVIF": {"pil": "AVIF", "ext": ".avif", "alpha": True, "animated": True},
    "HEIC": {"pil": "HEIF", "ext": ".heic", "alpha": True, "animated": True},
}

RAW_EXTENSIONS = {
    ".3fr", ".arw", ".bay", ".cr2", ".cr3", ".crw", ".dcr", ".dng", ".erf",
    ".fff", ".iiq", ".k25", ".kdc", ".mef", ".mos", ".mrw", ".nef", ".nrw",
    ".orf", ".pef", ".raf", ".raw", ".rw2", ".rwl", ".sr2", ".srf", ".srw",
    ".x3f",
}

LOSSY_FORMATS = {"JPG", "WEBP", "AVIF", "HEIC"}
ALPHA_UNSUPPORTED = {"JPG", "BMP", "PDF"}
ANIMATION_OUTPUTS = {"WEBP", "GIF", "TIFF", "PDF", "AVIF", "HEIC"}


def available_output_formats() -> list[str]:
    """Return output formats that the installed Pillow build can encode."""
    available: list[str] = []
    registered_saves = set(Image.SAVE.keys())

    for label, config in FORMAT_CONFIG.items():
        pil_format = config["pil"]
        if label == "HEIC":
            if HEIF_ENABLED and pil_format in registered_saves:
                available.append(label)
        elif label == "AVIF":
            if pil_format in registered_saves:
                available.append(label)
        elif pil_format in registered_saves:
            available.append(label)

    # Keep a predictable UX even on unusual Pillow builds.
    preferred_order = ["JPG", "PNG", "WEBP", "AVIF", "HEIC", "TIFF", "BMP", "GIF", "ICO", "PDF"]
    return [item for item in preferred_order if item in available]


def supported_input_extensions() -> set[str]:
    extensions = {ext.lower() for ext in Image.registered_extensions()}
    if SVG_ENABLED:
        extensions.update({".svg", ".svgz"})
    if RAW_ENABLED:
        extensions.update(RAW_EXTENSIONS)
    if HEIF_ENABLED:
        extensions.update({".heic", ".heif", ".hif", ".avif"})
    return extensions


@dataclass(slots=True)
class ConversionOptions:
    input_folder: Path
    output_folder: Path
    output_format: str
    quality: int = 90
    include_subfolders: bool = True
    preserve_structure: bool = True
    overwrite: bool = False
    preserve_metadata: bool = True
    strip_metadata: bool = False
    preserve_timestamps: bool = True
    preserve_animation: bool = True
    resize_enabled: bool = False
    max_width: int | None = 1920
    max_height: int | None = None
    allow_upscale: bool = False
    background_color: str = "#FFFFFF"


@dataclass(slots=True)
class ConversionResult:
    source: Path
    destination: Path | None
    success: bool
    skipped: bool = False
    message: str = ""


class BatchImageConverter:
    """Conversion engine independent from the GUI."""

    def __init__(
        self,
        options: ConversionOptions,
        progress_callback: Callable[[int, int, ConversionResult], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.options = options
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event or threading.Event()
        self.extensions = supported_input_extensions()
        try:
            self._input_root = options.input_folder.expanduser().resolve()
        except OSError:
            self._input_root = options.input_folder.expanduser()

    def discover_files(self) -> list[Path]:
        """Collect supported images quickly.

        Uses a single ``os.scandir`` walk (directory entry data is already cached
        on Windows) instead of resolving every file, which keeps large folders
        responsive even with tens of thousands of files.
        """
        root = self._input_root
        output = self.options.output_folder.expanduser()

        try:
            output = output.resolve()
        except OSError:
            pass

        if not root.is_dir():
            return []

        # Avoid re-reading generated files when the output folder lives inside
        # the input folder. An in-place conversion (same folder) is still allowed.
        excluded_prefix: tuple[str, ...] | None = None
        if output != root:
            try:
                excluded_prefix = output.relative_to(root).parts
            except ValueError:
                excluded_prefix = None

        extensions = self.extensions
        recursive = self.options.include_subfolders
        files: list[Path] = []
        pending = [root]

        while pending:
            if self.cancel_event.is_set():
                break
            current = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if recursive:
                                    pending.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                        except OSError:
                            continue

                        path = Path(entry.path)
                        if path.suffix.lower() not in extensions:
                            continue

                        if excluded_prefix is not None:
                            try:
                                relative_parts = path.relative_to(root).parts
                            except ValueError:
                                relative_parts = ()
                            if relative_parts[: len(excluded_prefix)] == excluded_prefix:
                                continue

                        files.append(path)
            except OSError:
                continue

        return sorted(files, key=lambda p: str(p).lower())

    def run(self, files: list[Path] | None = None) -> list[ConversionResult]:
        if files is None:
            files = self.discover_files()
        results: list[ConversionResult] = []
        total = len(files)

        for index, source in enumerate(files, start=1):
            if self.cancel_event.is_set():
                break

            result = self.convert_file(source)
            results.append(result)

            if self.progress_callback:
                self.progress_callback(index, total, result)

        return results

    def convert_file(self, source: Path) -> ConversionResult:
        try:
            destination = self._build_destination(source)
            if destination.exists() and not self.options.overwrite:
                return ConversionResult(
                    source=source,
                    destination=destination,
                    success=False,
                    skipped=True,
                    message="Output file already exists.",
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.stem}.part{destination.suffix}")

            if temporary.exists():
                temporary.unlink(missing_ok=True)

            self._convert_to_path(source, temporary)
            os.replace(temporary, destination)

            if self.options.preserve_timestamps:
                try:
                    stat = source.stat()
                    os.utime(destination, (stat.st_atime, stat.st_mtime))
                except OSError:
                    pass

            message = "Converted; embedded metadata removed." if self.options.strip_metadata else "Converted."
            return ConversionResult(
                source=source,
                destination=destination,
                success=True,
                message=message,
            )

        except Exception as exc:
            try:
                if "temporary" in locals() and temporary.exists():
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return ConversionResult(
                source=source,
                destination=locals().get("destination"),
                success=False,
                message=f"{type(exc).__name__}: {exc}",
            )

    def _build_destination(self, source: Path) -> Path:
        config = FORMAT_CONFIG[self.options.output_format]
        try:
            relative = source.relative_to(self._input_root)
        except ValueError:
            relative = Path(source.name)

        if self.options.preserve_structure and self.options.include_subfolders:
            parent = relative.parent
        else:
            parent = Path()

        return self.options.output_folder / parent / f"{source.stem}{config['ext']}"

    def _convert_to_path(self, source: Path, destination: Path) -> None:
        suffix = source.suffix.lower()

        if suffix in {".svg", ".svgz"}:
            if not SVG_ENABLED:
                raise RuntimeError("برای ورودی SVG باید CairoSVG نصب باشد.")
            image = self._load_svg(source)
            try:
                self._save_static(image, destination, source_info={})
            finally:
                image.close()
            return

        if suffix in RAW_EXTENSIONS:
            if not RAW_ENABLED:
                raise RuntimeError("برای فایل RAW باید rawpy نصب باشد.")
            image = self._load_raw(source)
            try:
                self._save_static(image, destination, source_info={})
            finally:
                image.close()
            return

        try:
            with Image.open(source) as image:
                source_info = dict(image.info)
                is_animated = bool(getattr(image, "is_animated", False))
                frame_count = int(getattr(image, "n_frames", 1))

                can_preserve_animation = (
                    self.options.preserve_animation
                    and is_animated
                    and frame_count > 1
                    and self.options.output_format in ANIMATION_OUTPUTS
                )

                if can_preserve_animation:
                    frames: list[Image.Image] = []
                    durations: list[int] = []
                    disposals: list[int] = []

                    try:
                        for frame in ImageSequence.Iterator(image):
                            copied = frame.copy()
                            processed = self._prepare_frame(copied)
                            frames.append(processed)
                            durations.append(int(frame.info.get("duration", source_info.get("duration", 100))))
                            disposals.append(int(frame.info.get("disposal", source_info.get("disposal", 2))))

                        self._save_animation(
                            frames=frames,
                            destination=destination,
                            source_info=source_info,
                            durations=durations,
                            disposals=disposals,
                        )
                    finally:
                        for frame in frames:
                            frame.close()
                else:
                    image.seek(0)
                    first = image.copy()
                    try:
                        self._save_static(first, destination, source_info=source_info)
                    finally:
                        first.close()

        except UnidentifiedImageError as exc:
            raise RuntimeError("فرمت تصویر شناسایی نشد یا فایل خراب است.") from exc

    @staticmethod
    def _load_svg(source: Path) -> Image.Image:
        png_bytes = cairosvg.svg2png(url=str(source))
        with Image.open(io.BytesIO(png_bytes)) as image:
            return image.convert("RGBA").copy()

    @staticmethod
    def _load_raw(source: Path) -> Image.Image:
        with rawpy.imread(str(source)) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=False,
                output_bps=8,
            )
        return Image.fromarray(rgb)

    def _prepare_frame(self, image: Image.Image) -> Image.Image:
        image = ImageOps.exif_transpose(image)

        if self.options.resize_enabled:
            image = self._resize(image)

        output_format = self.options.output_format

        if output_format in ALPHA_UNSUPPORTED:
            image = self._flatten_transparency(image)
        elif output_format == "GIF":
            if image.mode not in {"P", "L"}:
                rgba = image.convert("RGBA")
                image.close()
                image = rgba.quantize(
                    colors=255,
                    method=Image.Quantize.FASTOCTREE,
                    dither=Image.Dither.FLOYDSTEINBERG,
                )
        elif output_format == "ICO":
            if image.mode not in {"RGB", "RGBA"}:
                converted = image.convert("RGBA")
                image.close()
                image = converted
        elif image.mode not in {"RGB", "RGBA", "L", "LA", "P", "I;16", "I", "F", "CMYK"}:
            converted = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.close()
            image = converted

        return image

    def _resize(self, image: Image.Image) -> Image.Image:
        """Resize while always preserving the original aspect ratio.

        - Width only: calculate height automatically.
        - Height only: calculate width automatically.
        - Width and height: fit the image inside the requested box.
        """
        target_width = self.options.max_width
        target_height = self.options.max_height
        width, height = image.size

        if target_width is not None and target_height is not None:
            scale = min(target_width / width, target_height / height)
        elif target_width is not None:
            scale = target_width / width
        elif target_height is not None:
            scale = target_height / height
        else:
            return image

        if not self.options.allow_upscale:
            scale = min(scale, 1.0)

        new_size = (
            max(1, round(width * scale)),
            max(1, round(height * scale)),
        )

        if new_size == image.size:
            return image

        resized = image.resize(new_size, Image.Resampling.LANCZOS)
        image.close()
        return resized

    def _flatten_transparency(self, image: Image.Image) -> Image.Image:
        background_rgb = ImageColor.getrgb(self.options.background_color)

        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, background_rgb)
            background.paste(rgba, mask=rgba.getchannel("A"))
            rgba.close()
            image.close()
            return background

        if image.mode != "RGB":
            converted = image.convert("RGB")
            image.close()
            return converted

        return image

    def _save_static(self, image: Image.Image, destination: Path, source_info: dict) -> None:
        prepared = self._prepare_frame(image)
        try:
            if self.options.strip_metadata:
                prepared.info.clear()
                clean_info: dict = {}
            else:
                clean_info = self._metadata_after_transpose(prepared, source_info)

            kwargs = self._save_kwargs(clean_info)
            prepared.save(destination, format=FORMAT_CONFIG[self.options.output_format]["pil"], **kwargs)
        finally:
            prepared.close()

    def _save_animation(
        self,
        frames: list[Image.Image],
        destination: Path,
        source_info: dict,
        durations: list[int],
        disposals: list[int],
    ) -> None:
        if not frames:
            raise RuntimeError("هیچ فریمی برای ذخیره پیدا نشد.")

        first, *rest = frames
        if self.options.strip_metadata:
            for frame in frames:
                frame.info.clear()
            clean_info: dict = {}
        else:
            clean_info = self._metadata_after_transpose(first, source_info)

        kwargs = self._save_kwargs(clean_info)
        kwargs.update(
            {
                "save_all": True,
                "append_images": rest,
                "duration": durations,
                "loop": int(source_info.get("loop", 0)),
            }
        )

        if self.options.output_format == "GIF":
            kwargs["disposal"] = disposals

        first.save(
            destination,
            format=FORMAT_CONFIG[self.options.output_format]["pil"],
            **kwargs,
        )

    @staticmethod
    def _metadata_after_transpose(image: Image.Image, source_info: dict) -> dict:
        """Use EXIF from the transformed image so Orientation is not applied twice."""
        clean_info = dict(source_info)
        try:
            exif = image.getexif()
            if exif:
                clean_info["exif"] = exif.tobytes()
            else:
                clean_info.pop("exif", None)
        except Exception:
            clean_info.pop("exif", None)
        return clean_info

    def _save_kwargs(self, source_info: dict) -> dict:
        output_format = self.options.output_format
        quality = max(1, min(100, int(self.options.quality)))
        kwargs: dict = {}

        if output_format == "JPG":
            kwargs.update(
                quality=quality,
                optimize=True,
                progressive=True,
                subsampling=0 if quality >= 92 else -1,
            )
        elif output_format == "WEBP":
            kwargs.update(quality=quality, method=6)
        elif output_format in {"AVIF", "HEIC"}:
            kwargs.update(quality=quality)
        elif output_format == "PNG":
            # Compression changes file size, not visual quality.
            compress_level = round((100 - quality) * 9 / 99)
            kwargs.update(compress_level=max(0, min(9, compress_level)), optimize=True)
        elif output_format == "TIFF":
            kwargs.update(compression="tiff_lzw")
        elif output_format == "GIF":
            kwargs.update(optimize=True)
        elif output_format == "PDF":
            kwargs.update(resolution=100.0)

        if self.options.preserve_metadata and not self.options.strip_metadata:
            exif = source_info.get("exif")
            icc_profile = source_info.get("icc_profile")
            xmp = source_info.get("xmp")

            if exif and output_format in {"JPG", "PNG", "WEBP", "TIFF", "AVIF", "HEIC"}:
                kwargs["exif"] = exif
            if icc_profile and output_format in {"JPG", "PNG", "WEBP", "TIFF", "AVIF", "HEIC"}:
                kwargs["icc_profile"] = icc_profile
            if xmp and output_format in {"JPG", "WEBP", "TIFF", "AVIF", "HEIC"}:
                kwargs["xmp"] = xmp

        return kwargs
