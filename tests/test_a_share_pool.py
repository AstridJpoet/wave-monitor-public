from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd

from scanner.ma_touch_backtest import A_SHARE_FALLBACK, load_a_share_pool


class ASharePoolTests(unittest.TestCase):
    def test_eastmoney_pool_takes_over_when_index_source_fails(self) -> None:
        fake = types.SimpleNamespace()

        def fail_index(**_kwargs):
            raise RuntimeError("index source unavailable")

        fake.index_stock_cons_weight_csindex = fail_index
        fake.stock_zh_a_spot_em = lambda: pd.DataFrame(
            [
                {"代码": "600001", "名称": "大市值公司", "总市值": 200},
                {"代码": "300001", "名称": "成长公司", "总市值": 100},
                {"代码": "600002", "名称": "ST示例", "总市值": 300},
            ]
        )

        with patch.dict(sys.modules, {"akshare": fake}):
            pool = load_a_share_pool(2)

        self.assertEqual([item.symbol for item in pool], ["600001", "300001"])
        self.assertTrue(all("Eastmoney" in item.source for item in pool))

    def test_static_pool_is_last_resort(self) -> None:
        def fail(**_kwargs):
            raise RuntimeError("source unavailable")

        fake = types.SimpleNamespace(
            index_stock_cons_weight_csindex=fail,
            stock_zh_a_spot_em=fail,
        )
        with patch.dict(sys.modules, {"akshare": fake}):
            pool = load_a_share_pool(3)

        self.assertEqual([item.symbol for item in pool], [symbol for symbol, _name in A_SHARE_FALLBACK[:3]])
        self.assertTrue(all(item.source == "Static A-share fallback" for item in pool))


if __name__ == "__main__":
    unittest.main()

