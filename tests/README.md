# tests/

离线回归测试（不引入测试框架、不依赖外网）。每个脚本都是可直接运行的独立程序，
全部数据操作都在 `tempfile` 临时目录里进行（通过 `DANBOORU_DECK_DATA_DIR` 环境变量
重定向数据目录），跑完自动清理，不会碰真实的 hot_pic / deck.db。

## 运行

在仓库根目录执行：

```bash
.venv/Scripts/python.exe tests/test_p0_hardening.py
.venv/Scripts/python.exe tests/test_deck_db.py
```

## 测试内容

| 脚本 | 覆盖范围 |
|------|----------|
| `test_p0_hardening.py` | P0 加固：http_client 重试/退避/超时、临时文件唯一性与原子写、DanbooruData 惰性加载、优雅退出；HTTP 场景用本地 mock server |
| `test_deck_db.py` | P1 SQLite 迁移：① bootstrap 幂等（含自动引导不改名 → CLI `deck_db.py --import` 改名收尾）② JSON 镜像 ↔ deck.db 等价往返 ③ 多进程并发 stats/queue/log 竞争回归 ④ viewer 双唯一键 ⑤ reconcile 吸收外部镜像写入 ⑥ export-all → 删库 → 回滚重导 |

约定：
- 新增涉及持久化 / 网络层的改动后，至少跑相关脚本确认全绿。
- 脚本头部会把 stdout/stderr 切到 UTF-8（Windows GBK 控制台兼容）。
- 脚本通过 `sys.path` 注入仓库根目录，因此从任意 cwd 运行都可以，但命令示例统一以仓库根目录为准。
