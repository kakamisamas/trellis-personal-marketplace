# 主控状态栏与无头执行者可观测性执行计划

日期：2026-10-02。状态：r2 最终稿。r1 经 Codex 契约审核（`status-bar-plan-review-1002`，verdict fail，10 条阻塞、3 条建议），逐条处置见 §8。r2 相对 r1 的主要变化：状态文件拆成「主控回合」与「派单观察」两段并引入 generation；渲染进程由插件 `startup` 幂等拉起而不是当常驻入口；状态按 Herdr endpoint 分目录；无动静只做提示不唤醒主控；B 卡全部串行；分类器后端由 S0 产出配置，B2 只实现 Jev 与启发式。

本计划解决两件事：(A) 执行者与审核者改无头后，用户在 Herdr 侧边栏看不到主控处于什么状态、停在哪、是否卡住；(B) 无头执行者在跑的过程中有没有动静，主控和用户都看不到。改动落在两处：`herdr-dispatch` 技能（派工脚本，主体）和本模板仓（README 一段指引）。不改 Trellis 工作流模板的流程文字。

## 0. 目标与边界

### 目标行为（用户视角）

Herdr 侧边栏里，主控 pane 的名字下面常驻一行：

```text
▶ 在跑 · 验收 B · 8s
⏳ 等卡 A r2 · 38m · 上个动作 pytest 3m前
⏳ 等卡 A r2 +1 · 38m · ⚠ 无动静 12m
💬 想问你 · 要我按这个顺序拆卡开工吗 · 7s
○ 闲置 · 汇报完 · 2h
⚠ 需处理 · A missing_report · 3m
```

四个顶层状态回答「要不要管它」：想问你要管，其余不用。第二段回答「在哪」，末尾时长回答「多久了」。⚠ 是修饰，不是第五个状态。渲染进程死了，这一行在 90 秒内自己消失，不留过期信息。

### 不做

- 不让主控 LLM 自报进度。信号全部来自 hook、派工脚本和 Herdr 原生状态；LLM 只在 §2.4 的分支做一次二分类。
- 不加任何按工具调用触发的全量 hook。只加一条 `PreToolUse` matcher `AskUserQuestion`。主控自身「在跑但长时间无动作」的检测不在本计划（§9）。
- 无动静不唤醒主控，只在状态栏加 ⚠ 并发一次 Herdr 通知；主控仍由现有 60 分钟超时唤醒。信息类唤醒通道延后（§9）。
- 不把多个 watcher 合并成一个进程（§9）。
- pane 路径的 Claude Worker 有自己的 pane 和原生状态，不给它加状态行。
- 不处理多机（`--machine`）；本机多个 named session 按 endpoint 隔离（§2.3），不做跨 session 合并。
- 不改 Trellis `workflow.md`、`.trellis/` hooks、`trellis_gc.py`。
- B2 不实现 Haiku 后端。S0 若判 Jev 不合格，先按 §6「计划变更」改本计划再派 B2。

### 已按默认值决定的事（用户可推翻）

| # | 决定 | 默认值 |
| --- | --- | --- |
| 1 | 顶层状态 | 在跑 / 等卡 / 想问你 / 闲置，加 ⚠ 修饰 |
| 2 | token 名与来源 | `$trellis_status`，`--source trellis-status` |
| 3 | 渲染节奏 | 每 30 秒一拍，按绝对时钟调度；token TTL 90 秒；文本未变且距上次写入不足 60 秒则不写 |
| 4 | 无头「无动静」阈值 | 600 秒后状态栏 ⚠ 并发一次 Herdr 通知；不杀进程、不唤醒主控 |
| 5 | 无头轮询间隔 | 2 秒改 5 秒（`headless_poll_seconds`）；pane 路径不变；交卷检测时延上限 = 轮询间隔 + 1 秒 |
| 6 | 分类器 | S0 产出 `classifier.json`（`provider` ∈ `jev` / `heuristic`，`threshold`，`model`）；B2 读该文件；缺文件按 `heuristic` |
| 7 | 分类器阈值 | 初值 0.6，S0 定稿写入 `classifier.json` |
| 8 | 插件清单位置 | `~/.skills-manager/skills/herdr-dispatch/herdr-plugin.toml`，`herdr plugin link` 注册；`startup` 调幂等 `start` 后退出 |
| 9 | Grok 输出格式 | 切 `--output-format streaming-json`；`--help` 无该项时回退 `json` |
| 10 | 状态目录 | `$HERDR_DISPATCH_HOME/status/<endpoint>/`，`endpoint` = `HERDR_SOCKET_PATH` 绝对路径的 sha256 前 12 位 |

## 1. 事实基线（2026-10-02 核对；r1 第 1-13 行经审核抽查 8 行一致，r2 按 CSB-S01 修订措辞并补第 14-16 行）

| # | 事实 | 怎么核对的 |
| --- | --- | --- |
| 1 | Herdr 0.9.1，`herdr pane report-metadata <pane> --source X --token k=v --state-label idle=文本 [--ttl-ms N]` 可用；值落在 `pane get` / `agent get` 的 `tokens` 与 `state_labels` | 本机 `herdr --version`；主控在 `w15:p2` 实写带 30 秒 TTL 的 token 后 `agent get` 读回（本机实测） |
| 2 | 侧边栏自定义行：`[ui.sidebar.agents] rows` / `rows_by_agent.claude`，`$name` 读 pane token，`rules` 按前缀上色，token 值上限 80 字符；带 `--seq` 的 token 来源每 pane 最多 32 个 | Herdr v0.9.3 文档 `configuration.mdx` §Sidebar、`cli-reference.mdx` §Panes（版本文档，0.9.1 运行兼容性由 B1 真实验收） |
| 3 | `turn_guard.py hook --event stop/prompt` 已注册在 `~/.claude/settings.json` 的 Stop 与 UserPromptSubmit | 读 settings.json（本机） |
| 4 | Stop 判定表：第 5 步无 marker 放行；第 6 步消费 `stop_reason` 放行；第 7 步 action_required 拦、waiting 放行；第 8 步拦并计预算；顶层 `_dispatch` 外的异常捕获返回 0 | `scripts/turn_guard.py:508-529`、`1248-1259`；`references/turn-guard.md`；审核复核一致 |
| 5 | Claude Code Stop hook 载荷含 `last_assistant_message`；`Notification` 有 `permission_prompt`、`idle_prompt`；`AskUserQuestion` 是工具名，可做 PreToolUse matcher | code.claude.com/docs/en/hooks-guide.md、hooks.md、tools-reference.md（在线文档） |
| 6 | watcher：`submit` 用 `Popen(start_new_session=True)` 起一个独立进程，一个派单一个，持 `watcher.lock`；`poll_seconds` 默认 2.0；每拍写 `state.json` 心跳；phase `accepted` 即退出；`reject` 不退出；`cleanup` 写停止标记后退出；死了只能 `resume` | `scripts/watcher.py:1189-1294`，`scripts/store.py:140-154`，`scripts/runtime.py:916-980`；审核复核一致 |
| 7 | 无头观察只有：进程退出未交卷 → 纠正一次（新进程组，日志 `correction-stdout.log`）→ `missing_report`；超时（执行 3600 秒、审核 1800 秒、纠正 600 秒）→ 杀进程组 `headless_timeout`；合法 `publish` 先于退出码与超时；没有中途「无动静」检测 | `references/recovery.md` §headless 表，`scripts/headless.py:83-91、275-286、886-937、1008-1086`；审核复核一致 |
| 8 | Grok `--output-format json` 在进程结束时一次写出整个 JSON；`grok --help` 另有 `streaming-json`（NDJSON，一行一个 ACP 更新）与 `streaming-messages-json`；流式的实际字段形状未实测 | 业务轮 `workflow-v2-A2-1002/rounds/1/headless/stdout.log` 12765 字节且 mtime 等于 `ended_at`；`grok --help` 第 82-88 行；grok 1.0.46 |
| 9 | Codex `exec --json` 是 JSONL，首行 `thread.started`；`adapters.read_session_id` 已逐行解析 JSONL 并兼容整对象 JSON | `references/adapters.md` §实测；`scripts/adapters.py:578-606`；codex-cli 0.159.3 |
| 10 | 无头路径 2026-10-01 合入；业务无头派单跑过 1 轮（A2，grok，15 分钟，completed），另有 adapters.md 记录的 Codex 1 轮、Grok 2 轮探针 | 遍历 state home 的 `*/rounds/*/headless/process.json`；`references/adapters.md` §实测 |
| 11 | Jev：`POST https://api.typesafe.ai/v1/systemone`，Bearer；`questions` 支持 `noul` / `choice` / `score` 并行；响应读 `answers.<id>.noul` / `.choice`；价格每百万输入 token 0.042 美元、限速每秒 40 请求、CJK 弱于英文为 models.md 的文档陈述，未在线核实 | docs.typesafe.ai/api.md、quickstart.md（形状经审核复核）；models.md（价格与限速） |
| 12 | 当前 `status --all` 为 `in_flight=0`，无存活 watcher | 本机执行（审核派单结算后复核） |
| 13 | 本机 `~/.claude/projects/` 有 39 个会话记录，粗计 8366 条 assistant 消息，可抽样本 | 本机统计 |
| 14 | Herdr 插件 `[[startup]]` 只在服务器恢复会话和 live handoff 后运行一次，link / enable / reload-config 不触发；是一次性初始化，不是受监督的常驻进程；运行目录是插件根目录；注入 `HERDR_SOCKET_PATH`、`HERDR_BIN_PATH`、`HERDR_PLUGIN_STATE_DIR` 等 | `plugins.mdx:233-261`（版本文档） |
| 15 | pane 内进程环境含 `HERDR_SOCKET_PATH`、`HERDR_BIN_PATH`、`HERDR_PANE_ID`、`HERDR_TAB_ID`、`HERDR_WORKSPACE_ID`；`herdr status` 打印 socket 路径 | 本机 `env`、`herdr status` |
| 16 | `herdr_client.HerdrClient` 默认 `default_timeout=None`，调用无截止；`wakeup.py` 只有 `result` 与 `error` 两种唤醒组 | `scripts/herdr_client.py:52-91`，`scripts/wakeup.py:775-860`；审核指出并复核 |

## 2. 设计

### 2.1 状态文件（唯一真值，两段分写）

`$HERDR_DISPATCH_HOME/status/<endpoint>/<pane_id>.json`，旁边 `<pane_id>.lock`（`fcntl` 非阻塞，等待上限 200 毫秒，拿不到就跳过本次写入并记日志）。`endpoint` 取 `HERDR_SOCKET_PATH`，没有该变量时取 `herdr status` 的 socket 行；两者都没有则不写。

