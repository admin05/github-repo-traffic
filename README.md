# GitHub 全仓库流量档案

自动发现令牌可见的本人全部 GitHub 仓库，每天归档 GitHub 只保留 14 天的流量数据，并通过 GitHub Pages 提供可筛选、可排序的全量表格。

本项目基于 [piebro/github-repo-traffic-stats](https://github.com/piebro/github-repo-traffic-stats) 重构。

## 采集范围

GitHub Traffic API 提供的四个查询接口已全部覆盖：

- Views：每日浏览量和独立访客数
- Clones：每日克隆量和独立克隆者数
- Popular paths：最近 14 天热门内容路径快照
- Popular referrers：最近 14 天热门来源快照

同时采集用于完整描述仓库的 API 数据：

- 可见性、归档/分叉状态、描述、主页、主题、许可证和默认分支
- Star、Fork、Watcher、Issue/PR、仓库大小及每日历史快照
- 语言占比、社区健康度、过去 52 周参与度
- Contributor、Branch、Tag、Release 和开放 PR 数量

Views 和 Clones 会按时间戳覆盖合并 GitHub 返回的滚动窗口，因此持续运行后可永久保留超过 14 天的历史。热门路径和来源本身是滚动 14 天聚合值，项目按天保存原始快照，不会把重叠窗口错误相加。

## 部署

### 1. 创建访问令牌

推荐创建 Fine-grained personal access token：

1. Repository access 选择需要统计的全部仓库。
2. Repository permissions 至少授予 `Administration: Read-only` 和 `Metadata: Read-only`。
3. 如果需要私有仓库统计，必须把这些私有仓库纳入令牌范围。

Classic PAT 可使用 `repo` scope。不要把令牌写入源码或任何数据文件。

### 2. 添加 Actions Secret

在本仓库的 `Settings → Secrets and variables → Actions` 新建：

```text
GH_TRAFFIC_TOKEN=<你的令牌>
```

内置 `GITHUB_TOKEN` 通常只能读取当前仓库，无法替代这个跨仓库令牌。

### 3. 允许 Actions 写入

在 `Settings → Actions → General → Workflow permissions` 选择 `Read and write permissions`。

### 4. 启用 Pages

在 `Settings → Pages → Build and deployment` 中把 Source 设为 `GitHub Actions`，然后手动运行一次：

- `Collect GitHub traffic`
- `Deploy dashboard to GitHub Pages`

之后采集任务每天 UTC 23:23 自动运行。数据提交后会自动重新部署页面。

## 隐私提醒

如果令牌能看到私有仓库，归档会包含私有仓库名称、描述、语言、流量等信息。请保持本仓库为 private，并确认你的 GitHub 套餐和组织策略支持受控访问的 private Pages。普通公开 GitHub Pages 会公开 `data/` 下的全部内容；无法确保访问控制时，不要启用 Pages，可在本地运行：

```bash
python -m http.server 8000
```

访问 <http://localhost:8000>。

## 本地采集与验证

```bash
python -m pip install -r requirements.txt
GH_TOKEN=... GITHUB_OWNER=你的用户名 python query_github_traffic_data.py
python query_github_traffic_data.py --validate
python -m http.server 8000
```

`GITHUB_OWNER` 可省略，此时使用令牌所属账号。数据保存在：

- `data/dashboard.json`：轻量总览索引
- `data/repositories/<仓库名>.json`：仓库完整历史及快照

## 限制

- GitHub 不提供 14 天以前的历史补录接口，因此首次运行只能从当前窗口开始，越早部署越好。
- Traffic API 要求令牌对目标仓库具备相应管理读取权限；权限不足的端点会记录在仓库的 `collection_errors` 中。
- Unique 指标是 GitHub 在各日窗口内提供的值，跨日求和不等同于整个周期完全去重后的真实人数。
- API 限速仍然适用；项目对仓库逐个采集，并保留可用的部分结果。

## License

[MIT](LICENSE)
