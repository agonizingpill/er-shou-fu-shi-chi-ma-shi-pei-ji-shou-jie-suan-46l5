# 周末候补整批试衣排班 · 验收报告（gb-1039）

日期：2026-11-19　范围：`backend/src/batch_service.py`、第 03 页「预约与留衣」、种子数据与 README。

## 需求对照

| 需求 | 实现 | 验证 |
|---|---|---|
| 顾问在预约页面为一批候补买家选衣物和可到店时段 | 第 03 页右侧「建立周末预排」：最多 6 个可到店时段、6 位候选买家（匿名体型+候补起始+勾选可用时段） | `shot_01` / `shot_05`，UI 表单真实建批成功 |
| 用尺码模型挑合身的搭配 | `batch_service._fits` 对每位买家×每件已实测衣物调用 `fit_model.predict`，只保留“合身”搭配；面板展示合身标签与概率 | `shot_04` 每席显示「合身 100%」 |
| 按空闲试衣位排班 | 整数规划（scipy `milp`）分配两试衣位；同一试衣位重叠时段互斥、一衣一人、时段须在寄售期内 | `test_transitive_overlap_not_double_booked` 等 |
| 先让排上的买家尽可能多 | 目标权重 `BIG + 等待分钟×1000 + 概率×999`，`BIG` 远大于其余项，故人数优先 | `test_priority_max_count_before_waiting` |
| 再优先排等得久的 | 人数相同后按累计等待时长最大 | `test_longer_wait_preferred_when_scarce` |
| 预排不占衣物和试衣位 | 预排只写 BatchItem（draft），衣物保持 listed、Fitting 表不变 | `test_preplan_holds_nothing` |
| 确认时若与单笔预约并发撞上，整批暂不落单 | 确认在 Shop 父锁内逐条复核，任一冲突 `raise Replanned`，路由 `s.commit()` 后返回 409，不落任何单 | `test_confirm_conflict_replans_without_booking`、真实并发 `test_live_concurrency` 用例 1/4 |
| 按剩下的衣物和空位重排给顾问看 | 冲突即 `_write_version(plan_version+1)`，页面刷新显示“第 2 版”及每位买家改排/未排原因 | `shot_02`、`test_live_concurrency` 用例 4 |
| 确认重试不重复留衣 | `state=='confirmed'` 直接返回原视图；正常情况下每批只在首次成功确认时建 Fitting | `test_confirm_idempotent`、`test_live_concurrency` 用例 2/3 |
| 同页能查成单的安排和没排上的原因 | 同日泳道图 + 候补名单表（等待天数/可到店时段/排定衣物试衣位/状态与原因） | `shot_01`、`shot_03`、`shot_04` |

## 自动化测试

1. `test_batch_service.py`（SQLite，16 例）：预排不留衣、人数优先、等待优先、同试衣位不重叠（含传递重叠 A/C 复用）、未排原因（无合身/无时段/超寄售期/位占用）、确认落单与幂等、冲突重排后不落单、版本历史、时段校验。
2. `test_batch_api.py`（FastAPI TestClient，7 例）：建批读取、viewer 写接口 403、匿名 401、确认幂等、**409 冲突→首版 draft→重试只补落且不重复留衣**、register 暴露批次表、非法负载 409。
3. `test_live_concurrency.py`（真实 uvicorn + PostgreSQL 16，4 例，连跑 3 次稳定）：单笔预约与整批确认同时提交不重复占用；两个确认并发不重复留衣；顺序重试幂等；冲突 409 且整批未落单→重排→再确认。
4. `test_render.mjs`（jsdom 加载真实页面模块，11 项）：批次面板、两条试衣位泳道、当前版本隔离、未排原因、确认/重排按钮、等待天数、转义。

结果：
```
test_batch_service.py   Ran 16 tests  OK
test_batch_api.py       Ran  7 tests  OK
test_live_concurrency   4/4 PASS ×3
test_render.mjs         11/11 PASS
```

## 截图

- `shot_01_batch_plan.png`：第 03 页整体（单笔预约 + 整批预排面板 + 建立表单）。
- `shot_04_batch_card.png`：演示预排第 1 版：5 位候补排出 4 位，P033 未排上并给出「在库均偏紧」原因；两试衣位按时间排开。
- `shot_02_confirm_conflict.png`：先用单笔预约占走 G037 的时段，再确认 → 409，页面自动显示“第 2 版”并列出改排原因。
- `shot_03_confirm_ok.png`：第 2 版再次确认成功，状态“已确认落单”，衣物与试衣位已占用。
- `shot_05_new_batch.png`：通过页面表单真实新建批次并自动选中。

## 说明与边界

- 尺码模型为 seed42 合成样本的 KNN，仅辅助挑选“合身”；真实舒适度仍由试衣确认。模型训练已加进程内缓存，批量多次预测耗时从 ~3.4ms/次 降到 ~0.9ms/次。
- 每位候补买家本批只试一件；整数规划 5 秒时限，超时回落贪心（按等待时长）。
- 批次不可中途加人，只能整批重排或新建批次。
- scipy 新增为 `requirements.txt` 依赖（`scipy==1.15.1`），镜像构建会一并安装。