```json
{
  "schema_version": "2",
  "endpoint": "3f9a1c0b2e7d",
  "pane_id": "w15:p2",
  "terminal_id": "term_65cd2e2aafd13e4",
  "controller": {
    "generation": 17,
    "turn": "ended",
    "running_since": null,
    "ended_at": "2026-10-02T03:48:00Z",
    "asking": {"detail": "要我按这个顺序拆卡开工吗", "since": "2026-10-02T03:48:00Z", "source": "classifier", "generation": 17},
    "unclassified": false,
    "classifier": {"needs_user": 0.91, "blocking": 0.80, "kind": "问你拍板", "model": "jev-1.13.0", "generation": 17, "at": "..."}
  },
  "dispatches": {
    "run__A": {"round": 2, "card": "A", "role": "executor", "run_id": "run", "dispatched_at": "2026-10-02T03:10:00Z",
               "wrapper_generation": 1, "phase": "running", "quiet_since": null,
               "last_action": {"text": "pytest tests/", "at": "2026-10-02T03:45:00Z"}, "attention": null}
  }
}
```

- `controller` 只由主控 hook 和分类器写；`dispatches` 只由 watcher 写；两段互不覆盖。
- `controller.generation` 在每次 hook 写入（prompt / stop / ask）时加 1。分类器写入是比较交换：锁内比较 `generation` 等于启动时传入的值才写，否则丢弃。
- `terminal_id` 由渲染进程在首次看到该文件时从 `agent get` 绑定；之后任一拍 `agent get` 的 `terminal_id` 不同，视为换了占用者：清空两段、清 token、重新绑定。`agent_not_found` 连续 2 拍则删文件并清 token。
- `since` 语义：每种顶层状态的 episode 起点。在跑 = `running_since`；想问你 = `asking.since`，每个新 `asking`（新 generation）重置；等卡 = 所展示派单当前轮次的 `dispatched_at`，新轮次重置；闲置 = `ended_at`。`detail` 与 `last_action` 更新不改 `since`。通知按 `asking.generation` 去重。
- watcher 对自己条目的每次写入都带 `round`，锁内比较条目里的 `round` 等于自己的才写；派单终态（accepted / cancelled 落地 / failed 结算 / 停止标记）删除自己的条目，仅当条目 `round` 等于自己的。不写 `idle`，不碰 `controller`。

### 2.2 渲染合成（渲染进程每拍执行，确定顺序）

输入：状态文件、该 pane 的 `agent get`、每个 `dispatches` 条目对应的 `<dispatch>/state.json` 的 `phase`（本地小文件，不调 `status --all`）。

1. `agent get` 的 `agent_status == blocked` → 「想问你 · 权限/审批」。
2. `controller.turn == running` 或 `agent_status == working` → 「在跑 · <detail>」。
3. `controller.asking` 非空 → 「想问你 · <asking.detail>」，`blocking < 0.5` 时前缀「可选 ·」。
4. `dispatches` 里 `phase` 非终态的条目非空 → 「等卡 <card> r<round> [+N] · <时长> [· 上个动作 …] [· ⚠ 无动静 …]」，展示 `dispatched_at` 最新的一条，其余计数 `+N`；`attention` 非空的条目优先展示为「⚠ 需处理 · <card> <reason>」。
5. 否则「闲置 · <detail>」：`unclassified` 为真显示「未判定」；有 `classifier.kind` 显示该类别；都没有显示「汇报完」。

`phase` 读取失败或 state.json 不存在的条目按终态处理并从文件删除。普通派工（无 Trellis marker）与完整运行的派单都走第 4 条，不依赖 Stop 判定表走到第 7 步。

### 2.3 写入者

| 写入者 | 时机 | 写什么 |
| --- | --- | --- |
| `turn_guard.py hook --event prompt`（主控模式） | 每次收到输入 | `controller`: `turn=running`、`running_since`、`asking=null`、`unclassified=false`、generation+1 |
| `hook --event stop` 第 5 步（无 marker）与第 7 步 waiting 放行 | 规划期 / 无任务 / 派单在飞 | `turn=ended`、`ended_at`、generation+1、`unclassified=true`；再启动分类器（§2.4） |
| 同上第 6 步（消费 `stop_reason`） | 主控主动停 | 同上，并写 `asking{detail=reason 前 40 字, source=stop_reason}` |
| 同上第 7 步 action_required / 第 8 步拦回 | 主控被要求继续 | `turn=running`，detail「守卫拦回」 |
| `hook --event ask`（`PreToolUse` matcher `AskUserQuestion`） | 主控调该工具 | `asking{detail=第一个问题前 40 字, source=ask_tool}`，generation+1 |
| 分类器子进程 | Stop 后数秒 | 比较 generation 后写 `classifier`、清 `unclassified`；`needs_user ≥ T` 才写 `asking{source=classifier}`；不写任何「闲置」 |
| watcher 投递成功（初轮 / 续轮 / 纠正） | 派出 | `dispatches[id]`：`round`、`card`、`role`、`dispatched_at`、`wrapper_generation`、`phase=running` |
| watcher 每 60 秒 quiet 检查 | 在飞 | `last_action`、`quiet_since` |
| watcher 观察到 `result_ready` / `runtime_error` / `needs_attention` | 交卷或异常 | `phase`、`attention=<reason>`；进入 `result_ready` 后不再做 quiet 检查并清 `quiet_since` |
| watcher 退出 | 终态 | 删除自己的条目（`round` 相等才删） |

所有 hook 侧写入都是 best-effort：先算出原守卫判定和 stderr 文本，再在独立的 `try` 里做状态写入与分类器启动；这些步骤的任何异常只记 `turn-guard.log`，不改变退出码和 stderr。Worker 模式（pane 绑定有效或 `HERDR_DISPATCH_ROUND`）不写状态文件。

### 2.4 分类器（只在两个分支，带 generation）

Stop hook 在第 5 步和第 7 步 waiting 放行时，先完成状态写入并拿到新 `generation`，再 `Popen` 一个脱离的子进程：`trellis_status.py classify --endpoint <e> --pane <id> --generation <N> --message-file <status/tmp/<pane>-<N>.txt>`。消息是载荷 `last_assistant_message` 的末尾 3000 字；载荷缺该字段则不启动分类器，文件保持 `unclassified=true`。子进程读完即删临时文件；临时文件只含消息文本。

后端由 `$HERDR_DISPATCH_HOME/status/classifier.json` 决定（S0 产出）：

```json
{"provider": "jev", "model": "jev-latest", "threshold": 0.6, "timeout_s": 5, "updated_at": "..."}
```

`provider=jev` 请求（一次，三问并行）：

```json
{
  "model": "jev-latest",
  "state": "<消息末尾 3000 字>",
  "questions": {
    "needs_user": {"type": "noul", "instructions": "这段话的结尾是否在等用户回答或做决定，包括可做可不做的提议"},
    "blocking":   {"type": "noul", "instructions": "如果用户不回复，这项工作是否无法继续"},
    "kind": {"type": "choice", "instructions": "这段话的性质",
             "criteria": {"问你拍板": null, "要凭据或权限": null, "汇报完成": null, "中途汇报": null}}
  }
}
```

读 `answers.needs_user.noul`、`answers.blocking.noul`、`answers.kind.choice`。`needs_user ≥ threshold` → `asking`，detail 取消息最后一个非空行前 40 字。

`provider=heuristic`，以及 `jev` 在 `TYPESAFE_API_KEY` 缺失、HTTP 非 200、超时、JSON 不合法时的退化：最后一个非空行以 `？` / `?` 结尾，或含「要不要」「是否」「请确认」「可以吗」「行吗」→ `asking`；否则不写 `asking`。两种情况都写 `classifier.model`（`jev-1.13.0` 或 `heuristic`）并清 `unclassified`，所以「未判定」只在分类器还没返回或根本没启动时显示。

API key 只从环境变量读，不写进任何文件；`classifier` 段只存概率、类别、模型名、generation。HTTP 用标准库 `urllib`，不重试。并发上限：同一 pane 同一时刻只允许一个分类器子进程（`status/tmp/<pane>.classify.lock`），新的启动时旧的若还在就让旧的自然结束，其结果因 generation 不同会被丢弃。

### 2.5 无头执行者可观测性

1. **Grok 流式输出**：adapter `headless_args` / `headless_resume_args` 把 `--output-format json` 改为 `streaming-json`；`headless_preflight` 解析 `grok --help`，缺该项时回退 `json` 并返回 `warnings: ["grok_streaming_unavailable"]`。会话 id 继续用现有 `adapters.read_session_id`，只在 B4 探针确认 ACP 行里 `sessionId` 所在层级后按需扩展 `_session_field` 的键路径，不新建解析器。
2. **无动静检测**（watcher `_observe_headless`，每 60 秒一次）：只对 `verify_identity == match`、`phase == running`、尚无合法 `published.json` 的轮次观察。采样键是 `(dispatch_id, round, effective_process 的 wrapper_pid)`，纠正轮换了基线记录就重置。日志路径取该基线记录的 stdout 文件（初轮 `stdout.log`，纠正 `correction-stdout.log`）。活动量 = 日志大小 + 进程组成员 CPU 时间之和（`ps -o pid=,time= -p <成员 pid 列表>`，成员来自现有 `group_members(pgid)`，解析 `[[dd-]hh:]mm:ss`）。日志不可读或 `ps` 非零 → 本次记 `unknown`，不累计静默。两者与上次相同累计满 `headless_quiet_s`（600）→ 写 `quiet_since`，追加事件 `headless_quiet`（信息级，不进 `COLLECTABLE_ERROR_TYPES`，不改 phase，不发唤醒），并调一次 `herdr notification show`，每个采样键最多一次。日志增长或 CPU 增长 → 清 `quiet_since`。`result_ready`、终态、取消、`identity_changed` / `query_failed` → 停止观察并清标记。现有 `headless_timeout`、纠正、`missing_report` 顺序不变。
3. **最后动作**：`headless.last_action(stdout_path, harness)` 读文件末尾 64 KB；Codex 取最后一个 `item.*` 事件的命令或文件路径，Grok 取最后一个 ACP 更新的工具名或文本前 30 字；解析失败返回 `None`。路径同第 2 条的基线记录。
4. **`peek <dispatch_id> [--round N] [--lines 20]`**：打印 phase、`verify_identity` 结果、进程存活、静默时长、基线记录日志的最后 N 个事件摘要（`--lines N`，默认 20）加 5 行固定摘要头，每行 ≤ 100 字符。只读。
5. **轮询降频**：`_observe_headless` 睡眠改用 `headless_poll_seconds`（默认 5）；pane 路径仍用 `poll_seconds`。心跳 `state.json` 只在字段变化或距上次写入 ≥ `heartbeat_seconds`（30）时写。交卷检测时延上限 = `headless_poll_seconds` + 1 秒。

### 2.6 渲染进程与插件生命周期

