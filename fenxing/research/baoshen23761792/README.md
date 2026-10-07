# @baoshen23761792 公开资料研究

截至 2026-09-21，本目录保存 38 条可获取的推文及 18 张图片，并提供有来源依据的方法论研究。**这不是全部推文的导出。**

先阅读 [方法论.md](方法论.md)。

## 文件

| 文件 | 用途 |
| --- | --- |
| 方法论.md | 作者表达、研究归纳、执行补充及证据缺口 |
| tweets.csv | 用表格查看正文、日期、ID、原帖链接与来源 |
| tweets.jsonl | 逐条结构化数据，含媒体元数据 |
| raw/ | 38 次单条公开接口响应与抓取时间 |
| media/ | 18 张推文配图 |
| media_manifest.json | 配图链接、文件位置与下载状态 |
| manifest.json | 样本数量、时间范围和完整性标记 |
| profile_snapshot.json | 第三方账号元数据快照 |
| supplementary_sources.json | 无唯一推文 ID 的补充缓存证据摘要 |
| tweet_ids.txt | 公开索引发现的 ID 清单 |
| collect_public.py | 按已知 ID 抓取并去重，导出 CSV/JSONL |
| fetch_media.py | 下载已归档推文中的图片 |

## 实际范围

已归档最早发布时间为 2026-03-20，最晚为 2026-08-26。这个跨度不是连续覆盖；主要样本来自 3—5 月及 7—8 月。相对时间的网页缓存没有被转换成推文精确日期。CSV 保留接口返回的 UTC 时间。

发现 ID 的公开入口是 [TwStalker 缓存一](https://twstalker.com/baoshen23761792) 和 [缓存二](https://w.twstalker.com/baoshen23761792)。随后通过 `https://api.fxtwitter.com/baoshen23761792/status/{id}` 下载单条内容，核对返回 ID 与作者，再按 ID 去重。媒体来自返回的 `pbs.twimg.com` 地址。本次没有使用登录 Cookie、私人数据、付费服务或账号密钥。

原帖 URL 指向 X，实际采集来源是 FxTwitter 第三方接口。能保存正文与时间不代表独立验证了作者的市场事实、收益或截图真实性。raw/ 包含接口返回的完整元数据；研究只把目标账号的内容归属于他。

不同缓存将账号条数显示为 1K 或 2K；本次第三方账号接口返回 1565。它们的时间和统计口径不可据此视为一致，所以没有用这些数字计算覆盖率，也没有把 38 条称为全部分析推文。

X 原站读取失败；镜像直接下载遇到挑战页面；索引中的 Load More 链接回到同一页面，未得到有效的历史分页。插件目录未找到可用的 Twitter/X 历史数据连接。已知 ID 的接口解决了单条下载，未解决完整 ID 清单的获取。

## 复跑

在本项目目录执行：

```bash
python3 research/baoshen23761792/collect_public.py
python3 research/baoshen23761792/fetch_media.py
```

只依赖 Python 3 标准库与 curl。已有原始响应会复用；采集器默认最多处理 250 个 ID，按已发现的自身引用、回复关系补充目标作者记录。它没有全时间线枚举能力。新增 ID 可加入 tweet_ids.txt 后复跑；请保留旧 ID。不要把 `pending_ids` 为空解释成全账号已抓完，它只表示这批已发现的 ID 已处理。

collect_public.py 会重新生成正文导出及采集清单；图片下载状态以单独的 media_manifest.json 为准，人工看图记录以方法论报告为准。

## 如需补齐完整历史

当前公开检索路径无法证明完整性。需有能分页返回该账号历史的公开数据源、现成历史存档，或之后另行接入有权限的数据接口，才能继续补齐。X 官方文档说明用户时间线最多提供最近 3200 条，可分页；更长历史可使用全历史检索，实际访问仍取决于权限与费用。[时间线文档](https://docs.x.com/x-api/posts/timelines/introduction)、[历史检索文档](https://docs.x.com/x-api/posts/search/introduction)

本次按照用户“先用公开来源”的选择停止于可核对样本，没有开通或调用付费接口。补齐后应重做月份覆盖、回复与原创分类、线程恢复、图文分析及观点变化检查，尤其不能只增加正确预测的案例。
