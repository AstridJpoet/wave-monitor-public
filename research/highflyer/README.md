# 幻方公开足迹研究

本模块只分析依法公开的上市公司定期报告，不收集或推断非公开账户、逐笔交易、对冲头寸或私有模型。

## 第一阶段：公告足迹

```bash
python3 -m research.highflyer.collect_public_footprints \
  --start 2019-01-01 \
  --end 2026-09-06
```

输出位于 `research/highflyer/output/`：

- `footprints.csv`：公司、报告期、发布日期、管理人、可识别产品和巨潮原文链接。
- `collection_summary.json`：覆盖范围、样本量与局限。

索引命中后，还必须核验 PDF 上下文：

```bash
python3 -m research.highflyer.verify_public_footprints
```

- `verification_audit.csv`：每条初筛记录的核验状态。
- `verified_footprints.csv`：仅保留出现在前十大股东相关表格中的记录，并附证据页码。

核验完成后运行无前视偏差的行情分析：

```bash
python3 -m research.highflyer.analyze_public_footprints
```

- `enriched_footprints.csv`：季末技术特征，以及公告后 21/63 个交易日收益。
- `analysis_summary.json`：机器可读汇总。
- `report.md`：研究结论和自建透明模型的下一步。

同一公司同一报告期存在更正稿时，最新版用于 PDF 证据核验，同时保留首次公开日期。季度持仓快照不能还原买卖日期；任何后续回测必须以首次公开日期之后的交易日作为可执行起点。

## 数据边界

- 只出现进入上市公司前十大股东名单的仓位。
- 不包含未披露的小仓位、股指期货、融券、期权和其他对冲。
- 产品名可能揭示沪深300、中证500或中证1000增强方向，但不代表该产品只有这一种策略。
- 原始PDF和临时缓存不进入静态网站，也不提交到公开仓库。