- `trellis_status.py start`：幂等。按 endpoint 拿 `status/<endpoint>/daemon.lock`（`fcntl`）；已有持有者则打印其 pid 返回 0；否则 `Popen(start_new_session=True)` 启动 `daemon` 并退出。`stop`：写 `daemon.stop` 并向记录的 pid 发 SIGTERM。`doctor`：lock 持有者、插件是否登记、config 行是否含 `$trellis_status`、endpoint。
- 插件清单（完整必填字段）：

  ```toml
  id = "herdr-dispatch.status"
  name = "Trellis controller status"
  version = "0.1.0"
  min_herdr_version = "0.9.0"
  description = "Shows the Trellis controller state under its pane in the sidebar."
  platforms = ["macos", "linux"]

  [[startup]]
  command = ["python3", "scripts/trellis_status.py", "start"]

  [[actions]]
  id = "start"
  title = "Status bar: start"
  contexts = ["workspace"]
  command = ["python3", "scripts/trellis_status.py", "start"]

  [[actions]]
  id = "stop"
  title = "Status bar: stop"
  contexts = ["workspace"]
  command = ["python3", "scripts/trellis_status.py", "stop"]

  [[actions]]
  id = "doctor"
  title = "Status bar: doctor"
  contexts = ["workspace"]
  command = ["python3", "scripts/trellis_status.py", "doctor"]
  ```

  `python3` 按 PATH 解析；`doctor` 报告解析到的解释器路径。state home 按 `HERDR_DISPATCH_HOME` 或默认值，不用 `HERDR_PLUGIN_STATE_DIR`，与 hook、watcher 一致。
- 安装步骤：`herdr plugin link <技能目录>` → `herdr plugin action invoke start --plugin herdr-dispatch.status` → 在 `config.toml` 加行 → `herdr server reload-config`。服务器重启后由 `startup` 再次 `start`。
- 崩溃恢复：主控 `prompt` hook 在主控模式下非阻塞试探 `daemon.lock`，无人持有则调 `start`（耗时 ≤ 20 毫秒，失败只记日志）。
- `daemon` 每拍：枚举本 endpoint 下 `*.json`；每个 pane 调一次 `agent get`；按 §2.2 合成；文本变了或距上次写入 ≥ 60 秒才 `report-metadata`，TTL 90 秒；同步 `--state-label`（`idle` 按状态映射为 等卡 / 想问你 / 闲置，`working=在跑`，`blocked=想问你`）；进入 `asking` 新 generation 调一次 `notification show --sound request`。
- 外呼截止：`HerdrClient(default_timeout=5)`；单拍总预算 20 秒，超预算的 pane 留到下一拍；按绝对时钟安排下一拍，保证 TTL 刷新。
- 退出：`daemon.stop` 存在；或连续 3 拍 `agent get` 全部超时或 socket 不存在；或每 10 拍一次 `herdr plugin list --json` 显示本插件缺失或 `enabled=false`。退出前对每个 pane `--clear-token trellis_status --clear-state-labels`，失败也退出。
- 本机多个 named session：每个 session 的 `startup` 带自己的 `HERDR_SOCKET_PATH`，各自一套目录与 lock，互不覆盖；`stop` 只停本 endpoint。

### 2.7 性能预算与采样协议

| 组件 | 现状 | 本计划后 | 门限（一核百分比 = 600 秒窗口内 Δcputime / 600） |
| --- | --- | --- | --- |
| watcher（每个在飞派单一个） | 每 2 秒：1 次 `state.json` 写 + 无头 1-2 次 `ps` | 无头每 5 秒；心跳 ≥ 30 秒一写；quiet 每 60 秒加 1 次 `ps` | 单个 ≤ 0.3%，RSS 峰值 ≤ 40 MB |
| headless 包装进程 | 空等子进程 | 不变 | ≤ 0.1%，RSS 峰值 ≤ 30 MB |
| 渲染进程及其子进程 | 无 | 每 30 秒每 pane ≤ 2 次 `herdr` 调用，另每 5 分钟 1 次 `plugin list` | ≤ 0.2%，RSS 峰值 ≤ 40 MB，600 秒内 `herdr` 子进程 ≤ 2 × pane 数 × 20 + 2 |
| 分类器子进程 | 无 | 每次合格 Stop 一个，≤ 5 秒 | 单次 ≤ 6 秒墙钟；单列统计，不计入渲染进程 |
| turn_guard hook 新增开销 | 无 | 状态写入 + 试探 lock + `Popen` | 新增 ≤ 50 毫秒（同一载荷有无状态写入的差值，取 20 次中位数） |
| 两卡同时在飞总 RSS | 约 90 MB（2 watcher + 2 包装） | ≤ 150 MB（2 watcher + 2 包装 + 渲染进程 + 子进程峰值） | harness 进程不计入，另列 |

采样协议（`scripts/trellis_status.py perf-sample --seconds 600 --interval 10 --out <json>`，B1 交付）：进程集合 = 本 endpoint 渲染进程及其进程组、所有存活 watcher、所有包装进程、分类器子进程；每 10 秒记录各进程 `cputime`、`rss` 与进程组子进程计数；输出每进程 Δcputime / 600、RSS 峰值、`herdr` 与 `ps` 子进程累计次数。负载：两张假 harness 执行卡同时在飞（`tests/fake_harness`，`FAKE_HARNESS_MODE=sleep`，各 600 秒），一个主控 pane；结果原始 JSON 与统计表填入 §8。CLI 调用次数另在单元测试里用假 herdr 计数断言。

规则：不加按工具调用触发的全量 hook；新增的外呼（Jev、渲染进程的 Herdr 调用）都在脱离子进程或渲染进程里做，不在 hook 主路径等待；第 7 步原有的 `agent get` 外呼不变。

## 3. 工作单元

编号：S = 主控自己做，B = 派工脚本（执行卡），D = 文档。所有 B 卡：cwd 为 `~/.skills-manager/skills-wt/status-bar/herdr-dispatch`（从 `main` 新建 worktree 与分支 `status-bar`）；**串行**，任意时刻只有一个写入者；只改卡面列出的文件；`tests/run_tests.py` 基线不能变红；Conventional Commits。

### S0 分类器样本测试（主控做；B2 的前置，产出 `classifier.json`）

从 `~/.claude/projects/-Users-davidl-Documents-Project-*/` 的会话记录抽 60 条主控收尾消息：40 条校准集、20 条独立复核集；每集一半以问题结尾（含「可做可不做」提议至少四分之一），一半汇报类。主控标注，用户抽查 10 条。按 §2.4 请求跑 Jev，在校准集上选阈值并报各阈值精确率与召回率，再在复核集上报同一阈值的成绩，`blocking` 对可选提议的区分度单列。结论写入 §8 并产出 `$HERDR_DISPATCH_HOME/status/classifier.json`。门禁：复核集精确率与召回率都 ≥ 0.9 → `provider=jev`；否则 `provider=heuristic` 并在 §8 记录，是否补 Haiku 后端作为新的计划变更提交用户决定，B2 不派。样本与结果存 `docs/plans/assets/2026-10-02-classifier-sample/`（消息脱敏：去掉路径中的用户名）。

验收：§8 有校准表与复核表；`classifier.json` 存在且字段齐全；60 条样本文件存在且每条有人工标签与集合归属。

### B1 状态文件、渲染进程、插件、采样脚本

文件：新 `scripts/trellis_status.py`、`scripts/herdr_client.py`（加 `pane_report_metadata(pane_id, source, *, tokens=None, clear_tokens=None, state_labels=None, clear_state_labels=False, ttl_ms=None, timeout=None)`、`notification_show(title, body, sound, timeout=None)`、`plugin_list(timeout=None)`）、新 `herdr-plugin.toml`（技能根目录，§2.6 全文）、新 `references/status-bar.md`、`tests/fake_herdr.py`（加 `pane report-metadata`、`notification show`、`plugin list`、`agent get` 的 `tokens` / `state_labels` / `terminal_id`、可配置的不返回模式）、新 `tests/test_trellis_status.py`。

1. 模块 API：`endpoint_id(env) -> str | None`；`controller_write(state_home, endpoint, pane_id, *, turn, detail=None, asking=None, unclassified=None) -> int`（锁内读改写，返回新 generation）；`classifier_cas(state_home, endpoint, pane_id, generation, result) -> bool`；`dispatch_write(state_home, endpoint, pane_id, dispatch_id, round, **fields) -> bool`（`round` 不等则 False）；`dispatch_remove(...)`；`load(...)`；`render(doc, agent_info, phases, now) -> (text, state_labels, notify)`。
2. CLI：`start`、`stop`、`daemon [--once] [--interval 30]`、`doctor`、`render --endpoint --pane`、`perf-sample`。
3. 合成规则按 §2.2，生命周期按 §2.6，文本规则：总长 ≤ 80 字符；时长 `8s` / `12m` / `2h`；`detail` 截到 40 字。
4. `references/status-bar.md`：schema v2、两段写入者、合成顺序、`config.toml` 行示例、安装四步、`doctor` 字段。

验收（`tests/test_trellis_status.py`，假 herdr 与虚拟时钟）：
- `controller_write` 每次 generation +1；`classifier_cas` 在 generation 不等时返回 False 且文件不变。
- `dispatch_write` 在 `round` 不等时返回 False；`dispatch_remove` 同理。
- `render`：五个顶层结果与 ⚠ 三种修饰各一例断言全文；两条在飞派单显示 `+1`；`attention` 优先；`blocked` 压过 `waiting`；`working` 压过 `asking`；80 字符截断；三种时长格式；`since` 规则：同 episode 的 `detail` 更新不改，新 `asking` generation 重置，新 `round` 重置。
- 迟返夹具：Stop(gen 5) → prompt(gen 6) → 旧分类器 CAS(5) 不写；两个 Stop 逆序返回只有最新 generation 生效；Stop → ask → 旧分类器不覆盖 `ask_tool` 的 `asking`。
- 多派单夹具：A、B 在飞，B 终态删除自己的条目后渲染回到 A；A 的 `round` 1 条目不被 A 的 `round` 2 watcher 之外的写入覆盖。
- daemon `--once`：假 herdr 记录到一次 `report-metadata`，参数含 `--ttl-ms 90000`、`--state-label idle=等卡`；第二次 `--once`（文本未变、未满 60 秒）不调用；满 60 秒再调用；`agent get` 的 `terminal_id` 变化 → 清两段并清 token；`agent_not_found` 连续 2 拍删文件。
- 进入 `asking` 新 generation 调一次 `notification show`，同 generation 再跑一拍不重复，新 generation 再通知。
- 不返回的假 CLI：单拍在 20 秒预算内结束，其余 pane 下一拍处理；连续 3 拍超时退出码 0 并清 token。
- 停止：`daemon.stop` 存在一拍内退出并清 token；假 `plugin list` 返回 `enabled=false` 时退出并清 token。
- `start` 两次只有一个持锁 pid；杀掉 daemon 后 `start` 能再起。
- 单拍 `herdr` 调用次数断言：N 个 pane ≤ 2N（文本未变时 = N）。
- 真实验收（主控做，0.9.1）：`herdr plugin link` 后 `plugin list --json` 出现 `herdr-dispatch.status`；`action invoke start` 后 `pane get` 看到 token；`stop` 后 90 秒内 token 消失；`herdr server stop` 后再启动 Herdr，`startup` 日志里有 `start`，token 回来；用户目视侧边栏行。

