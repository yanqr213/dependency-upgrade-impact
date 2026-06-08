# dependency-upgrade-impact Showcase

这页展示一个真实可运行的依赖升级审计流程。它适合放进 Renovate、Dependabot、内部依赖治理 Bot，或 AI coding agent 的验证步骤中。

## 场景

一个升级 PR 同时改动了 Node 和 Python 依赖：

- `react` 从 `17.0.2` 升到 `18.2.0`
- `typescript` 从 `4.9.5` 升到 `5.4.5`
- `fastapi` 从 `0.95.0` 升到 `0.111.0`
- `requests` 从 `2.30.0` 升到 `2.32.3`
- `zod` 被新增
- `left-pad` 带有 `postinstall` 脚本

维护者真正关心的不是“lockfile 变了很多行”，而是：

- 哪些包是 high risk？
- 哪些源码文件会受影响？
- 哪些测试应该优先跑？
- CI 是否应该阻断？

## 一条命令

```bash
PYTHONPATH=src python -m dependency_upgrade_impact \
  --before examples/before \
  --after examples/after \
  --source-root examples/source \
  --format markdown \
  --language zh \
  --fail-on none
```

英文报告：

```bash
PYTHONPATH=src python -m dependency_upgrade_impact \
  --before examples/before \
  --after examples/after \
  --source-root examples/source \
  --format markdown \
  --language en \
  --fail-on none
```

JSON 给 agent 或 Bot：

```bash
PYTHONPATH=src python -m dependency_upgrade_impact \
  --before examples/before \
  --after examples/after \
  --source-root examples/source \
  --format json \
  --language en \
  --fail-on none
```

JUnit 给 CI：

```bash
PYTHONPATH=src python -m dependency_upgrade_impact \
  --diff examples/diff.patch \
  --source-root examples/source \
  --format junit \
  --language en \
  --output reports/dependency-impact.xml \
  --fail-on high
```

## 样例结论

真实示例输出的摘要：

```text
Total changes: 8
Added/removed/updated: 1/0/7
SemVer major/minor/patch: 4/3/0
Risk high/medium/low: 2/6/0
Max score: 83
CI Gate: PASSED
```

最高风险项：

| Package | Change | Risk | Why it matters | Affected source |
| --- | --- | --- | --- | --- |
| `npm:react` | `17.0.2` -> `18.2.0` | high 83 | major upgrade, source usage, engine constraint | `ui.tsx` |
| `npm:left-pad` | `1.1.3` -> `1.3.0` | high 73 | source usage, install-time script | `ui.tsx` |
| `npm:typescript` | `4.9.5` -> `5.4.5` | medium 65 | major dev-tool upgrade | `tests/test_app.py` |
| `python:urllib3` | `1.26.15` -> `2.2.2` | medium 65 | major runtime upgrade | `tests/test_app.py` |
| `python:fastapi` | `0.95.0` -> `0.111.0` | medium 53 | source usage | `app.py`, `tests/test_app.py` |

## 放进 CI

依赖升级 PR 可以把报告作为 artifact，并用退出码做 gate：

```yaml
name: Dependency impact

on: [pull_request]

jobs:
  dependency-impact:
    runs-on: ubuntu-latest
    env:
      PYTHONPATH: src
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m unittest discover -s tests
      - run: >
          python -m dependency_upgrade_impact
          --before examples/before
          --after examples/after
          --source-root examples/source
          --format junit
          --language en
          --output reports/dependency-impact.xml
          --fail-on high
```

`--fail-on high` 会在 high risk 变更出现时返回退出码 `2`。如果你只想生成报告而不阻断流水线，用 `--fail-on none`。

## 给 AI Agent 的 JSON 信号

Agent 可以按下面的顺序消费 JSON：

- `summary.gate_failed`：是否暂停并请求人工确认
- `changes[].risk_level` 和 `changes[].risk_score`：验证优先级
- `changes[].usage_files`：重点阅读的源码
- `changes[].suggested_tests`：优先运行的测试
- `changes[].reasons`：生成 PR 摘要或审计说明
- `changes[].engine_hints`、`changes[].script_hints`：单独标出运行时和安装期风险

一个实用策略：

```text
1. 先处理 high risk changes。
2. 对每个 high/medium risk change 打开 usage_files。
3. 运行 suggested_tests。
4. 如果 script_hints 或 engine_hints 非空，把 PR 标为需要人工复核。
5. 把 Markdown 报告贴到 PR 评论里，把 JSON 保存为 agent 证据。
```

---

# English Showcase

This page walks through a dependency upgrade review that can run inside Renovate, Dependabot, internal dependency bots, or AI coding agents.

## Scenario

One upgrade PR changes both Node and Python dependencies:

- `react` from `17.0.2` to `18.2.0`
- `typescript` from `4.9.5` to `5.4.5`
- `fastapi` from `0.95.0` to `0.111.0`
- `requests` from `2.30.0` to `2.32.3`
- `zod` is added
- `left-pad` includes a `postinstall` script

Reviewers do not just need a lockfile diff. They need to know which changes are risky, which source files are touched, which tests should run, and whether CI should block the PR.

## Command

```bash
PYTHONPATH=src python -m dependency_upgrade_impact \
  --before examples/before \
  --after examples/after \
  --source-root examples/source \
  --format markdown \
  --language en \
  --fail-on none
```

The example report finds:

```text
Total changes: 8
Added/removed/updated: 1/0/7
SemVer major/minor/patch: 4/3/0
Risk high/medium/low: 2/6/0
Max score: 83
CI Gate: PASSED
```

## CI and Agent Fit

- Markdown gives reviewers a readable PR comment.
- JSON gives agents stable fields for prioritization and evidence.
- JUnit gives CI systems familiar annotations.
- Exit code `2` lets a workflow block risky upgrades while still distinguishing analysis failures from policy failures.

Recommended agent loop:

```text
1. Sort changes by risk_score.
2. Read usage_files for every high and medium change.
3. Run suggested_tests.
4. Escalate when script_hints or engine_hints are present.
5. Attach Markdown to the PR and store JSON as audit evidence.
```
