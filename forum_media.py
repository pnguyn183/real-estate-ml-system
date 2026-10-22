"""Image validation and privacy-safe processing for forum attachments."""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_FORUM_IMAGES = 3
MAX_FORUM_IMAGE_BYTES = 5 * 1024 * 1024
MAX_FORUM_IMAGE_PIXELS = 25_000_000
_MAX_IMAGE_EDGE = 2000


def prepare_forum_images(uploads):
    """Validate uploaded photos and return normalized JPEG bytes without metadata."""
    if len(uploads) > MAX_FORUM_IMAGES:
        raise ValueError(f"Mỗi chủ đề được đính kèm tối đa {MAX_FORUM_IMAGES} ảnh.")

    prepared = []
    for upload in uploads:
        image_data = upload.getvalue()
        if len(image_data) > MAX_FORUM_IMAGE_BYTES:
            raise ValueError("Mỗi ảnh không được vượt quá 5 MB.")
        try:
            with Image.open(BytesIO(image_data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Chỉ chấp nhận ảnh JPG, PNG hoặc WEBP.")
                if source.width * source.height > MAX_FORUM_IMAGE_PIXELS:
                    raise ValueError("Ảnh có kích thước điểm ảnh quá lớn.")
                source.load()
                image = ImageOps.exif_transpose(source)
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
                image.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format="JPEG", quality=84, optimize=True)
                prepared.append(output.getvalue())
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError("Một ảnh không hợp lệ hoặc không thể đọc được.") from error
    return prepared