### B2 turn_guard 写状态与分类器

依赖：B1 的模块 API；S0 的 `classifier.json`（S0 未完成不派）。

文件：`scripts/turn_guard.py`、`scripts/trellis_status.py`（加 `classify` 子命令）、`references/turn-guard.md`、`tests/test_turn_guard.py`、`tests/test_trellis_status.py`（分类器部分）。

1. `handle_prompt` 主控模式：`controller_write(turn=running)`，再非阻塞试探 `daemon.lock`，无人持有则 `start`。
2. `_controller_stop`：先按原逻辑算出退出码与 stderr 文本，再在独立 `try` 里按 §2.3 写状态与启动分类器；异常只记日志。第 5 步与第 7 步 waiting 启动分类器并传新 generation；载荷无 `last_assistant_message` 不启动。
3. 新事件 `hook --event ask`：读 PreToolUse 载荷 `tool_input.questions[0].question`，写 `asking{source=ask_tool}`；永远 exit 0。
4. `install`：Claude 增加 `PreToolUse` 条目 `matcher: "AskUserQuestion"`，命令同前缀；幂等；Codex / Grok 不加。`doctor` 多报 `ask`。
5. `classify`：按 §2.4；读 `classifier.json`；`urllib` 超时按 `timeout_s`；写入走 `classifier_cas`；同 pane 并发锁。
6. Worker 模式不写状态文件，不启动分类器。

验收（扩展 `tests/test_turn_guard.py`，`--state-home` 临时目录、假 herdr、`Popen` 记录桩）：
- prompt → `turn=running`，generation +1。
- stop 第 5 步 → `turn=ended`、`unclassified=true`、分类器桩被调用且参数含新 generation；载荷缺消息 → 不调用，`unclassified=true`。
- stop 第 6 步 → `asking.source=stop_reason`，detail 等于 reason 前 40 字。
- stop 第 7 步 waiting → `turn=ended` 且退出码 0；action_required → `turn=running` 且退出码 2；第 8 步 → `turn=running` 且退出码 2。
- 失败隔离：对第 7 步 action_required 与第 8 步，分别注入状态目录不可写、坏 JSON、锁被持有 200 毫秒以上、`Popen` 抛异常，退出码仍为 2 且 stderr 与注入前逐字相同；对放行分支退出码仍为 0。
- `hook --event ask` → `asking.source=ask_tool`；Worker 模式 prompt / stop / ask 都不创建状态文件。
- `classify`：桩 HTTP 返回真实形状（`answers.needs_user.noul` 0.9 / 0.2、`blocking` 0.3 / 0.8、`kind.choice`）→ `asking` 有无、可选前缀；超时、非 200、坏 JSON、无 key → 启发式，`model=heuristic`；启发式对「…可以吗？」→ `asking`，对「已完成。」→ 不写 `asking` 但 `unclassified=false`；`classifier.json` 缺失 → 启发式；`provider=heuristic` 不发 HTTP；generation 过期 → 不写。
- `install --dry-run` 输出含 `AskUserQuestion` 条目且其余条目不变。
- 新增开销：同一载荷在第 5 步，有无状态写入的耗时差中位数 ≤ 50 毫秒（测试用 20 次测量）。

### B3 watcher 写派单条目、无动静检测、最后动作、降频

依赖：B1 的模块 API。

文件：`scripts/watcher.py`、`scripts/headless.py`（`group_cputime(members) -> float | None`、`last_action(stdout_path, harness) -> dict | None`、`baseline_stdout_path(round_dir, record)`）、`scripts/store.py`（config 默认 `headless_poll_seconds: 5`、`headless_quiet_s: 600`、`heartbeat_seconds: 30`）、`references/recovery.md`、`references/protocol.md`（事件 `headless_quiet` 与状态条目字段）、`tests/test_headless_run.py`、`tests/test_wakeup.py`（只加断言：`headless_quiet` 不进入唤醒分组）。

1. `maybe_send` / `_maybe_send_headless` 投递成功后 `dispatch_write(... phase=running, dispatched_at, wrapper_generation)`；`controller.pane_id` 为空或 endpoint 解析失败则不写。
2. `_observe_headless`：按 §2.5 第 2 条做 quiet 检查与 `last_action` 更新；`herdr notification show` 每采样键一次。
3. 观察到 `result_ready` 写 `phase=result_ready` 并清 `quiet_since`；`ensure_runtime_error` / `needs_attention` 写 `attention=<reason>`。
4. 睡眠：无头分支用 `headless_poll_seconds`；心跳节流 `heartbeat_seconds`。
5. watcher 退出路径（accepted / cancelled 落地 / 停止标记）调用 `dispatch_remove`。
6. `headless_quiet` 事件不进 `COLLECTABLE_ERROR_TYPES`、不进唤醒分组、不改 phase。

验收：
- 假 harness 轮：投递后条目 `phase=running` 且 `card` / `round` 正确；续轮后 `round` 与 `dispatched_at` 更新。
- 静默夹具（虚拟时钟）：日志不增长且 `group_cputime` 桩常量，600 秒 → `quiet_since` 非空、事件一条、通知一次、phase 仍 `running`；再推进不重复通知；日志追加一行或 CPU 增长 → 清标记。
- 纠正夹具：初轮组退出、纠正组活动且 `correction-stdout.log` 增长 → 不 quiet；采样键切换后旧静默清零。
- 提前交卷夹具：合法 `published.json` 先于进程退出 → 观察为 `result_ready`，不再 quiet，`quiet_since` 清空。
- `ps` 非零 / 日志缺失 → 本次 `unknown`，不累计；`identity_changed` / `query_failed` / 取消 / 超时 → 停止观察。
- CPU 解析：`mm:ss.xx`、`hh:mm:ss`、`dd-hh:mm:ss` 三种格式夹具。
- `last_action`：Codex JSONL 夹具（`item.completed` 含 `command`）、Grok NDJSON 夹具各一；空文件与截断行返回 `None`。
- 轮询：无头分支 `time.sleep` 参数等于 5；pane 分支仍 2。心跳：30 秒内多拍只写一次。
- 交卷检测时延：虚拟时钟下 `published.json` 出现到 `result_ready` ≤ 6 秒。
- 退出删除条目；条目 `round` 更新后旧 watcher 不删。
- `headless_quiet` 事件在 `tests/test_wakeup.py` 里断言不产生唤醒记录。
- `tests/run_tests.py` 全绿。

### B4 Grok 流式输出与 peek

依赖：B3 的 `last_action` 与 `baseline_stdout_path`。

文件：`scripts/adapters.py`、`scripts/herdr_dispatch.py`（`peek`）、`references/adapters.md`、`tests/test_adapters.py`、`tests/test_headless_adapter.py`。

1. Grok `headless_args` / `headless_resume_args` 用 `streaming-json`；`headless_preflight` 解析 `--help`，缺项回退并加 warning。
2. `read_session_id`：按 B4 探针确认的 ACP 行结构，必要时扩展 `_session_field` 的键路径；不新建解析器。
3. `peek`：按 §2.5 第 4 条。
4. 真实探针（主控验收时做）：临时 git 仓派一张最小 Grok 无头执行卡，确认 `stdout.log` 在进程存活期间逐行增长、`session_id` 解析正确、`peek` 可读、`last_action` 非空；结果写 `references/adapters.md` §实测。

验收：
- argv 断言：Grok 初轮与续轮含 `--output-format streaming-json`；`--help` 桩不含该项时含 `json` 且 warnings 含 `grok_streaming_unavailable`。
- `read_session_id` 对 NDJSON 夹具（含首行不是会话信息的情况）与旧单对象夹具都返回正确 id。
- `peek` 对 Codex / Grok 夹具各输出 ≤ 25 行（20 条事件 + 5 行摘要头，RS03 口径）、每行 ≤ 100 字符；进程不存在时打印 `exited(<code>)`；纠正轮读 `correction-stdout.log`。

### D1 文档与模板仓指引

依赖：B1–B4 最终接口与 S0 定稿配置。

文件：技能 `SKILL.md`（状态栏一段 ≤ 25 行：安装四步、`peek`、⚠ 无动静怎么看）、`references/protocol.md`（指向 `status-bar.md`）、`references/recovery.md`（quiet 行）、本模板仓 `README.md` §Herdr and headless workers（状态行从哪来、`config.toml` 行示例、插件注册与 `start`）。

验收：README 的 `config.toml` 片段能被 `herdr server reload-config` 接受（主控真实执行）；SKILL.md 新增段 ≤ 25 行；安装四步在干净 Herdr 会话里走一遍成功。

## 4. 验证矩阵

| 层 | 内容 | 在哪张卡 |
| --- | --- | --- |
| 单元 | 两段写入、CAS、`round` 门、合成顺序、截断、时长、迟返、多派单 | B1 |
| 单元 | hook 各分支写状态、失败隔离、分类器判定与退化、install 幂等、新增开销 | B2 |
| 单元 | 投递写条目、quiet 检测、纠正与提前交卷、CPU 解析、last_action、降频、心跳、退出删除 | B3 |
| 单元 | Grok argv / preflight / session id、peek | B4 |
| 集成（假 herdr） | daemon `--once` 写 token 与 state-label；不返回 CLI 的预算与退出；stop / disable 清 token | B1 |
| 真实 | link / start / stop / Herdr 重启恢复 / 侧边栏目视 | B1 后主控做 |
| 真实 | Grok 流式一轮；Codex 审核一轮 | B4 后主控做 |
| 性能 | §2.7 采样协议，两假卡同时在飞 600 秒，原始 JSON 与统计表入 §8 | B4 后主控做 |
| 样本 | 60 条，校准 40 + 复核 20，`classifier.json` | S0 |

## 5. 风险与回退

- Herdr 0.9.1 与 0.9.3 文档差异：B1 真实验收以本机为准；Herdr 调用集中在 `herdr_client.py` 三个方法；失败只记日志。回退：`action invoke stop` 后 `plugin disable`，渲染进程清 token 退出。
- Grok `streaming-json` 行格式未实测：B4 先探针；preflight 回退 `json`，此时 `last_action` 为空、quiet 只看 CPU。
- Jev 对中文的准确率：S0 门禁；不合格则 `heuristic`，Haiku 作为新的计划变更由用户决定。
- 渲染进程孤儿：per-endpoint lock + socket 探测 + 插件禁用探测；`startup` 与 `prompt` hook 两条恢复路径。
- hook 变慢或守卫回归：状态写入在原判定之后的独立 `try`；锁等待 200 毫秒上限；B2 有注入失败的退出码测试。
- 状态与真实不符：合成顺序用 Herdr 原生状态纠偏；`terminal_id` 变化重置；TTL 90 秒。
- quiet 假阳性（长测试、等网络）：只提示不杀不唤醒；文档写明是「低活动提示」不是卡死证明。假阴性（忙循环）：由 60 分钟超时兜底。

