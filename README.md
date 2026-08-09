# 波浪候选扫描：公开只读版

这是一个不依赖个人电脑的静态扫描网站：

- GitHub Actions 在云端扫描 A 股、美股和黄金相关标的。
- GitHub Pages 只发布候选列表，不运行可写后台。
- 默认在北京时间工作日 15:30 和次日 06:30 更新。
- 页面不包含持仓、观察名单、Telegram、日志或行情缓存。

## 均衡型 V2 扫描规则

- 股票池：最多 800 只流动性较好的 A 股、300 只美股大盘与活跃股，以及 2 个黄金标的。
- 浪级：周线识别大级别结构，日线识别中级别结构；两个级别同向时增加结构分。
- 形态：2 浪回撤、4 浪回踩、3 浪突破和 ABC/C 浪末端分别识别。
- 阶段：形态成立但尚未确认的归入“观察候选”，出现支撑收复、更高低点或放量突破后归入“今日触发”。
- 评分：结构 35 分、位置 25 分、确认 20 分、趋势 10 分、盈亏比 10 分。
- 硬过滤：低于失效位、距离支撑过远、盈亏比不足 1.5:1 或总分低于 65 的候选不发布。
- 分档：85 分以上优先，75-84 分较强，65-74 分观察。

> 仅供研究参考，不构成投资建议。波浪识别具有主观性，历史形态不代表未来表现。

## 发布步骤

1. 在 GitHub 新建一个空仓库。
2. 将本目录中的全部文件上传到仓库默认分支。
3. 打开仓库 `Settings > Pages`，将 `Source` 设为 `GitHub Actions`。
4. 打开 `Actions > Public Wave Scan`，点击 `Run workflow`。
5. 首次扫描完成后，在工作流的 `deploy` 页面打开公开网址。

后续无需保持个人电脑开机。定时任务由 GitHub 运行，网站由 GitHub Pages 提供。

## 本地验证

```bash
python3 -m unittest discover -s tests
python3 scripts/privacy_check.py
```

本地完整扫描：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/run_public_scan.py
```

## 隐私边界

`site/` 是唯一会发布的目录。发布前工作流会自动运行 `scripts/privacy_check.py`，发现以下内容时终止发布：

- 私人配置或观察名单文件
- Telegram 地址、令牌形态或密钥
- 已知个人路径或个人标识
- 原本地应用地址

扫描缓存与原始失败日志只存在于 GitHub Actions 缓存中，不会进入 Pages 网站。
