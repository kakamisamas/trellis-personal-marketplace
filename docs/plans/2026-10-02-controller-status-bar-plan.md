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
4. **`peek <dispatch_id> [--round N] [--lines 20]`**：打印 phase、`verify_identity` 结果、进程存活、静默时长、基线记录日志的最后 20 个事件摘要（每行 ≤ 100 字符）。只读。
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
- `peek` 对 Codex / Grok 夹具各输出 ≤ 20 行、每行 ≤ 100 字符；进程不存在时打印 `exited(<code>)`；纠正轮读 `correction-stdout.log`。

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
B1 → B2 → B3 → B4 → D1   串行，同一 worktree；每张：grok 无头执行 → 主控自验（复跑全套 + 对照卡面验收段 + diff 只碰卡面文件）→ accept
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

（S0 结果、性能采样原始数据与统计表、真实探针记录在执行时追加到本节。）

## 9. 延后清单

- 信息类唤醒通道（`wakeup.py` 新增 `notice` 组），用于 quiet 唤醒主控与其他提示。
- 主控自身卡死检测（在跑但长时间无工具调用）。
- 多个 watcher 合并为一个进程。
- Haiku 4.5 分类器后端（S0 判 Jev 不合格时由用户决定是否立项）。
- 分类器给 Codex 主控用（Codex 无 `last_assistant_message`，需读 transcript）。
- 多机（`--machine`）前缀。