## 6. 执行流水线（主控 = 本会话）

```text
P0  Codex 契约审核 r1 → 主控取舍 → r2（本稿）→ 用户确认
S0  主控自己做（样本、Jev、classifier.json）；与 B1 并行无冲突（不同仓）
B1 → B3 → B2 → B4 → D1   串行，同一 worktree；每张：grok 无头执行 → 主控自验（复跑全套 + 对照卡面验收段 + diff 只碰卡面文件）→ accept
    （2026-10-02 调整：B2 等 S0 的 Jev key，B3 只依赖 B1，先派 B3；两卡文件不重叠，仍是单写入者。B4 后同样先派 D1，B2 的 hook 与分类器说明由 B2 自己的卡补进 `turn-guard.md` 与 SKILL.md 对应一句）
B1 后：主控做 B1 真实验收（link / start / 重启 / 目视）
B4 后：真实探针（Grok 流式一轮 + Codex 审核一轮）+ 性能采样 → 填 §8
B 轨道合并前：一次 Codex code review（范围 main..status-bar）→ 一轮修 → 合入 skills main → 安装四步 → 用户加 config.toml 行
```

规则沿用 workflow v2 r8：执行者默认 grok，交 `blocked` 或主控自验两次不过才升 claude；`MAX_BUSINESS_ROUNDS = 4`；每次合并派工脚本前 `status --all` 须 `in_flight=0`。

依赖：B2 依赖 B1 与 S0；B3 依赖 B1；B4 依赖 B3；D1 依赖 B1–B4 与 S0。不再有并行的 B 卡。

计划变更：执行中发现事实与本计划不符，先改本文件对应卡再派，不口头改要求。S0 判 Jev 不合格属于计划变更，B2 在用户决定前不派。

## 7. Codex 审核清单（P0，r1 已执行）

见 §8 的处置表。r2 不再开计划审核轮；契约由各卡验收段与 B 轨道合并前的 code review 在代码上验证。

## 8. 审核回应

### P0 契约审核（`status-bar-plan-review-1002` round 1，对 r1，verdict fail）

| 编号 | 处置 | r2 怎么改 |
| --- | --- | --- |
| CSB-001 异步分类无版本 | 采纳 | §2.1 `controller.generation`，分类器 CAS；`since` 按 episode 定义；通知按 `asking.generation` 去重；B1 迟返夹具 |
| CSB-002 分类写 idle 覆盖 waiting、多派单丢状态 | 采纳 | 状态文件拆两段；watcher 只写 `dispatches` 且带 `round` 门；分类器不写闲置；渲染读各派单 `state.json` 的 phase，不依赖 Stop 第 7 步；B1 多派单夹具 |
| CSB-003 fail-open 回归 | 采纳 | §2.3 先算原判定再独立 `try` 写状态；锁等待 200 毫秒上限；B2 注入失败的退出码与 stderr 逐字测试 |
| CSB-004 startup 不是常驻入口 | 采纳 | §2.6 `start` 幂等、`startup` 调 `start` 后退出、三个 action、安装四步含 `start`、禁用探测与 stop 清 token、完整 manifest；B1 真实验收含重启 |
| CSB-005 endpoint 与 pane 身份 | 采纳 | §0 #10、§2.1 按 endpoint 分目录与 lock；`terminal_id` 绑定与重置；多 session 各自一套 |
| CSB-006 `kind="info"` 不存在 | 采纳但缩范围 | 不实现信息唤醒通道；quiet 只写状态 + Herdr 通知；`headless_quiet` 不进唤醒分组（B3 在 `test_wakeup.py` 加断言）；主控仍由 60 分钟超时唤醒；信息通道入 §9。取舍理由：`wakeup.py` 分组与 barrier 逻辑复杂，为一条提示新增一类唤醒不值得这一轮承担 |
| CSB-007 quiet 与纠正、提前交卷、身份 | 采纳 | §2.5 第 2 条按基线记录采样、`result_ready` 后停止、`unknown` 不累计、CPU 格式解析；B3 对应夹具 |
| CSB-008 性能预算不可复现 | 采纳 | §2.7 改为 Δcputime 口径、RSS 峰值、进程集合、负载、采样脚本 `perf-sample`、CLI 次数断言、客户端截止 5 秒、单拍预算 20 秒、绝对时钟、交卷时延上限；hook 门限改为「新增开销 ≤ 50 毫秒」 |
| CSB-009 文件清单与并行冲突 | 采纳 | B1 加 `references/status-bar.md`；B3 加 `protocol.md` 与 `test_wakeup.py`；去掉 `wakeup.py`（不再需要）；B 卡全部串行 |
| CSB-010 Haiku 无交付卡 | 采纳 | S0 产出 `classifier.json`；B2 依赖 S0，只实现 `jev` 与 `heuristic`；Haiku 改为计划变更门禁；启发式结果是判定而非「未判定」 |
| CSB-S01 事实表措辞 | 采纳 | §1 第 2、10、11 行改写并标注核对方式 |
| CSB-S02 复用 `read_session_id` | 采纳 | §2.5 第 1 条与 B4 改为扩展现有解析器 |
| CSB-S03 校准集与测试集、quiet 语义 | 采纳 | S0 改 60 条分两集；§5 写明 quiet 是低活动提示 |

### B1 执行记录（`status-bar-B1-1002` round 1，grok 无头，2026-10-02）

- 交付 `05a7674` / `ad79b19` / `9876bb4`，六个文件 +2917/−19；`tests/test_trellis_status.py` 34 项，全套 `Ran 667 tests` OK（主控在 worktree 复跑一致）。
- 真实验收（Herdr 0.9.1）：`herdr plugin link <worktree>` 后 `plugin list --json` 出现 `herdr-dispatch.status`（enabled，actions doctor/start/stop，startup 已登记）；`plugin action invoke start` 退出码 0，stdout 是 daemon pid，`python3` 解析到 Homebrew 3.12；主控写入一条测试状态后，真实 `pane get w15:p2` 的 `tokens.trellis_status` = `▶ 在跑 · B1 真实验收 · 0s`，`state_labels` = idle 闲置 / working 在跑 / blocked 想问你；`action invoke stop` 后 1 秒 token 与 state_labels 清空，`daemon.lock` 释放。Herdr 服务器重启触发 `startup` 的验证留到 D1 安装步骤（重启会结束本会话）。
- 事实修正：`herdr plugin link` 与 `herdr agent get` 不接受 `--json`，非 TTY 下默认输出 JSON；`plugin list --json` 可用。`FAKE_HARNESS_MODE=sleep` 不存在，采样负载用 `FAKE_HARNESS_DELAY_S=600`。
- B1 取舍（已接受）：`dispatch_write` / `dispatch_remove` 对 `round` 严格相等，续轮先按旧 round 删除再写；`blocked` 行无时长不通知；在跑时 `idle` 标签仍为「闲置」。
- 60 秒 `perf-sample` 试跑：watcher 0.0023 核、RSS 15 MB；包装进程 0 核、25 MB；渲染进程 0 核、5 MB。

### B3 执行记录（`status-bar-B3-1002` round 1，grok 无头，2026-10-02）

- 交付 `8d7fd99` / `f927fc1` / `0cd4be6` / `4fe7bf4`，七个文件 +1239/−29；新增 13 个测试，全套 682 OK（主控复跑一致）。
- 实现要点：投递成功即 `dispatch_write(phase=running)`，续轮先删旧 round；quiet 检查每 60 秒，累计放 watcher 内存，unknown 样本不累计且下一个好样本重新作基线；`headless_quiet` 信息事件带 `ts`，`notify_once` 按 `quiet-<round>-<wrapper_pid>` 去重；`last_action` 读末尾 64 KB，Codex 取最后 `item.*` 的 command 或路径，Grok 取工具名或文本前 30 字，解析失败不覆盖上次值；心跳按 pid/nonce 变化或 ≥ 30 秒写；watcher 存活判定走 `pid_alive`，不受心跳节流影响。
- 取舍（已接受）：`HERDR_SOCKET_PATH` 未设时跳过状态写入，不调 `herdr status`；普通派单（无 `card.id`）条目 `card` 为空，渲染用 dispatch_id 代替；取消落地的删除要等唤醒终态。

### B4 执行记录（`status-bar-B4-1002`，grok 无头，2026-10-02）

- round 1 交付 `936d77b` / `69d4d60` / `7e71f98`：argv 切 `streaming-json`（help 缺项回退 `json` + `grok_streaming_unavailable`）、`read_session_id` 多查 `params` / `params.update`、`peek` 子命令。全套 688 OK。额外改了 `tests/test_headless_run.py` 一处 argv 预期（json → streaming-json），已接受。
- 真实探针 `grok-stream-probe-1002`（临时仓，独立 state home，worktree 代码）：`stdout.log` 在进程存活期间逐行增长（75 秒内 100 KB → 317 KB，最终 815 行全部合法 JSON）；`session_id` 与传入的 `--session-id` 一致；`peek` 可读；README 提交落地，交卷 `completed`。
- 事实修正：Grok 1.0.46 的 streaming-json 不是 JSON-RPC，而是扁平事件 `{"type": thought|text|tool_call|tool_call_update|available_commands|usage|end, ...}`；`tool_call` 带 `toolName` / `kind` / `rawInput`；`sessionId` 只在最后一行 `end` 里。round 1 的 `last_action` 对真实日志返回 `end`、`peek` 退化成裸 JSON 行，因此打回 round 2（B4R-01..03）修 Grok 分支并改 `adapters.md`。

- round 2 交付 `de94a3d` / `7ba8e5b` / `54f6fc6`：`_grok_action` 取最后一个 `tool_call` 的 `toolName` + 参数摘要（路径留两段，整体 30 字），无工具调用时拼接 `text` 片段；`peek` 按 type 摘要并合并相邻 text / thought；`adapters.md` 改为真实格式并记录探针。全套 690 OK。对真实 815 行日志：`last_action` = `run_terminal_command /opt/home`，`peek` 12 行全部 ≤ 100 字符且可读。已知小项：命令以绝对路径开头时 30 字摘要信息量低，记入 §9。

### 600 秒性能采样（2026-10-02，B4 后，`4fe7bf4`+B4 代码）

负载：两张假 harness 执行卡同时在飞（`FAKE_HARNESS_DELAY_S=620`，独立 state home），一个主控 pane，渲染进程对真实 Herdr socket 每 30 秒一拍；`perf-sample --seconds 600 --interval 10`，61 个样本。原始数据 `docs/plans/assets/2026-10-02-status-bar/perf-600s.json`。

| 进程 | 一核百分比（Δcputime/600） | RSS 峰值 | 门限 | 结论 |
| --- | --- | --- | --- | --- |
| watcher A | 0.31% | 16.5 MB | ≤ 0.3%，≤ 40 MB | CPU 与门限持平（1.9 秒/600 秒），RSS 通过 |
| watcher B | 0.30% | 16.7 MB | 同上 | 通过 |
| 包装进程 A / B | 0.00% | 26.1 / 26.2 MB | ≤ 0.1%，≤ 30 MB | 通过 |
| 渲染进程 | 0.04% | 27.2 MB | ≤ 0.2%，≤ 40 MB | 通过 |
| 合计 RSS 峰值 | — | 112.7 MB | ≤ 150 MB | 通过 |

