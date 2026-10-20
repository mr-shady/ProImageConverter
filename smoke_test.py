"""Non-GUI smoke tests for conversion, metadata removal and settings persistence."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, PngImagePlugin

from converter_core import BatchImageConverter, ConversionOptions
from settings_store import load_settings, save_settings


def test_basic_conversion() -> None:
    with TemporaryDirectory() as temp:
        base = Path(temp)
        source = base / "images"
        output = base / "converted"
        (source / "sub").mkdir(parents=True)

        transparent = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
        draw = ImageDraw.Draw(transparent)
        draw.rectangle((100, 100, 700, 500), fill=(30, 130, 230, 180))
        transparent.save(source / "transparent.png")

        photo = Image.new("RGB", (320, 240), (235, 180, 40))
        photo.save(source / "sub" / "photo.jpg", quality=85)

        options = ConversionOptions(
            input_folder=source,
            output_folder=output,
            output_format="JPG",
            quality=90,
            include_subfolders=True,
            preserve_structure=True,
            resize_enabled=True,
            max_width=400,
            max_height=400,
            background_color="#FFFFFF",
        )

        results = BatchImageConverter(options).run()
        assert len(results) == 2
        assert all(result.success for result in results)
        assert (output / "transparent.jpg").exists()
        assert (output / "sub" / "photo.jpg").exists()

        with Image.open(output / "transparent.jpg") as converted:
            assert converted.mode == "RGB"
            assert converted.size == (400, 300)


def test_metadata_stripping() -> None:
    with TemporaryDirectory() as temp:
        base = Path(temp)
        source = base / "images"
        output = base / "converted"
        source.mkdir()

        image = Image.new("RGB", (300, 200), (90, 130, 180))
        exif = Image.Exif()
        exif[0x010E] = "AI generated image"
        exif[0x0131] = "AI Generator"
        image.save(
            source / "metadata.jpg",
            exif=exif,
            icc_profile=b"TEST_ICC_PROFILE",
            comment=b"AI image",
        )

        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Software", "AI Generator")
        png_info.add_text("Prompt", "example prompt")
        image.save(source / "metadata.png", pnginfo=png_info)

        for output_format, filename in (("JPG", "metadata.jpg"), ("PNG", "metadata.png")):
            format_output = output / output_format.lower()
            options = ConversionOptions(
                input_folder=source,
                output_folder=format_output,
                output_format=output_format,
                overwrite=True,
                preserve_metadata=True,
                strip_metadata=True,
            )
            results = BatchImageConverter(options).run()
            assert all(result.success for result in results)

            with Image.open(format_output / filename) as converted:
                assert len(converted.getexif()) == 0
                for key in ("exif", "icc_profile", "xmp", "comment", "Software", "Prompt"):
                    assert key not in converted.info


def test_settings_persistence() -> None:
    with TemporaryDirectory() as temp:
        previous = os.environ.get("PRO_IMAGE_CONVERTER_CONFIG_DIR")
        os.environ["PRO_IMAGE_CONVERTER_CONFIG_DIR"] = temp
        try:
            save_settings(
                {
                    "resize_enabled": True,
                    "max_width": "900",
                    "max_height": "",
                    "strip_metadata": True,
                    "theme": "روشن",
                }
            )
            loaded = load_settings()
            assert loaded["resize_enabled"] is True
            assert loaded["max_width"] == "900"
            assert loaded["max_height"] == ""
            assert loaded["strip_metadata"] is True
            assert loaded["theme"] == "روشن"
        finally:
            if previous is None:
                os.environ.pop("PRO_IMAGE_CONVERTER_CONFIG_DIR", None)
            else:
                os.environ["PRO_IMAGE_CONVERTER_CONFIG_DIR"] = previous


def main() -> None:
    test_basic_conversion()
    test_metadata_stripping()
    test_settings_persistence()
    print("All smoke tests passed.")


if __name__ == "__main__":
    main()
