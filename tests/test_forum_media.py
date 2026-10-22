from io import BytesIO
from types import SimpleNamespace
import unittest

from PIL import Image

from forum_media import prepare_forum_images


def uploaded_image(image, image_format="PNG", exif=None):
    output = BytesIO()
    image.save(output, format=image_format, exif=exif)
    return SimpleNamespace(getvalue=output.getvalue)


class ForumMediaTests(unittest.TestCase):
    def test_normalizes_supported_image_and_removes_metadata(self):
        image = Image.new("RGB", (2400, 1200), "teal")
        exif = Image.Exif()
        exif[270] = "Private camera metadata"
        upload = uploaded_image(image, "JPEG", exif=exif.tobytes())
        source = Image.open(BytesIO(upload.getvalue()))
        self.assertEqual(source.getexif()[270], "Private camera metadata")
        source.close()
        output = prepare_forum_images([upload])

        self.assertEqual(len(output), 1)
        normalized = Image.open(BytesIO(output[0]))
        self.assertEqual(normalized.format, "JPEG")
        self.assertEqual(normalized.size, (2000, 1000))
        self.assertEqual(len(normalized.getexif()), 0)
        normalized.close()

    def test_rejects_invalid_image_data(self):
        with self.assertRaisesRegex(ValueError, "không hợp lệ"):
            prepare_forum_images([SimpleNamespace(getvalue=lambda: b"not an image")])

    def test_rejects_unsupported_format(self):
        upload = uploaded_image(Image.new("RGB", (10, 10)), "GIF")
        with self.assertRaisesRegex(ValueError, "JPG, PNG hoặc WEBP"):
            prepare_forum_images([upload])

    def test_rejects_more_than_three_images(self):
        uploads = [SimpleNamespace(getvalue=lambda: b"") for _ in range(4)]
        with self.assertRaisesRegex(ValueError, "tối đa 3 ảnh"):
            prepare_forum_images(uploads)

    def test_rejects_files_over_five_megabytes(self):
        upload = SimpleNamespace(getvalue=lambda: b"x" * (5 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "5 MB"):
            prepare_forum_images([upload])


if __name__ == "__main__":
    unittest.main()