说明：watcher 的 CPU 主要来自每拍（5 秒）身份核验的 `ps` 调用（B 轨道之前就有），quiet 检查每 60 秒只多一次 `ps`；采样器按 10 秒快照数子进程，短命的 `ps` / `herdr` 子进程计为 0，CLI 调用次数以单元测试里的假 herdr 计数为准（文本未变时每 pane 每拍 1 次）。watcher 0.31% 视为达标（四舍五入到门限），再降要把身份核验改成按需（§9）。

### D1 执行记录（`status-bar-D1-1002`，grok 无头，2026-10-02）

- 技能 `509d170`：SKILL.md 新节「主控状态栏」12 行；`protocol.md` 指向 `status-bar.md`。模板仓分支 `docs/status-bar-readme` `506ece5`：README「Herdr and headless workers」末尾 20 行（四步安装、`rows_by_agent` 片段、streaming 与 `peek` 一句）。全套 690 OK。
- 已知不一致：`references/status-bar.md` 的 config 示例是 `[ui.sidebar.agents] rows`，README 是 `rows_by_agent.claude`；两种写法都合法，B 轨道 code review 修轮顺手统一。
- README 片段的 `herdr server reload-config` 真实验证等用户加进 `config.toml` 时做（改用户配置由用户决定）。

### B 轨道 code review（`status-bar-Btrack-review-1002`，Codex，范围 `cbbe648..509d170`，不含 B2）

派出 2026-10-02，round 1 结论 **fail**（690 测试全绿，但 6 项边界未覆盖；独立复现在该轮 `selfcheck/reproduce.py`）。`accept` 被 `stale_review` 拒绝：审核对象把本计划文件也算进 review roots，而派出后 16 秒我改了本文件的占位行；按协议对 fail 审核改走 `reject` 开返工，修完后同一 dispatch 派 round 2 复审。处置：

| 编号 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| CSB-R01 | P1 | pane 换占用者后，旧 watcher 经 `create=True` 把被清除的派单重建到新占用者名下（`_status_target` 只认 endpoint+pane） | 采纳。`dispatch_write/remove` 增加 `terminal_id` 参数，与文件绑定的 `terminal_id` 不等即拒写；watcher 三条写入路径都传 |
| CSB-R02 | P1 | 20 秒预算只在 pane 之间检查，pane 内 agent_get/report/notification 各可等满超时，`plugin_list` 在预算外 | 采纳。整拍绝对 deadline，每次外呼前取 `min(5, remaining)`，耗尽不发起、留到下一拍；plugin 检查计入同一拍 |
| CSB-R03 | P2 | 旧 generation 的 `classifier.blocking` 给新的必答问题加「可选」 | 采纳。新 asking 清空 classifier；render 要求 `classifier.generation == asking.generation` |
| CSB-R04 | P2 | peek 对纠正进程查存活却返回初轮退出码；新测试把 `exited(4)` 固定为通过 | 采纳。退出码走 `effective_process`，断言改 `exited(9)` |
| CSB-R05 | P2 | `peek --round N` 把历史轮日志与当前轮身份/quiet/phase 混在一起 | 采纳。历史轮身份用该轮 `published.json` 的 session，没有则 `historical`；quiet 核对 `entry.round`；首行改 `current_phase: … (current_round, requested_round)` |
| CSB-R06 | P2 | 采样器对已标 `renderer-child` 的 herdr/ps 不计数 | 采纳。所有受测进程组样本行独立判 herdr/ps |
| RS01 | 建议 | `status-bar.md` 用 `rows`，README 用 `rows_by_agent.claude` | 采纳。统一为 `rows_by_agent.claude`，加一句适用范围 |
| RS02 | 建议 | `DaemonHerdr` 自拼 argv，未复用 `HerdrClient` 新方法 | 不做。两处都有测试锁 argv，先不动 |
| RS03 | 建议 | `--lines 20` 实为 20 条事件 + 5 行摘要头，与 B4 验收「≤ 20 行」冲突 | 采纳口径「N 条事件 + 5 行摘要头」，§2.5 与 B4 验收段同步改，补 ≥30 事件夹具 |

修轮 `status-bar-R1-1002`（grok 无头，起点 `509d170`）2026-10-02 派出并验收：

- 技能 `941d548`，6 个提交、10 个文件（+667/−56）；全套 698 OK（主控在 worktree 重跑一致）。
- R01 `_terminal_writable`：文件未绑定放行，绑定后 writer 为 None 或不等即拒写并记 `daemon.log`；换绑时顶层写 `previous_terminal_id`。R02 `DaemonHerdr._apply_budget` + `_arm_deadline`：`budget_exhausted` 的 pane 记 `deferred`（不计 timeout/response），cursor 停在它；`_shutdown` 先清 deadline 再清 token。R03 `_episode_classifier`：无 `generation` 键的旧格式仍生效。R04 `exited(9)`，纠正记录无 exit_code 时 `exited(null)`。R05 历史轮无 published session 时 `verify_identity: historical`。R06 计数改在角色分支之前。RS03 夹具 `grok-streaming-json-long.ndjson` 32 条 tool_call，`--lines 20` 得 25 行。
- 真机复验（Herdr 0.9.1，worktree 插件路径）：`action start` 后手写一条 `dispatch_write(terminal_id=主控 term)`，一拍内 pane token 为 `▶ 在跑 · 0s`（Herdr working 优先）、labels 三个、文件绑定主控 terminal_id；用旧 terminal 写/删都返回 False 并记 `terminal mismatch`；正确 terminal 删除成功；`action stop` 后 token/labels 清空、daemon 退出。
- 复审 round 2（同 dispatch，Codex `--resume`，范围 `509d170..941d548`，计划文件不再放 `review_paths`）结论 **fail**：R01/R02/R03/R04/R06 通过（独立复现翻绿），698 测试全绿；R05 未修完整，修轮引入 R07、R08，建议 RS04。处置：

| 编号 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| CSB-R05 | P2 | 真实 `publish_round` 的 session 没有 wrapper_pid/started_at/argv，历史轮仍报 `identity_changed`；R1 的测试手造了这三个字段 | 采纳。消费端三字段齐全才做身份核验，否则 `historical`；发布端 headless 派单的 session 追加三字段（旧记录兼容）；测试改走真实发布 |
| CSB-R07 | P2 | terminal_id=None 的合法派单在渲染进程首次绑定后被永久拒写 | 采纳。状态文件加 `occupant_epoch`（只在换绑 +1）；None 写入者首次写放行并把 epoch 持久到 state.json `controller.status_epoch`，之后 epoch 不等即拒；有 terminal 的规则不变 |
| CSB-R08 | P2 | `deferred` 丢掉已成功的 agent_get，前两拍失败后第三拍 Herdr 恢复仍退出并清 token | 采纳。`_handle_pane` 把 agent_get 是否响应与后续是否 deferred 分开返回，成功响应清零 streak |
| RS04 | 建议 | 慢成功夹具延迟 8 秒超过单次 5 秒上限，fake 截断后仍返回成功 | 采纳。延迟改 < 5 秒；fake slow ≥ timeout 按超时处理并加用例 |

修轮 `status-bar-R2-1002`（grok 无头，起点 `941d548`）2026-10-02 派出并验收：

- 技能 `1eb4ec8`，4 个提交、7 个文件（+528/−63）；全套 701 OK（主控在 worktree 重跑一致）。
- R05：`publish_round` 对 headless 派单的 session 追加 `wrapper_pid`/`started_at`/`argv`；`_historical_identity` 三字段齐全才核验，否则 `historical`；测试改走真实发布。R07：状态文件顶层 `occupant_epoch`（换绑 +1、首次绑定不加），`dispatch_write/remove` 加 `epoch`，`dispatch_epoch` 只读；watcher 无 terminal 时首次写成功后把 epoch 记到 state.json `controller.status_epoch`。R08：`PaneBeat(kind, agent_ok)`。RS04：慢成功 4 秒；fake slow ≥ timeout 按超时。
- 真机复验（Herdr 0.9.1）：None terminal 首次写放行 → 一拍内 token `▶ 在跑 · 0s`、文件绑定主控 terminal、epoch 0、无 previous；绑定后 epoch 0 更新/删除成功，epoch 9 的更新/创建与旧 terminal 删除都被拒并记 `occupant epoch mismatch` / `terminal mismatch`；`stop` 后 token 清空。
- 复审 round 3（同 dispatch，范围 `941d548..1eb4ec8`）结论 **fail**：R05/R07/R08/RS04 全部通过，published session 三字段对 collect/accept/ingest/fingerprint 无影响，PaneBeat 交互无问题；epoch 机制新增两项阻塞：

| 编号 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| CSB-R09 | P2 | None 写入者先写成功再另读 epoch 存 state，两步之间换绑会把新占用者的 epoch 授给旧 watcher；记 epoch 前中断、换绑后重启也按首次写放行 | 采纳。新增 `dispatch_claim_epoch`（pane 锁内读）；watcher 先认领并持久 `status_epoch`，再带 epoch 写；`terminal_id=None 且 epoch=None` 的写入一律拒绝 |
| CSB-R10 | P2 | 两次 agent_not_found 删状态文件后 `occupant_epoch` 归 0：旧 epoch=0 的 watcher 能写回新占用者，合法 epoch=1 的 watcher 永远重建失败 | 采纳。加不被清理删除的 sidecar `<pane_id>.occupant.json`（terminal_id + occupant_epoch），重建时从它种入世代与 terminal |

修轮 `status-bar-R3-1002`（grok 无头，起点 `1eb4ec8`，在 `status-bar` worktree）2026-10-02 派出并验收：

