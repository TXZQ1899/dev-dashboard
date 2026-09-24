# EnvScope Tasks

本目录用于按阶段驱动 Trae Agent 实现 Ingress Path Resolution V1。

## 推荐执行方式

按顺序执行：

1. `TASK-01-request-resolver-framework.md`
2. `TASK-02-dns-endpoint-nat.md`
3. `TASK-03-clb-resolver.md`
4. `TASK-04-nginx-resolver.md`
5. `TASK-05-application-resolution.md`
6. `TASK-06-end-to-end-path-explorer.md`

## 使用方式

每次只让 Trae Agent 执行一个 Task。

推荐提示词：

```text
Please implement the task defined in:
tasks/TASK-01-request-resolver-framework.md

Follow the task scope strictly.
Do not implement later tasks.
Read the existing code before changing anything.
After implementation, run tests/type-check/build and provide the final report required by the task.
```

完成后：

1. Review Agent 修改
2. 运行测试
3. 用真实数据验证
4. 修正问题
5. Git commit
6. 再开始下一个 Task

不要一次性要求 Agent 连续执行全部 6 个 Task。
