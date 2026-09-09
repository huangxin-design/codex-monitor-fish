# 本地统计约定

这是对已观测 Codex 本地日志的解析实现，不是 OpenAI 稳定计费接口。不同版本可能需要调整解析器；不能保证清理过日志或其他设备上的历史完整。

## 当前数据结构

- `state_5.sqlite` 的 `threads` 表用于获取任务名称、cwd、日志位置、模型、创建时间、来源与归档状态；`thread_spawn_edges` 表提供父子关系。以只读 URI 打开，并设置 `PRAGMA query_only=ON`。
- 根任务通过 cwd 精确匹配选中项目；Windows 盘符路径大小写不敏感，POSIX 路径保留大小写。不默认把相邻目录或独立 worktree 算入项目。子任务通过父子关系计入，不要求与根任务 cwd 相同。
- 日志位置迁移时，允许在当前 Codex 数据目录的 `sessions`、`archived_sessions` 内按任务编号查找替代文件。
- `token_usage_record.payload` 应包含 `thread_id`、`response_id`、`usage`。只接受属于当前任务的记录，按请求编号去重，再汇总 usage 的输入、缓存输入、输出、推理输出、总量字段。

## 容易错算的地方

- 不汇总 `thread_token_usage`、任务索引的 `tokens_used` 或 `event_msg/token_count` 的累计值。它们可能继承历史、重置或遗漏压缩请求。
- 相同日志里的其他任务记录不属于当前任务；同一个请求的重复日志只计一次。
- `total_tokens = input_tokens + output_tokens`；缓存是输入的一部分，推理是输出的一部分。
- 活跃文件尚未写完的最后一行等下次刷新，缺失日志和损坏行要给出不完整提示。没有逐次记录不能表述为已确认零消耗。
- 单位“亿”只改变显示；CSV 保留整数。显示时四舍五入后的值不能用于核对精确合计，应读原始值。

修改解析逻辑后运行模板的 `test_monitor.py`。用受控假数据验证去重、跨任务继承、递归子任务、目录范围与未知格式，再用用户自己的只读记录检查结果。不要把用户生成的 config、快照、CSV、会话日志、浏览器头像或截图放入分享包。
