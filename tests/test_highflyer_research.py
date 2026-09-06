from __future__ import annotations

import json
import unittest

from research.highflyer.collect_public_footprints import (
    extract_managers,
    extract_product_names,
    is_primary_periodic_report,
    parse_report_period,
    publication_date_from_ms,
    select_footprints,
)
from research.highflyer.verify_public_footprints import is_shareholder_context, verify_pages
from research.highflyer.analyze_public_footprints import (
    benchmark_for_product,
    features_at,
    forward_path,
    parse_sina_history,
    summarize_metric,
)

import pandas as pd


class HighFlyerResearchTests(unittest.TestCase):
    def test_report_period_parser(self) -> None:
        self.assertEqual(parse_report_period("示例公司：2024年年度报告"), ("FY", "2024-12-31"))
        self.assertEqual(parse_report_period("示例公司2025年第一季度报告"), ("Q1", "2025-03-31"))
        self.assertFalse(is_primary_periodic_report("示例公司：2024年年度报告摘要"))
        self.assertFalse(is_primary_periodic_report("关于2024年年度报告的问询函"))

    def test_extracts_manager_and_product(self) -> None:
        text = (
            "宁波<em>幻方</em>量化投资管理合伙企业（有限合伙）－"
            "幻方量化青溪5号私募证券投资基金 772,700"
        )
        self.assertEqual(extract_managers(text), ["宁波幻方量化"])
        self.assertEqual(extract_product_names(text), ["幻方量化青溪5号私募证券投资基金"])
        noisy = (
            "浙江九章资产管理有限公司141,100人民币普通股141,100天津锐新昌科技股份有限公司"
            "2020年年度报告全文53幻方星月石私募基金"
        )
        self.assertEqual(extract_product_names(noisy), ["幻方星月石私募基金"])

    def test_selects_latest_report_and_keeps_source(self) -> None:
        base = {
            "secCode": "300001",
            "secName": "示例公司",
            "announcementTitle": "示例公司：2024年年度报告",
            "announcementContent": (
                "浙江九章资产管理有限公司-九章幻方皓月1号私募基金 1,000,000"
            ),
            "adjunctUrl": "finalpage/example.PDF",
            "matched_search_term": "浙江九章资产管理有限公司",
        }
        older = {**base, "announcementId": "1", "announcementTime": 1740000000000}
        newer = {**base, "announcementId": "2", "announcementTime": 1740100000000}

        rows = select_footprints([older, newer])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].announcement_id, "2")
        self.assertEqual(rows[0].first_publication_date, publication_date_from_ms(1740000000000))
        self.assertEqual(rows[0].symbol, "300001.SZ")
        self.assertEqual(rows[0].manager, "浙江九章资产")
        self.assertEqual(json.loads(rows[0].product_names), ["九章幻方皓月1号私募基金"])
        self.assertTrue(rows[0].source_url.endswith("finalpage/example.PDF"))

    def test_rejects_tokenized_search_false_positive(self) -> None:
        row = {
            "secCode": "600001",
            "secName": "误报公司",
            "announcementTitle": "误报公司：2025年年度报告",
            "announcementContent": "浙江某资产管理有限公司与九章数据科技有限公司",
            "adjunctUrl": "finalpage/false.PDF",
            "matched_search_term": "浙江九章资产管理有限公司",
            "announcementId": "false",
            "announcementTime": 1770000000000,
        }
        self.assertEqual(select_footprints([row]), [])

    def test_each_result_keeps_its_own_snippet(self) -> None:
        rows = []
        for code, snippet in [
            ("600001", "宁波幻方量化投资管理合伙企业（有限合伙）-幻方A私募基金"),
            ("600002", "浙江九章资产管理有限公司-九章幻方B私募基金"),
        ]:
            rows.append(
                {
                    "secCode": code,
                    "secName": code,
                    "announcementTitle": f"{code}：2024年年度报告",
                    "announcementContent": snippet,
                    "adjunctUrl": f"finalpage/{code}.PDF",
                    "matched_search_term": "幻方",
                    "announcementId": code,
                    "announcementTime": 1740000000000,
                }
            )

        selected = select_footprints(rows)

        self.assertEqual([row.manager for row in selected], ["宁波幻方量化", "浙江九章资产"])
        self.assertIn("幻方A", selected[0].source_snippet)
        self.assertIn("九章幻方B", selected[1].source_snippet)

    def test_verifies_manager_in_shareholder_table(self) -> None:
        previous = "第七节 股份变动及股东情况 前十名无限售条件股东持股情况 股东名称"
        current = "宁波幻方量化投资管理合伙企业（有限合伙）－幻方量化青溪5号私募证券投资基金 772700"
        self.assertTrue(is_shareholder_context(previous, current))
        evidence = verify_pages([previous, current])
        self.assertEqual(evidence.status, "verified_shareholder")
        self.assertEqual(evidence.pages, "2")

    def test_rejects_company_investment_in_high_flyer_fund(self) -> None:
        page = (
            "证券投资情况不适用 私募基金投资情况适用 公司于2021年10月"
            "认购了宁波幻方量化投资管理合伙企业（有限合伙）管理的基金产品"
        )
        self.assertFalse(is_shareholder_context("", page))
        self.assertEqual(verify_pages([page]).status, "manager_found_non_shareholder")

    def test_benchmark_inference_is_explicit(self) -> None:
        self.assertEqual(benchmark_for_product("幻方中证1000增强"), ("512100.SS", "中证1000ETF", "product_name"))
        self.assertEqual(benchmark_for_product(""), ("510300.SS", "沪深300ETF", "default"))

    def test_features_and_post_publication_entry(self) -> None:
        prices = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=280, freq="B"),
                "open": range(100, 380),
                "high": range(101, 381),
                "low": range(99, 379),
                "close": range(100, 380),
                "volume": [1000] * 280,
            }
        )
        feature = features_at(prices, prices.iloc[220]["date"].date())
        self.assertEqual(feature["feature_as_of"], prices.iloc[220]["date"].date().isoformat())
        self.assertGreater(feature["ret_120d"], 0)
        publication = prices.iloc[100]["date"].date()
        path = forward_path(prices, publication, 21)
        self.assertEqual(path["entry_date"], prices.iloc[101]["date"].date().isoformat())
        self.assertEqual(path["exit_date"], prices.iloc[121]["date"].date().isoformat())

    def test_summary_exposes_outlier_sensitivity(self) -> None:
        frame = pd.DataFrame({"return": [0.01] * 8 + [0.5, 1.0]})
        summary = summarize_metric(frame, "return")
        self.assertGreater(summary["mean_ex_best"], 0.01)
        self.assertAlmostEqual(summary["mean_ex_top2"], 0.01)
        self.assertAlmostEqual(summary["trimmed_mean"], 0.07125)

    def test_parses_sina_jsonp_history(self) -> None:
        text = 'var _=([{"day":"2021-01-04","open":"10","high":"11","low":"9","close":"10.5","volume":"100"}]);'
        frame = parse_sina_history(text)
        self.assertEqual(len(frame), 1)
        self.assertEqual(float(frame.iloc[0]["close"]), 10.5)


if __name__ == "__main__":
    unittest.main()
