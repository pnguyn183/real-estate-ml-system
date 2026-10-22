import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name, filename in (
            ("DATA_FILE", "properties.json"),
            ("USERS_FILE", "users.json"),
            ("MESSAGES_FILE", "messages.json"),
            ("FORUM_FILE", "forum.json"),
        ):
            patcher = patch.object(storage, name, self.root / filename)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_empty_properties_stay_empty_without_rewriting(self):
        original = b"[\n]\n"
        storage.DATA_FILE.write_bytes(original)

        self.assertEqual(storage.load_properties(), [])
        self.assertEqual(storage.DATA_FILE.read_bytes(), original)

    def test_loading_adds_missing_display_fields_without_rewriting(self):
        records = [{"id": 3, "title": "Nhà mới", "custom_field": {"value": 12}}]
        original = json.dumps(records, ensure_ascii=False).encode("utf-8")
        storage.DATA_FILE.write_bytes(original)

        loaded = storage.load_properties()

        self.assertEqual(loaded[0], {
            **records[0], "user_id": "", "rating": 0, "reviews": 0, "images": [],
        })
        self.assertEqual(storage.DATA_FILE.read_bytes(), original)

    def test_normalization_preserves_owner_values_and_independent_image_defaults(self):
        record = {"id": 1, "user_id": "minh", "rating": 4.8, "reviews": 7,
                  "images": ["images/home.jpg"], "extra": "preserved"}
        self.assertEqual(storage.normalize_property(record), record)

        first = storage.normalize_property({"id": 2})
        second = storage.normalize_property({"id": 3})
        first["images"].append("new.jpg")
        self.assertEqual(second["images"], [])

    def test_all_json_stores_save_and_load_roundtrip(self):
        properties = [storage.normalize_property({"id": 1, "title": "Nhà mẫu"})]
        users = {"minh": {"full_name": "Minh Hoàng"}}
        messages = [{"from": "minh", "to": "admin", "content": "Xin chào"}]
        for save, load, payload in (
            (storage.save_properties, storage.load_properties, properties),
            (storage.save_users, storage.load_users, users),
            (storage.save_messages, storage.load_messages, messages),
            (storage.save_forum_posts, storage.load_forum_posts, storage.default_forum_posts()),
        ):
            with self.subTest(store=save.__name__):
                save(payload)
                self.assertEqual(load(), payload)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         ["forum.json", "messages.json", "properties.json", "users.json"])

    def test_failed_serialization_preserves_existing_file_and_cleans_tempfile(self):
        original = b"[]\n"
        storage.DATA_FILE.write_bytes(original)

        with self.assertRaises(TypeError):
            storage.save_properties([{"not_serializable": object()}])

        self.assertEqual(storage.DATA_FILE.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [storage.DATA_FILE])

    def test_failed_replace_preserves_existing_file_and_cleans_tempfile(self):
        original = b"[]\n"
        storage.DATA_FILE.write_bytes(original)

        with patch.object(storage.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                storage.save_properties([{"id": 1}])

        self.assertEqual(storage.DATA_FILE.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [storage.DATA_FILE])

    def test_missing_properties_file_still_initializes_samples(self):
        loaded = storage.load_properties()

        self.assertEqual(loaded, storage.default_properties())
        self.assertEqual(json.loads(storage.DATA_FILE.read_text(encoding="utf-8")), loaded)

    def test_missing_forum_file_initializes_pinned_home_nest_topics(self):
        loaded = storage.load_forum_posts()

        self.assertEqual(loaded, storage.default_forum_posts())
        self.assertEqual(json.loads(storage.FORUM_FILE.read_text(encoding="utf-8")), loaded)
        self.assertTrue(all(post["pinned"] for post in loaded))

    def test_invalid_existing_json_stores_are_preserved_and_reported(self):
        stores = (
            (storage.DATA_FILE, storage.load_properties, b"{"),
            (storage.USERS_FILE, storage.load_users, b"[]"),
            (storage.MESSAGES_FILE, storage.load_messages, b"{}"),
            (storage.FORUM_FILE, storage.load_forum_posts, b"null"),
        )
        for path, load, invalid_content in stores:
            with self.subTest(store=path.name):
                path.write_bytes(invalid_content)

                with self.assertRaises(storage.DataStoreError):
                    load()

                self.assertEqual(path.read_bytes(), invalid_content)
                self.assertFalse(any(file.suffix == ".tmp" for file in self.root.iterdir()))

    def test_invalid_records_and_user_profiles_are_preserved_and_reported(self):
        invalid_stores = (
            (storage.DATA_FILE, storage.load_properties, b'[{"id": 1}, null]'),
            (storage.USERS_FILE, storage.load_users, b'{"alice": null}'),
            (storage.MESSAGES_FILE, storage.load_messages, b'[null]'),
            (storage.FORUM_FILE, storage.load_forum_posts, b'[null]'),
        )
        for path, load, invalid_content in invalid_stores:
            with self.subTest(store=path.name):
                path.write_bytes(invalid_content)

                with self.assertRaises(storage.DataStoreError):
                    load()

                self.assertEqual(path.read_bytes(), invalid_content)


if __name__ == "__main__":
    unittest.main()
