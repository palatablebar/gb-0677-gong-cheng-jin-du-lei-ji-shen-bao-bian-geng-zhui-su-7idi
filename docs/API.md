# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 补充变更参数

`POST /api/runs`、`/api/changes` 的 `parameters` 支持：

```json
{"changes":{"C1":{"approved":true,"effective":"2026-11","price":"100.00","cap":"100","reversal":"approved","item":"001"},
            "S1":{"supplement_to":"C1","approved":true,"effective":"2027-01","price":"130.00"}}}
```

`supplements` 也可平铺 `{"S1":{...,"supplement_to":"C1"}}` 或按父级分组 `{"C1":{"S1":{...}}}`。补充变更必须重述单价，未重述的 cap/reversal/items/end 向原变更继承，结果行带 `inherited_terms`、`cap_source`、`reversal_rule_source` 与 `change_basis`。

## 发布与签认

- `POST /api/runs/<id>/publish`：原子发布本期计量结果，返回 `result_id`、`created`、`measurement`。同一结果重复发布返回 `created=false` 且不新增记录；内容冲突返回409 `PUBLISH_CONFLICT`，旧版保留。
- `GET /api/runs/<id>/publications`：该批次已发布结果列表。
- `GET /api/publications/<result_id>`：单条已发布结果。
- `POST /api/publications/<result_id>/sign`：`{"version":N,"decision":"已复核"}`；已签认返回409 `ALREADY_SIGNED`，陈旧版本返回409 `STALE_VERSION`。

`measurement` 含 `current_change`、`inherited_terms`、`effective_interval`、`unsigned_remaining`（quantity/amount/cap/used/pending_amount/by_chain）、`chains`、`signable`、`result_id`。