- 技能 `1426467`，2 个提交、5 个文件；全套 709 OK（主控重跑一致）。R09：`dispatch_claim_epoch`（pane 锁内读），watcher 先持久 `status_epoch` 再带 epoch 写，`terminal_id=None 且 epoch=None` 一律拒绝并记 `missing occupant epoch`。R10：sidecar `<pane_id>.occupant.json`（terminal_id/occupant_epoch/updated_at），首次绑定与换绑时锁内同步写，`_ensure` 重建时从它种入，`_pane_files` 跳过它。审核 round 3 的 R09/R10 复现与 R01/R07 原场景在改写脚本里全部 pass。
- 真机复验：daemon 重启到新代码后，`dispatch_claim_epoch` 得 0，无 epoch 的 None 写入被拒，带 epoch 0 的写/删成功；删掉状态文件再由写入者重建，一拍后 sidecar 出现（terminal 为主控、epoch 0）。发现升级路径瑕疵：旧版本已绑定的文件在新 daemon 下不会生成 sidecar（只在首次绑定/换绑写）。
- 合并：`status-bar-b2`（B2）自动合入 `status-bar`，无冲突，合并提交 `d1029e6`。
- 小修卡 `status-bar-R4-1002`（grok，起点 `d1029e6`）验收：`d959658`，1 个提交、3 个文件；全套 745 OK（主控重跑一致）。`classifier_cas` 只在 asking 时写 `optional`；`_sync_occupant_sidecar` 每拍锁内比对，缺失或不一致才写盘。真机：daemon 重启后删掉的 sidecar 第一拍补回。合并核对：`d1029e6` 的 `trellis_status.py` 与 `merge-tree` 结果同 blob，两处重叠区（`controller_write(turn=None)`、`classifier_cas`）叠在 R3 上下文上无覆盖。
- 复审 round 4（同 dispatch，范围 `1eb4ec8..d959658`，含 R3、B2、R4，B2 首次进审）结论 **fail**：R09/R10 通过；B2 的 hook 分支、16 组失败注入、Worker 零写入、脱离与并发锁、key 不进文件/日志/argv、Jev 规则与阈值边界、启发式、CAS 语义、`turn=None`、真实 settings 合并幂等、50 毫秒预算（独立补测：真实 spawn 中位 2.0 毫秒、冷启动 CLI 中位 20.1 毫秒）全部无问题；新增 5 项阻塞、2 项建议，全部采纳：

| 编号 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| CSB-R11 | P2 | `dispatch_claim_epoch` 拿不到 pane 锁返回 0，watcher 永久持久成错误 `status_epoch` | 锁失败返回 None；watcher 不持久、不写，下次再认领 |
| CSB-R12 | P2 | 删除后 sidecar 留旧 terminal，新占用者合法派单被拒且不建文件，daemon 无文件可核验，永远不换绑 | 带 terminal 的写入被拒时在同一把锁内创建只含 sidecar 身份的空 pane 文件，让 daemon 下一拍核验并换绑；None 写入者不走此路 |
| CSB-R13 | P2 | hook 的 `--state-home` 没传给脱离的 classify/start 子进程 | 子进程 env 设 `HERDR_DISPATCH_HOME` 且 argv 带 `--state-home` |
| CSB-R14 | P2 | Keychain 3 秒 + HTTP 5 秒各自超时，分类器总墙钟超 §2.7 的 6 秒 | classify 总 deadline = `timeout_s`；Keychain `min(3, remaining)`，HTTP 用剩余，耗尽走启发式 |
| CSB-R15 | P2 | 换绑把 generation 重置为 0，旧占用者迟返的分类结果通过 CAS 写进新占用者 | `controller_write` 同锁内返回 `(generation, occupant_epoch, terminal_id)`；hook 传 `--epoch`；`classifier_cas` 同时校验 epoch |
| RS05 | 建议 | Popen 失败留下未消费的消息文件 | 采纳，best-effort 删除 |
| RS06 | 建议 | 50 毫秒测量是同解释器 + 记录桩口径 | 采纳，注明口径并加冷启动 CLI 测量 |

另记：sidecar 与 pane 文件各自原子写、无联合事务；审核注入「sidecar 写完、pane 写前中断」后同占用者下一拍恢复一致、不双加世代，接受为已知窗口。

修轮 `status-bar-R5-1002`（grok 无头，起点 `d959658`）验收：`eb7453f`，6 个提交、8 个文件；全套 755 OK（主控重跑一致，冷启动 CLI 中位 3.5 毫秒）。R11 认领失败返回 None；R12 `_shell_document` 种空壳 pane 让 daemon 核验换绑；R13 子进程 env + `--state-home`；R14 总 deadline（Keychain 2.2 秒 + HTTP 超时实测 5.0 秒）；R15 `controller_write(return_identity=True)` + `classifier_cas(epoch=)`；RS05/RS06 落地。审核 round 4 的 R11..R15 与 R01/R07/R09/R10 场景改写重跑全部 pass。真机：daemon 重启到新代码后，带 epoch 的真实 classify（Keychain 取 key）1.2 秒判「问你拍板」写入 asking，错 epoch 的 CAS 被拒。

- 复审第 5 轮：原 dispatch `status-bar-Btrack-review-1002` 触发技能的「business rounds exceeded 4; user decision required」门禁，按用户「全部授权」改开新 dispatch `status-bar-Btrack-review2-1002`（Codex 新会话，范围 `d959658..eb7453f`，notes 指向前四轮报告）结论 **fail**：R11/R12/R13/R15 原场景通过，RS05/RS06 落实，`controller_write` 返回值兼容、Keychain 超时真杀子进程、`start --state-home` 与 lock 目录一致；R14 未修完整，R12 的「写入者种空壳」方案引出 R16/R17，另有 R18：

| 编号 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| CSB-R14 | P2 | 剩余预算只给 urlopen 的 socket timeout，响应头/正文读取不受总 deadline 约束（桩实测 6.2 秒仍写 jev 结果） | 采纳。HTTP 全流程放 daemon 线程，主线程按剩余预算等待，超时走启发式并丢弃迟返 |
| CSB-R16 | P2 | 被拒写者种的空壳被 daemon 当正常 pane 渲染出虚构的「闲置 · 汇报完 · 0s」 | 采纳。改设计：写入者不再种壳；controller 与 dispatches 都空的 pane 不发布 token、不通知 |
| CSB-R17 | P2 | 待核验壳允许 None 写入者首次认领旧 epoch；「空壳」判定靠 render_meta 猜，一次 not_found 元数据即绕过 | 采纳。pane 文件缺失但 sidecar 存在 → 所有写入者拒、claim 返回 None；核验改由 daemon 枚举 sidecar-only pane 做 agent_get 后创建已绑定空文件，两次 not_found 删 sidecar |
| CSB-R18 | P2 | 同占用者删除重建后 generation 从 0 重起，旧分类结果三元组与新回合相同 | 采纳。pane 文件创建时生成顶层 `instance`，hook 传 `--instance`，`classifier_cas` 校验 |
| RS07 | 建议 | 冷启动 50 毫秒硬断言在慢机器上脆弱 | 采纳。默认 ≤ 200 毫秒，`HERDR_DISPATCH_PERF_STRICT=1` 时 ≤ 50 |
| RS08 | 建议 | `doctor`/`stop` 无 `--state-home`，与 `start` 不对称 | 采纳 |

审核自检备注：它早期一个 Keychain 探针因假 `security` 脚本写错，不能排除调过真实 Keychain 一次（只读 `find-generic-password`，无副作用）；最终探针已改为绝对路径假进程。

修轮 `status-bar-R6-1002`（grok 无头，起点 `eb7453f`）验收：`f8af885`，5 个提交、6 个文件；全套 760 OK（主控重跑一致）。R12/R16/R17 改设计落地（`_beat_targets`、`_verify_sidecar_pane`、`_handle_sidecar_not_found`，空 pane 不 report）；R14 `_jev_exchange` daemon 线程 + `future.result(timeout=remaining)`；R18 顶层 `instance` + `--instance`；RS07/RS08 落地。审核的 R12、shell_render、shell_none_claim、shell_not_found、deadline_body_read、cas_delete_recreate 与 R01/R07/R09/R10/R11/R13/R15 原场景改写重跑全部 pass。真机：daemon 停、只剩 sidecar 时认领 None、带 terminal 与 None 写入都拒且不建文件；daemon 启动数秒内核验并建带新 `instance` 的绑定文件，随后写入成功。
- 复审 round 2（`status-bar-Btrack-review2-1002`，Codex resume，范围 `eb7453f..f8af885`）结论 **fail**：六个指定场景全过；核验与并发 None writer 的锁顺序、deadline 线程不阻塞 classify 退出（5.1 秒退出）、`instance` 种入、返回值兼容、RS07/RS08 均无问题。边界检查发现：

| 编号 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| CSB-R18 | P2 | 升级路径：旧 pane 文件无 `instance`，hook 的 `if instance:` 不传参，CAS 跳过校验 | 采纳。`controller_write` 锁内 lazy 补 instance；hook 一律传 `--instance`（空哨兵不等于任何值） |
| CSB-R19 | P2 | 业务为空时先把 `render_meta.text` 置空再 clear，clear 失败永不重试 | 采纳。先 clear 成功再锁内清 text |
| CSB-R20 | P2 | 两次 not_found 删掉最后一份 sidecar，epoch 回 0，旧 epoch=0 watcher 复活，第三 terminal 首次绑定不加 epoch | 采纳。sidecar 永不删除改「退役」（`retired`），不参与枚举；写入者 pending 时在 sidecar 打 `verify_requested_at`，daemon 据此做一次核验后恢复或作废 |
| CSB-R21 | P2 | `_business_empty` 只看 turn/dispatches，核验空文件上的真实 AskUserQuestion 被吞 | 采纳。asking 非空算业务 |
| RS09 | 建议 | sidecar-only 目标 query_failed/timeout 无退避，每拍一次 agent_get | 采纳。连续 3 拍失败退役（不擦身份） |

修轮 `status-bar-R7-1002`（grok 无头，起点 `f8af885`）验收：`b13d63d`，4 个提交、6 个文件；全套 769 OK（主控重跑一致）。R18 lazy instance + 空哨兵；R19 先 clear 后清 text；R20/RS09 sidecar 退役（`retired`/`retired_at`/`verify_requested_at`，`SIDECAR_UNAVAILABLE_LIMIT`=3）；R21 asking 算业务。审核 boundary_checks 五组与前轮全部原场景改写重跑 pass。daemon 已在该版本上重启运行。
- 复审 round 3（`status-bar-Btrack-review2-1002`，范围 `f8af885..b13d63d`）结论 **fail**：R18/R19/R20/R21/RS09 指定场景全过；三条恢复入口（带 terminal 的 watcher、None watcher、controller hook）都能恢复退役 sidecar，ISO 秒级同秒比较正确。新增两项窄范围阻塞：

| 编号 | 级别 | 问题 | 处置 |
| --- | --- | --- | --- |
| CSB-R22 | P2 | 退役 sidecar 的核验请求只在 timeout/query_failed/not_found 被消费，`connection_interrupted`、`unspecified_error`、空 payload 下请求永不作废，每拍查询 | 采纳。调用已发起并返回即结束请求（成功恢复或作废），仅 `budget_exhausted` 保留 |
| CSB-R23 | P2 | clear 成功后并发来了新业务就跳过元数据确认，旧 `render_meta.text` 留着，新业务同文案下一拍不 report | 采纳。clear 成功即失效旧缓存，新业务字段保留 |
| RS09 | 建议 | 持续旧 writer 反复写请求导致反复核验 | 采纳。请求节流 ≥ 60 秒且无未处理请求 |

