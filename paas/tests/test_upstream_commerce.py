"""Verify the two explicit upstream repairs and their fixed SQL metric semantics."""

import sqlite3
import unittest

from paas.upstream_commerce import (
    AUTHOR_SOURCE_SHA256,
    ORIGINAL_MODEL_HASHES,
    apply_metric_repairs,
    metric_queries,
)


class UpstreamMetricRepairTests(unittest.TestCase):
    def test_rejects_non_author_snapshot_or_non_author_models(self):
        with self.assertRaisesRegex(ValueError, '作者原版 SQLite'):
            apply_metric_repairs({}, source_sha256='other', original_hashes=ORIGINAL_MODEL_HASHES)
        with self.assertRaisesRegex(ValueError, '六份未修改'):
            apply_metric_repairs({}, source_sha256=AUTHOR_SOURCE_SHA256, original_hashes=ORIGINAL_MODEL_HASHES)
        changed = dict(ORIGINAL_MODEL_HASHES, **{'m_metric_model.yaml': 'changed'})
        with self.assertRaisesRegex(ValueError, '六份未修改'):
            apply_metric_repairs({}, source_sha256=AUTHOR_SOURCE_SHA256, original_hashes=changed)

    def test_metrics_keep_order_dedup_and_distinct_sold_spu_weight(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE t_campaign(campaign_id, campaign_name, start_time, end_time);
            CREATE TABLE t_coupon_usage(campaign_id, order_id);
            CREATE TABLE t_order(order_id PRIMARY KEY, status, payment_amount, gmt_create);
            CREATE TABLE t_product(product_id PRIMARY KEY, avg_rating);
            CREATE TABLE t_order_item(order_id, product_id);
            INSERT INTO t_campaign VALUES
                ('C1', '秋季活动', '2025-09-02', '2025-09-06'),
                ('C2', '零贡献', '2025-09-03', '2025-09-03'),
                ('C3', '期外活动', '2025-08-01', '2025-08-10');
            INSERT INTO t_coupon_usage VALUES
                ('C1','O1'), ('C1','O1'), ('C1','O2'), ('C1','O3'), ('C1','O4'), ('C1','O5'), ('C1', NULL);
            INSERT INTO t_order VALUES
                ('O1', 2, 100, '2025-09-02'), ('O2', 4, 30, '2025-09-06'),
                ('O3', 5, 400, '2025-09-03'), ('O4', 6, 700, '2025-09-03'),
                ('O5', 2, 600, '2025-09-07');
            INSERT INTO t_product VALUES ('P1', 5), ('P2', 3), ('P3', 1), ('P4', NULL), ('P5', 1);
            INSERT INTO t_order_item VALUES
                ('O1', 'P1'), ('O1', 'P1'), ('O2', 'P2'), ('O3', 'P3'),
                ('O2', 'P4'), ('O5', 'P5');
        """)
        period = {'date_start': '2025-09-01', 'date_end': '2025-09-07'}
        queries = metric_queries()
        marketing = conn.execute(queries['MTR-MKT-001'], period).fetchall()
        self.assertEqual(marketing, [('C1', '秋季活动', 2, 130.0), ('C2', '零贡献', 0, 0.0)])
        rating = conn.execute(queries['MTR-PRD-005'], period).fetchone()
        self.assertEqual(rating, (4.0, 3, 2))
