import unittest

from prediction import estimate_batch, validate_subject


class PredictionTests(unittest.TestCase):
    def test_validation_rejects_invalid_numeric_fields(self):
        subject = dict(location='Long An', type='Đất nền', area=85)
        for field, values in {
            'area': [0, -1, 'abc', float('nan'), float('inf'), True],
            'frontage': [0, -1], 'road_width': [0, -1],
            'bedrooms': [-1, 1.5], 'bathrooms': [-1], 'floors': [-1, 2.5],
            'latitude': [91, -91], 'longitude': [181, -181],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertIn(field, validate_subject({**subject, field: value}))
        self.assertEqual(validate_subject({**subject, 'bedrooms': 0, 'frontage': None}), {})

    def test_batch_preserves_order_ids_errors_and_missing_samples(self):
        listing = dict(title='Nhà A', location='Long An', type='Đất nền', status='Bán', price=1000, area=10)
        subjects = [
            dict(id='same', location='Long An', type='Đất nền', area='20'),
            dict(id='same', location='Long An', type='Đất nền', area='invalid'),
            dict(id='third', location='Khác', type='Đất nền', area='20'),
        ]
        rows = estimate_batch([listing], subjects)
        self.assertEqual([row['Dòng dữ liệu'] for row in rows], [1, 2, 3])
        self.assertEqual([row['ID'] for row in rows], ['same', 'same', 'third'])
        self.assertEqual(rows[0]['Giá tham khảo (₫)'], 2000)
        self.assertEqual(rows[1]['Trạng thái'], 'Dữ liệu không hợp lệ')
        self.assertIn('Diện tích', rows[1]['Lỗi'])
        self.assertEqual(rows[2]['Trạng thái'], 'Thiếu dữ liệu so sánh')
        self.assertIsNone(rows[2]['Giá tham khảo (₫)'])

    def test_required_text_and_optional_csv_empty_fields(self):
        self.assertEqual(set(validate_subject({'area': 20})), {'location', 'type'})
        self.assertEqual(validate_subject(dict(location='Long An', type='Đất nền', area='20', frontage='', floors='')), {})