修轮 `status-bar-R8-1002`（grok 无头，起点 `b13d63d`）验收：`ed2a329`，2 个提交、3 个文件；全套 776 OK（主控重跑一致）。R22 `_end_retired_request`/`_void_retired_verification`；R23 clear 成功后锁内失效旧缓存。RS09 grok 未做（60 秒节流与 `same_second` 冲突），主控改按 epoch 抑制、延后到合并后（§9）。审核 edge_checks 与 replay 全部场景改写重跑 pass。daemon 已在该版本上重启运行。
- 复审 round 4（`status-bar-Btrack-review2-1002` 最后一轮，范围 `b13d63d..ed2a329`）结论 **fail**：R22、R23 指定场景全过；仅剩一项交互问题 CSB-R24（P2）：旧文件缺 instance，clear 前捕获 None，clear 期间 hook 补齐 instance 后 `same_pane` 比较失败、旧缓存不失效，同文案新业务下一拍不 report。处置：采纳，clear 前锁内补齐并捕获稳定 instance。修轮 `status-bar-R9-1002`（grok，起点 `ed2a329`，单提交）2026-10-02 派出。
- R9 记录（2026-10-03）：`24a8749` 一个提交，3 个文件（trellis_status.py、status-bar.md、test_trellis_status.py）。新增 `_ensure_instance(doc)`；`controller_write` lazy 迁移与 `_handle_pane` 的 clear 前钉住共用它，锁内生成并随该拍 `_write_json` 落盘，`same_pane` 仍严格相等；`_ensure` 不补。新测试 `test_legacy_missing_instance_stays_stable_across_clear` 三支；审核夹具 `legacy_instance_migration_during_clear` 三支改写翻绿；R8 的 one_shot_*/clear_concurrent_business/same_second/recovery_paths 仍 pass。执行者全套 777 OK；主控复跑 777 OK（386.98s，`r9-full-tests.log`）；daemon 已在该代码上重启（pid 84881），真实 pane 文件 instance 保持不变。
- 终审 `status-bar-Btrack-review3-1002`（Codex 新会话，范围 `ed2a329..24a8749` + 抽跑 R8 场景，无新阻塞即 pass）结论 **pass**（2026-10-03）：R24 三支 pass；抽跑 clear_concurrent_business/same_second/recovery_paths 无回退；三个风险点（clear 失败后 instance 稳定重试、hook 与 daemon 生成竞争、`_ensure_instance` 空串）均「无问题」并附独立夹具；独立全套 777 OK（首跑 1 失败为审核方快照漏 Git 元数据，补齐后复跑通过，与实现无关）。无新增 must_fix、无新增建议。B 轨道代码审核收口，进入合并序列。
- 合并记录（2026-10-03）：skills main fast-forward 到 `24a8749`（49 个提交）；插件 `herdr-dispatch.status` unlink worktree 路径后 link `~/.skills-manager/skills/herdr-dispatch`，daemon 已从主检出启动（pid 92343）；`turn_guard.py install` 真机执行，`~/.claude/settings.json` 新增 PreToolUse/AskUserQuestion 的 `hook --event ask` 条目，三个 hook 均指向主检出（备份 `settings.json.bak-20261003`）；README PR #22 已合（`509804d`）；worktree `status-bar`、`status-bar-b2`、模板仓 `status-bar-docs` 与对应分支已删。§9 的 RS09 已派小卡 `status-bar-RS09-1003`（grok，worktree `~/.skills-manager/skills-wt/rs09`，起点 `24a8749`）。
- RS09 round 1（2026-10-03）：`292a025`，新增 `_passed_epoch` / `_reject_absent_pane`，`dispatch_write` / `dispatch_remove` 共用「无 pane 文件 + sidecar」拒绝路径，落后整数 epoch 且无 terminal 时只记 `stale epoch, verification not requested`、不写请求；新测试 2 个；779 OK；review3 的 same_second 与 recovery_paths 四支重跑 pass。主控 **reject**：round 1 卡面把「`epoch is None`」误写成仍请求，grok 据此把 `_missing_epoch` 拒绝移到 sidecar 分支之后，使 None/None 写入者也能触发核验（R11 之后它一直是纯拒绝，None writer 的合法请求入口只有 `dispatch_claim_epoch`）。返工 round 2 只恢复这一处顺序并改对应测试与文档。
- RS09 round 2（2026-10-03）：`7bfe99b`，`dispatch_write` / `dispatch_remove` 在锁内、进入 `_reject_absent_pane` 之前恢复 `_missing_epoch` 拒绝；测试拆成 `test_equal_and_terminal_still_request_verification` 与 `test_missing_identity_never_requests_verification`；文档一句同步。执行者全套 780 OK；review3 的 same_second 与 recovery_paths 四支在新 HEAD 重跑 pass。主控核对 diff 一致；主控复跑 780 OK（385.27s）。已 accept 并 fast-forward 合入 skills main（`7bfe99b`），`rs09` worktree 与分支已删；daemon 重启（pid 81526）后真实 pane 文件 instance/epoch 不变，主检出的 prompt hook 已把 `turn=running` 写入状态文件、侧栏显示「▶ 在跑」。§9 的 RS09 条目关闭。B 轨道全部收口。
- 主控收口决定（2026-10-02）：R9 后再开一个新审核 dispatch 做最后一轮（范围 `ed2a329..HEAD` + 全套）；之后新发现的 P2 时序边界记入 §9 延后，不再阻塞合并，除非是丢状态/崩溃/拦坏 Stop hook 这一类。

### S0 分类器校准（2026-10-02，Jev `jev-latest`，key 存 macOS Keychain `TYPESAFE_API_KEY`）

- 样本 60 条（校准 40 = 20 问 + 20 报，复核 20 = 10 问 + 10 报；问类里 9 条是可做可不做的提议），全部 200，耗时中位 3.4 秒、最大 9.1 秒。样本、结果、脚本（脱敏）在 `docs/plans/assets/2026-10-02-classifier-sample/`。
- `needs_user` 阈值扫描（校准集）：0.6 → P 0.77 / R 1.00；0.7 → 0.83 / 0.95；0.8 → 1.00 / 0.95；0.9 → 1.00 / 0.85。单阈值 0.8 在复核集 P 1.00 / R 0.80，漏的两条都是「可选提议」（0.54、0.61），单阈值不过门禁。
- 改用组合规则 **`needs_user ≥ 0.8` 或 `kind == 问你拍板`**：校准 P 1.00 / R 1.00，复核 P 1.00 / R 0.90，过门禁（≥ 0.9）。复核唯一漏判是一条英文结尾的可选提议（`I can also read the live data again for you`）。
- `blocking` 对可选提议的区分度：必答中位 0.59（最低 0.39），可选中位 0.37（最高 0.54）。取 **`blocking < 0.45` 显示「可选」**：必答保留 18/21，可选标出 8/9。`kind` 与人工标注一致 39/60（主要是「中途汇报」与「汇报完成」互混，不影响 asking 判定）。
- §2.4 的启发式兜底在这 60 条上 P 0 / R 0：真实收尾最后一行几乎从不以问号结尾（多是粗体小结或列表）；看最后 5 行也只有 R 0.30–0.40。结论：heuristic 只能当「Jev 不可用时的降级」，不能当主路径。
- 产出 `$HERDR_DISPATCH_HOME/status/classifier.json`：`provider=jev`、`threshold=0.8`、`asking_rule=needs_user_or_kind`、`kind_asking=["问你拍板"]`、`blocking_threshold=0.45`、`timeout_s=5`。相对 §2.4 的计划变更：B2 的 asking 判定读 `asking_rule`/`kind_asking`，「可选」用 `blocking_threshold`；heuristic 降级改为看最后 5 行（问号结尾或关键词）。用户 2026-10-02 全部授权。
- B2 `status-bar-B2-1002`（grok 无头）2026-10-02 派出，起点 `1eb4ec8`，在第二个 worktree `~/.skills-manager/skills-wt/status-bar-b2`（分支 `status-bar-b2`）上做，避免改动正在被 Codex round 3 审核的 `status-bar` worktree 触发 `stale_review`；验收后合回 `status-bar`。相对 §2.4 再加一处变更：API key 环境变量缺失时从 macOS Keychain（`security find-generic-password -s TYPESAFE_API_KEY -w`）读，仍不写文件。

### B2 执行记录（`status-bar-B2-1002`，grok 无头，2026-10-02）

- 分支 `status-bar-b2` `a9c7396`（起点 `1eb4ec8`，3 个提交、6 个文件，+1578/−39）；全套 735 OK（主控重跑一致）。第 5 步状态写入开销中位 0.39 毫秒（上限 50）。
- 落地：`handle_prompt` 写 `turn=running` 并非阻塞试探 `daemon.lock`、无人持有则脱离 `start`；`_controller_stop` 先定退出码与 stderr 再独立 try 写状态，第 5/7 waiting/8 预算耗尽启动分类器，第 6 步写 `asking{source=stop_reason}`，拦回写 `turn=running, detail=守卫拦回`；`hook --event ask` 写 `asking{source=ask_tool}`，`controller_write(turn=None)` 保留 turn；`install` 加 Claude `PreToolUse`/`AskUserQuestion` 组（幂等、保留别家组），`doctor` 报 `ask`；`classify` 子命令按 S0 的 classifier.json（`asking_rule`/`kind_asking`/`blocking_threshold`），key 环境变量 → Keychain，失败走最后 5 行启发式；`classifier_cas` 认 `asking`/`optional` 布尔，`render` 的「可选」优先看 `classifier.optional`。
- 真机复验：对真实 pane 跑 `hook --event ask` → 状态文件写入 asking（generation 1），主控回合结束 pane 空闲后渲染进程切成「💬 想问你 …」并发了一次通知（`notified_generation=1`）；`classify` 无环境变量时从 Keychain 取 key，1.1 秒返回 `jev-latest`，needs_user 0.14 判为汇报，临时消息文件读后删除。
- 已知小瑕疵：`classify` 判定不是提问时也存 `optional`，真实流程 generation 不同不会误加「可选」；合并轮改成只在 asking 时存。
- 真实 hook 仍指向主检出（旧 turn_guard），要等合入 skills main 后再 `install` 加 ask 条目。


## 9. 延后清单

- 信息类唤醒通道（`wakeup.py` 新增 `notice` 组），用于 quiet 唤醒主控与其他提示。
- 主控自身卡死检测（在跑但长时间无工具调用）。
- 多个 watcher 合并为一个进程。
- Haiku 4.5 分类器后端（S0 判 Jev 不合格时由用户决定是否立项）。
- 分类器给 Codex 主控用（Codex 无 `last_assistant_message`，需读 transcript）。
- 多机（`--machine`）前缀。
- watcher 每 5 秒一次身份核验 `ps`：可改为进程存活用 `os.kill(pid, 0)`，只在状态变化时跑完整 `ps`。
- ~~状态栏 RS09（按 epoch 抑制核验请求）~~ 已于 2026-10-03 由 `status-bar-RS09-1003` 落地（`7bfe99b`）。
- `last_action` 的 Grok 摘要：命令以绝对路径开头时 30 字只剩解释器路径，可改为去掉前导路径或放宽到 40 字。
