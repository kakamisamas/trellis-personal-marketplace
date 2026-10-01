# 工作流 v2 执行计划：开始即到底、脚本清理、派工执行细节

日期：2026-10-01。状态：修订版 r6（r2 按 Codex 第 1 轮修改；r3 按第 2 轮；r4 按第 3 轮；r5 按第 4 轮；r6 按 B 卡复审（第 5 轮，`workflow-v2-plan-bcards-review-1001`）的**取舍**修改，见 §8「第 5 轮」与 §9 延后清单）。r6 起不再开计划审核轮，契约由各卡的 Codex code review 在代码上验证。T1 已合入（`3f382d6`，code review 通过）；按 §6 流水线执行中。

本计划覆盖两大块：(A) Trellis 工作流改造及配套脚本/hooks；(B) 从 firstmate 吸收的派工执行细节。改动落在三处：`herdr-dispatch` 技能（派工脚本）、`trellis-personal-marketplace`（Trellis workflow 模板与 `trellis_gc.py`）、`sanctions-radar`（应用）。

## 0. 目标与边界

### 目标行为（用户视角）

```text
规划（不变：PRD/design/implement，用户审）
  └─ 用户说「开始」（= task.py start）
       └─ 执行：派卡 → 交卷 → 验收 → trellis-check → OCR
            └─ 自动、不停：spec 更新 → 完成报告 → 提交 → 归档+journal → PR → CI → squash 合并 → 脚本清理
只在四种情形停下并说明原因：
  1. 产品行为需要用户拍板   2. 缺凭据/权限
  3. CI 失败且不在本任务范围内可修   4. 工作树有归属不明的脏文件
用户可用「开始，合并前停」要求在合并前暂停一次。
```

### 三个交付物

| 交付物 | 位置 | 内容 |
| --- | --- | --- |
| 派工脚本 | `~/.skills-manager/skills/herdr-dispatch/`（git 仓 `~/.skills-manager/skills`，分支 `main`） | 落地证明与全局状态、回合守卫、轮次绑定与状态标签、单卡单 worktree、卡片渲染、清理证明、无头审核 |
| Trellis 模板 | `~/Documents/Project/trellis-personal-marketplace/`（`origin/main` @ `d78956f`，v1.5.1；herdr 卡片路由已在 PR #12/#13 上游） | 上游合并 sanctions-radar 剩余定制、「开始即到底」文案、`trellis_gc.py` 扩展、README/分发指针、发布 v1.6.0 |
| 应用 | `~/Documents/Project/sanctions-radar/` + 本机用户级 hooks | 安装新 workflow/gc/setup skill、config.yaml 生命周期 hook、用户级 Stop/UserPromptSubmit hook 注册与真实探针、真实冒烟 |

### 不做

- 不改 Trellis 自己管理的 hook 文件（`.claude/hooks/*.py`、项目级 `.claude/settings.json`、`.codex/hooks.json`、`.grok/hooks/impeccable.json`，均在 `.trellis/.template-hashes.json` 哈希跟踪）。守卫只注册在用户级。
- 不换成 firstmate；不搬它的 bash（约 31k 行，焊死在其 state 布局）。只搬 §2 列出的 5 个想法。
- 不把无头路径押在 `claude -p`（额度政策未定）。Claude 当 Worker 继续进 Herdr。
- 不处理 Ankicenter / opencode / wechat 三个项目的 workflow 升级（另起任务）。
- 不在本计划里做「同一任务多次合并」设计：合并后的补充是新的轻量任务。
- 不动 sanctions-radar `.trellis/config.yaml` 的 `registry.spec`（那是 spec 模板 `solo-baseline` 的来源，与 workflow 无关）。

### 已按默认值决定的事（用户可推翻）

1. 归档兜底阈值 7 天；兜底归档提交直接推到 `origin/<default>`（sanctions-radar 是免费版私有仓，GitHub 返回 403 表示没有分支保护）。推送前置条件见 T3 第 4 条。
2. 守卫脚本放在 `herdr-dispatch/scripts/turn_guard.py`，一份脚本，三处用户级注册。作用域绑定 `HERDR_PANE_ID`（Herdr 在每个 pane 的 shell 里导出 `HERDR_PANE_ID/HERDR_TAB_ID/HERDR_WORKSPACE_ID`，harness 及其 hook 子进程继承）；不在 Herdr 里运行时静默退出。
3. 所有 worktree 统一到 `<repo>-wt/`：任务 worktree `<repo>-wt/<MM-DD-slug>`，多卡运行 `<repo>-wt/<run_id>/<run_id>-<card>` 与 `<run_id>-integration`。
4. 无头审核用 `codex exec` / `grok -p` / `cursor-agent -p`，只用于 reviewer 角色；execute 卡仍进 Herdr。
5. 派工脚本改动在 `~/.skills-manager/skills` 的 git worktree 里做，验收后由主控快进合并到 `main`，合并前必须通过 S0 定义的「无派工在飞」检查。
6. 转向收件箱（firstmate inbox+doorbell）排最后，可裁剪。
7. Worker 的轮次身份不靠环境变量注入（`herdr agent start` 没有环境选项，agent 参数不等于子进程环境），改为 watcher 写「pane → 当前轮次」绑定文件；只有无头路径（进程由我们自己起）才用环境变量。
8. 「合并前停」记在守卫 marker 上（`turn_guard.py pause-before-merge`），不用 `task.py set-meta`（归档后 task.json 路径变化、值是字符串、active pointer 被清，都不适合承载）。
9. 守卫的「等待豁免」和「一次性 stop」都是 pane 级：一个主控 pane 在等自己派出的、监督与唤醒路径可证实有效的派工时，结束回合是正常的，与它有几个 marker 无关；`stop --reason` 是用户可见的显式停下，也按 pane 一次性消费。marker 级（每个任务）的判断只决定「是否还有未完成任务」。
10. 任务 marker 的解除条件是「任务生命周期完成 + 成果落地」两者同时成立，不是单独的 Git 内容证明（新建分支的 tip 本来就是 main 的祖先）。
11. 分工：`trellis_gc.py` 只清 `task/*` 分支/worktree，并且要求「任务生命周期已结束」的证据；`card/*`、`run/*` 的 worktree/分支只由派工脚本的 `run cleanup`（B6）清，GC 只报告不动手。Git 内容证明（祖先/树相等）只回答「内容在不在目标里」，永远不单独作为删除依据。
12. 兜底归档只认两种完成证据：task.json `status == completed`（归档中断）或本任务 `pr_url` 的 PR 已 MERGED 且 head 属于本任务分支。没有 PR 的 in_progress 任务不自动归档（SKIP `no_pr`），由用户手动归档。

## 1. 事实基线（2026-10-01 核对；Codex 第 1–3 轮已复核 §1 主要行）

| 项 | 事实 |
| --- | --- |
| 版本 | Claude Code 2.1.285；codex-cli 0.159.2；grok 1.0.44；cursor-agent 2026.09.08；Trellis 0.6.17；Herdr 0.9.1 |
| 派工脚本 | 10,396 行 Python；`python3 tests/run_tests.py` → 327 tests OK（180s；worktree 内复跑 153s）。`herdr_dispatch.py status` 必须给 `dispatch_id`，没有全局枚举；`runtime.watcher_instance_alive(store, id)` 可判 watcher 锁是否被活进程持有；`runtime._assignee_live_status(client, state)` 用 `agent get` 得到 `gone/idle/done/working/…`；写锁登记在 `<state home>/write-scopes/holders.json`（`register_holder/update_holder/settle_holder_if_idle`，字段 `assignee_settled/phase/allow_write`），完整运行的资源占用在 `runs/<id>/resources.json` 与 `resource-scopes/holders.json`（`runstore.py:97-120`）；`accept` 成功后 watcher 退出，但执行者可能仍在 working（`runtime.py:974`、`watcher.py:1136`）；派单 phase：`registered → dispatched → running → result_ready → accepted \| needs_revision`，出口 `failed/blocked/cancelled`，异常 `needs_attention`（`references/protocol.md:42`）；`identity_changed` 时 watcher 保留旧 phase、写 `state.last_error` 并记事件 `{type: runtime_error, reason: identity_changed, round, event_id}`（`watcher.py:982-990`），claim 状态单独查 `store.is_claimed(id, event_id)`（`store.py:281`）；唤醒记录在 `rounds/<r>/wakeups/*.json`，`wakeup.primary_wakeup(store, id, round)` 取 ts 最新一条（`wakeup.py:151`），组状态可为 `skipped(controller_identity_changed)` / `retry_exhausted`（`wakeup.py:910-925`），耗尽另记事件 `type=wakeup_retry_exhausted`（`wakeup.py:1020`），不是 runtime_error；`collect --claim` 只领取 `result_ready`、`cancelled` 与 `COLLECTABLE_ERROR_TYPES = (watcher_failed, protocol_failed, runtime_error)` 的事件（`runtime.py:777-789`、`protocol.py:87`），`wakeup_retry_exhausted` 事件本身不可领取，但它的 `group_event_id` 指向的组事件（result_ready / cancelled / 错误事件）可领取（`wakeup.py:1020-1031`）；`settle_holder_if_idle` 只处理 `TERMINAL_DECISION_PHASES`（不含 `needs_attention`）、只看当前 pane 的原始 `agent_status`，working / 查询失败一律返回 False（`runtime.py:290-314`）；`lifecycle.classify_assignee` 对 `working/blocked/unknown` 直接返回，对 idle/done 先用 `evidence.waiting_on_tool`、`turn_active` 否决，再按 `settle_confirm_s`/`stall_timeout_s` 决定（`lifecycle.py:405-461`）；`evidence_blocks_settled` / `evidence_has_reliable_end`（`lifecycle.py:506-511`） |
| 身份核对 | `capture_controller` 保存 `pane_id/tab_id/workspace_id/name/agent/agent_session/terminal_id/cwd`，`agent_session` 是 `{kind, value, source}` 对象（`runtime.py:68-84`）；`classify_identity` 比较 pane、terminal、session value、**name**、agent（`runtime.py:89-117`）；真实 `pane_get` 不返回 name，`agent_get` 返回（`runtime.py:150` 注释；`tests/fake_herdr.py:323`）——唤醒投递用的是 `agent_get` + `identity_matches(controller, agent)`（`wakeup.py:917`）；执行者首个 prompt 之后才有 `agent_session` 是正常协议：`classify_identity` 返回 `fill_session`，`watcher.observe_once` 调 `fill_assignee_session`，该函数只更新 `state.assignee`（`runtime.py:207-214`、`watcher.py:962-966`） |
| Herdr 启动 | `herdr agent start <NAME> --kind <KIND> --pane <ID> [-- AGENT_ARG…]`，无环境变量选项；`herdr_client.agent_start` 只传 agent 参数。会话复用（`ensure_assignee` 的 reuse 分支）不重启进程；发送前 `classify_identity` 校验执行者身份（`watcher.py:556-575`）。`herdr pane report-metadata <PANE_ID> --source <ID> [--state-label STATUS=TEXT] [--ttl-ms N] [--clear-state-labels]` |
| 已发布交卷的判据 | `runtime.load_published_snapshot(store, task)`（`runtime.py:437-463`）核对 `published.json` 的 dispatch_id/round/request_fingerprint、`result_sha256`/`report_sha256` 与制品实际哈希、`validate_result`/`validate_report_text` 协议；任一不符抛 `published_tampered` 等 `DispatchError` |
| cancel 的落地 | `runtime.cancel` 只写 `cancel.json`、holder `phase=cancel_requested` 和 `cancel_requested` 事件（`runtime.py:1032-1052`）；完整的取消结算事务在 `watcher.handle_cancel`（`watcher.py:741-890`）：`completion_recorded` 已记 → 只重发唤醒；否则 `agent_get` 观察执行者——`agent_not_found` → 结算（gone）、`identity_matches` 不成立 → 结算（identity_changed）、idle/done 且 `evidence_blocks_settled` → 不结算（turn_active）、否则 `classify_assignee(cancel_mode=True)` 要求 `assignee_quiescent`、`unknown`/其他 HerdrError → 不结算；结算时算 `post_cancel_changes`、写 `result.cancelled.json`（无原交卷则补 result/report/published）、`ensure_group_event("cancelled")`（按组幂等）、`cancel.json.completion_recorded=True`、`set_phase(cancelled)`、`update_holder(assignee_settled=True)`、释放卡片窗口、`notify_once`；watcher 已死时 `resume` 因 cancel 标记拒绝启动（`runtime.py:1061`），`watch_loop` 开头先看 `watcher.stop`（`watcher.py:1127`），`cleanup` 留下的 stop 标记会让新 watcher 立即退出。2026-10-01 遗留清理实测：三个派单卡在 `result_ready/registered` 五天，改名 `watcher.stop` 后 `watch --detach` 才落地；无原生证据来源的 harness（Codex/Claude，`activity_scan.path = null`）在 cancel_mode 下 `classify_assignee` 永不返回 `assignee_quiescent`（`lifecycle.py:445-458` 只认 `evidence.turn_ended`），2026-10-01 实测 Codex idle 19 分钟不落地，`herdr tab close` 后 1 分钟内 cancelled |
| 模板仓 | `origin/main` = v1.5.1（`d78956f`）；`workflows/solo-github-flow/workflow.md` 1023 行；`scripts/trellis_gc.py` 265 行（只扫 `task/*`，靠 upstream `[gone]` + PR MERGED + head 相等；删除前只排除脏、锁定、主检出、当前 worktree，`trellis_gc.py:170-236`）；`scripts/setup.sh` `RELEASE_REF=v1.5.1`，`install_replaceable` 分发 gc/codegraph/diff 三个脚本，`install_conservative` 分发 PR gate、test CI 模板与 `trellis-setup/SKILL.md`（目标已存在且不同 → 只打印 `[MANUAL]` diff、`partial=true`、exit 2，不替换；`setup.sh:106-163`；exit 2 时 gc 已 `[UPDATE]`）；`v1.5.1` 字样在分发源与契约测试里共 7 个文件：`README.md`（10 处：:77 :85 :97 :100 :108 :126 :127 :202 :218 :246）、`scripts/setup.sh:4`、`workflows/solo-github-flow/workflow.md:404`、`assets/skills/trellis-setup/SKILL.md:24`、`tests/test_marketplace.py`（:143 :162 :271 :301 :373）、`tests/test_herdr_card_workflow.py:81`、`tests/test_spec_registry.py:12`（`docs/releases/` 与 `docs/plans/` 是历史记录，不改）；`scripts/smoke-install.sh` 默认期望 Trellis `0.6.12`，可用 `TRELLIS_EXPECTED_VERSION`、`TRELLIS_WORKFLOW_FILE`、`TRELLIS_SETUP_ASSET_ROOT`、`TRELLIS_MARKETPLACE_SOURCE` 覆盖；测试基线 81 tests OK（skipped=1）；README:13 仍写「after the user says 结束工作/收尾」 |
| Trellis CLI | `trellis workflow -m <source> --create-new` 在非 TTY 且未指定 `--template` 时报错 `No --template specified and stdin is not a TTY`；正确命令必须带 `--template solo-github-flow`。workflow 来源不写入 `config.yaml`（CLI 只读 `--marketplace`）；`config.yaml:164-170` 的 `#v1.5.0` 是 `registry.spec.source`（spec 模板 `solo-baseline`） |
| Trellis 归档 | `task.py archive <name>`：把 `status` 写成 `completed`、`completedAt`，目录移到 `.trellis/tasks/archive/<YYYY-MM>/<name>/`，`session_auto_commit` 为 true 时 `_auto_commit_archive` 通过 `safe_archive_paths_to_add` 暂存：整个 `.trellis/tasks/archive` 前缀、源任务目录（配合 `git rm --cached`）与被改写子任务的目录（`task_store.py:1442-1449`、`safe_commit.py:144-160`）——这是 `git add` 参数，不是最终改动：干净收尾时提交里只出现本任务源/归档路径与子任务 `task.json`；若归档前 archive 子树里有别的脏文件也会被一并提交（B3 白名单核对的是最终 diff，不是 staging 参数），然后 `after_archive` hook 的 `TASK_JSON_PATH` 指向归档后路径（`task_store.py:1317-1401`）。工作流 3.4 第 5 步先归档，3.5 才 push/PR/CI/merge：归档 ≠ 完成。归档提交落在任务分支上 |
| Trellis 收尾写入的路径 | 3.4 实际写入：spec `.trellis/spec/**`（`workflow.md:835`）；任务目录 `.trellis/tasks/<task>/**` → 归档 `.trellis/tasks/archive/<YYYY-MM>/<task>/**`；父/子关系涉及的子任务 `task.json`；journal/index 由 `add_session.py` 的 `_auto_commit_workspace` 只暂存当前开发者的 `.trellis/workspace/<dev>/journal-*.md`、`index.md` 与当前任务目录（`add_session.py:1070-1090`，#303 明确不 `git add` 整个 `.trellis/`）。3.4 不要求写 `docs/`、CHANGELOG 或仓外路径。`.trellis/scripts/**`、`.trellis/workflow.md`、`.trellis/config.yaml` 是可执行框架与行为配置，不是收尾产物 |
| Git 命令事实 | `git diff-tree --no-commit-id --name-only -r <sha>` 对两父 merge commit 默认不输出任何路径（Codex r3 真实夹具验证）；核对单个提交内容必须相对其父 `git diff-tree -r --name-status -M <sha>^ <sha>`，merge/root 提交须单独处理；linked worktree 的 `.git` 是文件，真实 `index.lock` 在 `<common-dir>/worktrees/<name>/index.lock`，必须用 `git -C <wt> rev-parse --path-format=absolute --git-path index.lock` 解析（Trellis `common/git.py:88-97` 同法；Codex r4 真实夹具证明字面 `<wt>/.git/index.lock` 检查永远为 False） |
| `task.py set-meta` | 签名 `set-meta <task-dir> <key> <value>`，值原样存字符串（`task_store.py:1840-1870`） |
| sanctions-radar | `.trellis/workflow.md` 1005 行（最后一次改动是项目内 PR #113），与模板 `origin/main` diff 349 行（hunk 表见 T1，Codex 已核对一致）；`scripts/trellis_gc.py` 与模板一致；已安装的 `trellis-setup/SKILL.md` 三份（`.agents/`、`.claude/`、`.grok/`）bootstrap URL 都钉在 `v1.2.1`（`SKILL.md:19`），不在 `.template-hashes.json` 跟踪；`.gitignore` 忽略 `.codegraph/`、`__pycache__/` 等，不忽略 `.trellis/` 下新文件；4 个未归档任务：`08-31-batch1-pipeline`（planning，branch=null，有 children，最后提交 09-04）、`09-03-batch2-site`（planning，branch=null，有 children，09-06）、`09-10-sidebar-reading-demo`（in_progress，branch=`main`，09-13）、`09-13-event-source-translation`（in_progress，branch=`feat/event-source-translation`，`pr_url=null`，该本地 ref 已不存在，09-13）；8 个 worktree / 15 个分支，其中 4 个卡片 worktree 残留（enf-0926 E1–E3 有 accepted_head 之后的额外提交；sr-empty-days-20260920-A 无 parent_acceptance）；历史 run `trk-progress-0927` 的 `project.base_branch` 是 `task/09-27-tracking-progress-display`（远端已删），而 record-unit 的 `target_ref` 是 `main` |
| 派工状态目录遗留 | 2026-10-01 已清：`enforcement-plan-review-0926`、`codex-trust-live-0926`、`tabclose-code-0926`、`loon-ai-research-20260926-164930` 全部 `cancelled` + `cleanup`；10 条 9 月中旬 `accepted` 的旧 holder 经 `settle_holder_if_idle` 结算（执行者全部 `gone`）；门禁复跑只剩本计划审核派单在飞 |
| 停顿根因 | 模板 `origin/main` `:257` `:273`（in_progress / inline 状态块）「completion report -> wait for 结束工作/收尾」由 `inject-workflow-state.py` 每回合注入；`:862-863` 3.4「present … then stop」；唯一例外是已保存的 full-run 授权（`:258` `:866-868`） |
| 失败数据 | 128 次派工、213 轮、49 次 reject（截至 2026-10-01 派本计划审核前）；最大失败类 `missing_report` 63 次（按事件 `reason` 字段统计；Worker 结束回合没交卷）；启动类失败合计 <15 次 |
| Worktree 层数 | 单卡运行 `trk-progress-0927` 也建了任务 + 集成 + 卡片三个 worktree；`sr-us-enacted-20260922` 则把任务 worktree 当集成目录，布局不一致 |
| Hook 能力 | Claude/Codex/Grok 都有 Stop hook，exit 2 阻止结束（firstmate `docs/turnend-guard.md`）；三者都有 `UserPromptSubmit`（本机 `~/.claude/settings.json`、`~/.codex/hooks.json`、`~/.grok/hooks/moshi-hooks.json` 已各注册一条）；Codex 用 `stop_hook_active`，Grok 用 `stopHookActive`；Grok 会加载 Claude 的 settings.json hooks，需 `[ -z "${GROK_AGENT:-}${GROK_HOOK_EVENT:-}" ] \|\| exit 0` 守卫；Codex 用户级 hook 需要 `~/.codex/config.toml` `[hooks.state."…"] trusted_hash` 信任条目；Claude 支持 `--settings <file>` 附加设置；**未核实**：三 harness 是否保证同一 pane 的 prompt/Stop hook 跨事件串行、Stop hook 读到的共享状态是否属于本回合——firstmate 文档只证明 Stop hook 能阻止结束；本机 `~/.claude/settings.json:164`、`~/.grok/hooks/moshi-hooks.json:62` 有 `async: true` 的其他 hook，说明 harness 支持异步 hook，不能假定串行。A1 探针专门测这一点（`serial_events`），B2 的消费规则不依赖它 |
| Trellis 任务元数据 | `.trellis/tasks/<name>/task.json`：`status`、`branch`、`base_branch`、`worktree_path`、`pr_url`、`completedAt`、`meta`、`children`、`parent`；`.trellis/config.yaml` 支持 `hooks.after_start/after_archive`，注入 `TASK_JSON_PATH` |
| firstmate 参考 | `~/.cache/firstmate-ref`（HEAD `65c75b0`）；关键文件 `bin/fm-turnend-guard.sh`、`bin/fm-turnend-guard-grok.sh`、`.claude/settings.json`、`.codex/hooks.json`、`.grok/hooks/fm-primary-turnend-guard.json`、`bin/fm-task-inbox-lib.sh`、`bin/fm-teardown.sh:1461-1602`（`content_in_default`：`merge-tree --write-tree origin/<default> HEAD` 无冲突且结果树 == `origin/<default>^{tree}`；其 patch-id 路径限定在 PR head 与共同基线区间内）、`bin/fm-busy-lib.sh`、`docs/turnend-guard.md` |

## 2. 从 firstmate 吸收什么（按本机失败数据排序）

| # | 想法 | 对应本机问题 | 落点 |
| --- | --- | --- | --- |
| 1 | 回合结束守卫（Stop hook exit 2 + 预算 + `stop_hook_active` 处理） | 主控 3.4 停顿；Worker `missing_report` 63 次 | B1 |
| 2 | 语义 busy 契约：只有精确 `busy` 才豁免停滞判定；Herdr 原生 `idle` 不可信；Codex 一律 `unknown` | `assignee_stalled` 误判、`missing_report` 发现慢 | B1 附带 |
| 3 | 启动身份显式传递（firstmate 用启动文件 + 环境变量；本机改为 pane 绑定文件） | Worker 找不到自己的轮次目录；hook 无法定位 | B2 |
| 4 | teardown 的 landed 证明（祖先 / `merge-tree` 树相等；patch-id 只做线索不做证明） | `cleanup_run` 只认 `live_head == accepted_head`，4 个残留 | B0、B6、T3 |
| 5 | 收件箱 + 常量门铃 + `mv handled/` ack + 90s×3 再响 | 中途转向靠直接敲 prompt，无投递证据 | B7（可裁剪） |

不搬：一次性 watcher + 唤醒队列（7000 行，前提是 supervisor 是会被 Stop 的 CLI 会话）；Claude `asyncRewake` 自动续臂；transactional relaunch。

## 3. 工作单元

编号：T = 模板仓，B = 派工脚本，A = 应用与本机，P = 流水线自身。每张卡独立可验收；执行顺序见 §6。所有卡：只改卡面列出的文件；测试基线不能变红；提交用 Conventional Commits。

### S0 隔离与基线（主控自己做，不派工）

已完成（2026-10-01）：

- `git -C ~/.skills-manager/skills worktree add ~/.skills-manager/skills-wt/workflow-v2 -b workflow-v2 main`（skills `main` = `381b7a1`）；B 卡的 cwd = `~/.skills-manager/skills-wt/workflow-v2/herdr-dispatch`。
- 模板仓：`main` = `origin/main` = `d78956f`，已 `git switch -c workflow-v2`；T 卡 cwd = 模板仓。本计划文件随第一张 T 卡一起提交到该分支。
- 基线：派工 327 OK（worktree 内 153s）；模板仓 81 OK（skipped=1）。
- 遗留派单清理（见 §1「派工状态目录遗留」）。清理过程暴露的两个脚本缺口（cancel 不落地、stop 标记挡住新 watcher）写进 B0 第 5 条。

待做（每次合并派工脚本前）：

- **合并门禁（无派工在飞）**：B0 落地前用临时脚本（scratchpad `merge_gate.py`，import `~/.skills-manager/skills`（`main`）的模块），B0 落地后用候选 CLI `python3 ~/.skills-manager/skills-wt/workflow-v2/herdr-dispatch/scripts/herdr_dispatch.py status --all`。两者必须实现 B0 第 2 条的**同一张真值表**，且都是**只读**（不写 holder；`holder_unsettled` 行给出下一步 `status --all --settle`——B0 落地前，执行者 gone/idle 且 phase 在 `TERMINAL_DECISION_PHASES` 的旧 holder 可用现有 `settle_holder_if_idle` 手动结算，其余等 B0）；临时脚本是一次性的，B0 合入后删除。任一在飞 → 退出码 1，不合并；输出存到 `~/.local/state/herdr-dispatch/merge-gate-<UTC>.log`。
- skills `main` 有 skills-manager 的 auto backup 提交。合并前 `git -C ~/.skills-manager/skills rev-parse main` 与 `381b7a1` 比对：若 main 已前进，先在 worktree 里 `git rebase main`，重跑 `tests/run_tests.py`，再 `git merge --ff-only`；不用 `reset --hard` 补救。

### S1 模板仓

#### T1 上游合并 sanctions-radar 的 herdr 定制

cwd 模板仓（`main` = `origin/main`）。输入：`diff -u workflows/solo-github-flow/workflow.md ~/Documents/Project/sanctions-radar/.trellis/workflow.md`（349 行）。herdr 卡片路由已在模板里，不重复。不是整份覆盖，按 hunk 取舍（hunk 号是模板侧行号；Codex 第 1 轮已核对与实际 diff 一致）：

| hunk | 处理 |
| --- | --- |
| `@@ -29` spec 层说明（pipeline/site/ops） | 不上游；项目专有，已在 sanctions-radar `AGENTS.md`，A2 再确认 |
| `@@ -152` Request Triage、`@@ -183` no_task 块 | 上游（只读工作直接进行、复用已给同意、不为只读工作跑 bootstrap/GC）；但 no_task 块保留模板的两句契约句「Do not install marketplace files into the coordinating worktree.」「Create the task worktree first, then bootstrap missing solo-github-flow tooling inside that task worktree.」（`tests/test_marketplace.py:191-192` 断言，模板级契约），末句改为合并句；剩余 diff 因此多一处 `@@ -189,7`（T1 r2 已按此落地，`3f382d6`） |
| `@@ -208` `@@ -227` planning 块删除 Test CI gate、`@@ -613` 1.5 删除 CI 行 | 不上游；保留模板的 gate |
| `@@ -250` `@@ -271` 删除 CodeGraph 句、`@@ -788` 用 awk 替换 `trellis_diff.py` 并删 CodeGraph sync | 不上游；模板为准。同一 hunk 里「Keep the project PC-first customization in AGENTS.md」是项目指针，不上游 |
| `@@ -334` Guardrails、`@@ -627` Verification Evidence 节 | 上游 |
| `@@ -648` 「Do not migrate business features…」 | 不上游 |
| `@@ -674` `@@ -689` `@@ -703` `@@ -719` `@@ -768` `@@ -776` `@@ -788`（Fix task-introduced… / Final pass 措辞） | 上游 |
| `@@ -816` OCR 第 7 条 | 上游 |
| `@@ -353` `@@ -383` Phase 1.0、`@@ -875` 3.5 第 2 条删除 | 不上游；模板为准 |
| 3.4（模板 `:862-868`）与 in_progress 块的停顿句 | 由 T2 重写 |

验收：`tests/test_marketplace.py`、`tests/test_herdr_card_workflow.py` 等现有测试通过；新 workflow.md 里每个 `[workflow-state:X]` 块成对闭合；`grep -c "Verification Evidence"` ≥ 3；用 sanctions-radar `.claude/hooks/inject-workflow-state.py` 的 `load_breadcrumbs`（或等价解析函数，临时脚本调用，`PYTHONDONTWRITEBYTECODE=1`，不改该 hook）对新文件解析出 `no_task/planning/planning-inline/in_progress/in_progress-inline/completed` 六个块（文档示例 `my-status` 不算运行状态）。

#### T2 「开始即到底」文案

cwd 模板仓，基于 T1 结果。改动点（以 T1 后的行号为准，`origin/main` 上是 `:257` `:273` `:862-868`）。`tests/test_herdr_card_workflow.py` 里断言旧「wait for 收尾」措辞的用例同步改成断言新措辞：

1. `[workflow-state:in_progress]` 与 `[workflow-state:in_progress-inline]`：`completion report -> wait for “结束工作” / “收尾” -> automated GitHub finish` 改为 `completion report (do not stop) -> Phase 3.4-3.5 run through`，并加一句：`Stop only for: product decision, missing credentials/permissions, CI failure outside this task's scope, dirty files of unknown ownership; before stopping run turn_guard.py stop --reason "<why>"`。
2. 3.4：删除「then stop / 结束工作 or 收尾 is one-shot authorization」，改为：`task.py start`（用户的「开始」）即 3.4–3.5 授权；报告照旧呈现，紧接着执行 3.5。列出四种停下情形与记录命令 `python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py stop --reason "<text>"`。
3. 「开始，合并前停」：用户这么说时，主控在 `task.py start` 之后运行 `turn_guard.py pause-before-merge`（写在该任务 marker 上，归档后仍在）。3.5 在 squash 合并前先 `turn_guard.py status`，看到 `pause_before_merge=true` 则给出 PR 链接、运行 `turn_guard.py stop --reason "user asked to pause before merge"` 后停下；用户说「继续」后运行 `turn_guard.py resume`（清掉该标记与预算）再合并。会话中断后由 `trellis-wrap-up` 恢复：`turn_guard.py status --repo` 列出本仓所有 marker（含归档后 task.json 路径、分支、PR），`turn_guard.py adopt <task>` 把 marker 绑到当前 pane 后续做。
4. 3.5 末尾：合并并删除远端分支后运行 `turn_guard.py mark-merged`（用与守卫自检相同的完整解除条件验证，不成立则拒绝）与 `python3 scripts/trellis_gc.py --apply`（已有），并写明 GC 只清 `task/*`（要求任务已归档或 PR 已合并），`card/*`、`run/*` 由派工脚本 `run cleanup` 清（B6），以及兜底归档（T3）。
5. Phase Index / Rules / `trellis-wrap-up` 提及处：`trellis-wrap-up` 改为「恢复入口：会话中断或主控被守卫拦下后手动续做 3.4–3.5」，不再是默认流程的一环。
6. 「Customizing」一节加一段：`.trellis/config.yaml` 需要 `hooks.after_start` / `hooks.after_archive` 调用 `turn_guard.py mark-start` / `mark-archive`（A2 安装）；没有该 hook 时守卫静默不拦。
7. 文件首行加 `<!-- trellis-personal-marketplace solo-github-flow v1.6.0 -->`（安装后可 `head -1` 得知版本；T4 的测试断言它与 `setup.sh` `RELEASE_REF` 一致）。

验收：`grep -n "结束工作\|收尾" workflow.md` 只剩恢复入口与「合并前停」说明；`tests/` 通过；六个状态块仍成对；T1 的解析验证再跑一次（含首行注释）。

#### T3 `trellis_gc.py` 扩展

cwd 模板仓，文件 `scripts/trellis_gc.py`、`tests/test_gc.py`（真实临时 git 仓 + 本地 bare `origin`，不 mock git）。保持默认 dry-run、`--apply` 才动手、每条删除打印证据。

1. `--prefix` 改为可重复，默认只有 `task/`。`card/*`、`run/*` 分支与 `<repo>-wt/<run_id>/` 下的 worktree 只列出 `[INFO managed_by=herdr-dispatch run cleanup]`，不删（§0 #11）；`remove_empty_worktree_parent` 仍删真正为空的 `<repo>-wt/<run_id>/` 两层目录。
2. `task/*` 的**生命周期证据**（先于内容证明，任一不成立即 `[SKIP]`）：
   - 用分支名在 `.trellis/tasks/*/task.json` 与 `.trellis/tasks/archive/*/*/task.json` 里找 `branch == <branch>` 的任务：
     - 在 `archive/` 下（`status == completed`）→ 生命周期已结束，进入第 3 条内容证明；
     - 在 `tasks/` 下（planning/in_progress 等）→ `[SKIP task_active]`；
     - 找不到 → 只接受旧规则 (a)（upstream `[gone]` 且 `gh pr view` MERGED 且 head 相等），否则 `[SKIP task_unknown]`。
   - `$(git rev-parse --git-common-dir)/trellis-guard/*.json` 里有 `branch == <branch>` 的守卫 marker（无论 phase）→ `[SKIP guard_active]`（B1 保证 marker 只在合并落地后删除）。
   - worktree 目录由别的进程持有：`git worktree list --porcelain` 标 `locked` → `[SKIP locked]`；`git -C <wt> rev-parse --path-format=absolute --git-path index.lock` 解析出的文件存在 → `[SKIP git_locked]`；解析失败 → `[SKIP lock_unknown]`。不把 linked worktree 的 `.git` 当目录。`--apply` 下在 `git worktree remove` 之前再检查一次。
3. 「已落地」内容证明（对通过第 2 条的分支，按顺序，任一成立即通过；patch-id 不作为证明）：
   a. 旧规则：upstream `[gone]` 且 `gh pr view` MERGED 且 head 相等；
   b. `git merge-base --is-ancestor <branch> origin/<default>`；
   c. `git merge-tree --write-tree origin/<default> <branch>` 无冲突且结果树 == `origin/<default>^{tree}`（squash 合并后的判定，取自 firstmate `fm-teardown.sh:1575-1591`）；
   d. `--force-gone` 沿用，前置条件不变。
   不确定（无 `origin/<default>`、merge-tree 冲突、命令失败）一律 `[SKIP]` 并给原因。分支 == 默认分支 → `[SKIP branch_is_default]`。worktree 脏（含 untracked）一律跳过，`--force-dirty` 沿用。锁定的 worktree、主检出、当前所在 worktree 一律 `[SKIP]`。
4. 兜底归档 `--archive-idle-days N`（默认 7，`0` 关闭）。
   - **候选条件**（全部满足）：
     - 完成证据（任一，§0 #12）：(i) `status == completed`（归档中断留下的）；(ii) `status == in_progress` 且 `pr_url` 非空、`gh pr view <pr_url> --json state,headRefName,headRefOid` 为 MERGED、`headRefName == task.branch`、且 `headRefOid` 按第 3 条已落地。其余 in_progress → SKIP `no_pr`（无 PR）/ `pr_not_merged` / `pr_branch_mismatch` / `not_landed`；分支 tip 与 merge-base 的关系不再作为证据。
     - `planning` 一律 SKIP `planning`；`children` 里有任何非 archived/completed 的子任务 → SKIP `children_active`。
     - 任务目录最近一次提交距今 ≥ N 天（`git log -1 --format=%ct -- .trellis/tasks/<name>`）；没有 `git worktree list` 里的 worktree 在该分支上；`git status --porcelain -- .trellis/tasks/<name>` 为空；没有该任务的守卫 marker。
     - 每个 SKIP 打印原因（`planning`、`no_pr`、`pr_not_merged`、`pr_branch_mismatch`、`not_landed`、`children_active`、`dirty_task_dir`、`worktree_active`、`guard_active`、`recent`）。
   - **pending 记录**：`$(git rev-parse --git-common-dir)/trellis-gc/pending-push.json`（不在工作树里，不污染 `git status`）：`{default_branch, commits: [sha…], recorded_at}`。
   - **单个提交的合法性检查**（归档新增提交与 pending 重试共用一个函数）：`git rev-list --parents -n1 <sha>` 恰好一个父（merge、root、其他 → 不合法 `unexpected_commit_type`）；`git diff-tree -r --name-status -M <sha>^ <sha>` 的每个条目（R 的旧新两侧都算）路径都在 `.trellis/tasks/` 下；输出为空视为不合法（不是「没改动」）。
   - **同步前置**（`--apply` 时，在处理候选之前先检查；有 pending 记录时零候选也执行）：当前目录是主检出（`git rev-parse --git-dir` == `--git-common-dir`）；当前分支 == 默认分支；`git status --porcelain --untracked-files=all` 为空；`git fetch origin <default>` 成功；`git merge-base --is-ancestor origin/<default> HEAD` 成立（本地不落后、不分叉）；`git rev-list origin/<default>..HEAD` 的每个 SHA 都在 pending 记录里且通过单提交合法性检查。任一不满足 → 打印具体原因（`behind_remote`、`diverged`、`unknown_local_commits`、`unexpected_commit_type`、`not_main_checkout`、`not_default_branch`、`dirty`）后**不归档、不推送**，退出码 1，并打印恢复提示（列出可疑 SHA，让用户 `git log -p <sha>` 检查；脚本不自动 reset）。`session_auto_commit` 为 false → SKIP `auto_commit_disabled`。
   - **执行**：先推送 pending（有则 `git push origin <default>`，成功清记录，失败保留并退出码 1）；再逐个：记 `before=HEAD` → `python3 .trellis/scripts/task.py archive <name> --skip-branch-validation` → 返回码非零 → `[WARN]` 停止；`git rev-list before..HEAD` 必须恰好 1 个提交且通过合法性检查（`after_archive` hook 等产生的额外提交 → 停止 `unexpected_archive_commits`，保留现场，不推送）→ 追加到 pending。全部完成后 `git push origin <default>`，成功清 pending，失败 `[WARN] pending push` 保留记录。不在 `--apply` 下只打印 `[PLAN] would archive <name> (idle Nd, evidence=<completed|pr_merged>, proof=<ancestor|tree_equal|pr>)`。
5. 输出结尾一行汇总：`removed=… archived=… skipped=… managed_by_dispatch=… pending_push=…`。

验收：`tests/test_gc.py` 覆盖：
- 内容证明：task/ 分支（任务已归档）squash 合并后被识别；rebase 后合并、`--autosquash` 合并的正例；**反例必测**：目标分支加补丁后 revert、源分支独立加同一补丁 → keep；未落地 SKIP；merge-tree 冲突 SKIP；脏 worktree SKIP；分支 == 默认分支 SKIP；锁定 worktree SKIP；**真实 linked worktree 夹具：任务已归档、无 marker、已落地、干净，但 `<common-dir>/worktrees/<name>/index.lock` 存在 → dry-run/apply 均 SKIP `git_locked`，目录/分支保留；删掉锁文件后同一候选被清理**；空 `<repo>-wt/<run>/` 目录被删。
- 生命周期证据：**从 main 新建、无 upstream、工作树干净、HEAD==基线的 `task/*` 分支，其任务仍 in_progress → 在另一主检出跑 dry-run/apply 均 SKIP `task_active`，目录/分支都在**；同样情形但只有守卫 marker（任务 json 已归档）→ SKIP `guard_active`；任务 json 与 marker 都没有 → SKIP `task_unknown`（除非旧规则 (a) 成立）；`card/*`、`run/*` 分支和 worktree 一律不删，只 INFO；任务归档 + marker 已删 + 内容落地 → 删除。
- 归档候选：planning SKIP；branch=null SKIP；branch=main SKIP；**in_progress + 单个空提交 + 无 PR + 无 worktree + 旧目录 → SKIP `no_pr`**；in_progress + PR MERGED 但 `headRefName` ≠ 任务分支 → SKIP；PR MERGED + head 落地（普通 merge、fast-forward、squash 三种）→ PLAN/archive；`completed` 路径；children 活动 SKIP；任务目录脏 SKIP；有 marker SKIP；`--archive-idle-days 0` 关闭。
- 同步前置与 pending：非默认分支、默认分支 ahead 含非归档提交、本地落后、分叉、`session_auto_commit: false`、archive 返回非零停止；**两父 merge 提交（解决冲突时改了 `business.py`）即使 SHA 已登记且其普通父提交只改 tasks → 不推送**；归档后出现第二个提交 → 停止且不推送；push 失败后记录在 git common dir 且 `git status` 仍干净、下一次零候选仍重试推送并清记录；pending 期间出现新的未知本地提交 → 不推送；远端只能新增通过合法性检查的归档提交；`--apply` 前后 `git worktree list` 与 `git branch` 差异符合预期。
- 对 sanctions-radar 只跑 dry-run（含 `--archive-idle-days 7`），输出附在报告里。

#### T4 README、分发指针、版本

cwd 模板仓。

1. README「Worktree model」改写为统一 `<repo>-wt/` 布局与单卡单 worktree 规则；README:13 的「after the user says 结束工作/收尾」改为「after the user says 开始, the agent runs through 3.4–3.5」；新增「Turn guard」小节（用户级注册命令 `python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py install`、`doctor`、config.yaml hook 片段）；GC 小节写明生命周期证据 + 内容证明、`card/run` 归派工脚本、兜底归档（completed / PR MERGED）、同步前置与 pending 记录。
2. `v1.5.1` → `v1.6.0`，文件清单（不是计数）：`README.md`、`scripts/setup.sh`、`workflows/solo-github-flow/workflow.md`、`assets/skills/trellis-setup/SKILL.md`、`tests/test_marketplace.py`、`tests/test_herdr_card_workflow.py`、`tests/test_spec_registry.py`。验收命令：`rg -n "v1\.5\.1" --glob '!docs/**' --glob '!.git/**' .` 为空。不删除 `test_release_references_stay_aligned` 等一致性断言。
3. 新增测试：workflow.md 首行注释里的版本 == `setup.sh` 的 `RELEASE_REF` == `assets/skills/trellis-setup/SKILL.md` 的 bootstrap URL 版本。
4. 新增 `docs/releases/v1.6.0.md`（格式照 `docs/releases/v1.5.1.md`）。发布动作（打 tag、push）由主控在 A2 前做，不在卡内。

验收：模板仓全部测试通过；`TRELLIS_EXPECTED_VERSION=0.6.17 TRELLIS_WORKFLOW_FILE=$PWD/workflows/solo-github-flow/workflow.md TRELLIS_SETUP_ASSET_ROOT=$PWD bash scripts/smoke-install.sh` 通过，报告贴出它安装到临时目录的 `trellis_gc.py` 与候选一致（`cmp`）。

### S2 派工脚本

cwd 一律 `~/.skills-manager/skills-wt/workflow-v2/herdr-dispatch`。每张卡结束时 `python3 tests/run_tests.py` 全绿并附耗时。

#### B0 落地证明与全局状态（B1/B2/B6 的前置）

文件：`scripts/gitutil.py`、`scripts/runtime.py`、`scripts/watcher.py`（第 5 条：`handle_cancel` 改为调用共享事务；`watch_loop` 的 stop 顺序）、`scripts/herdr_dispatch.py`、`tests/test_gitutil_landed.py`、`tests/test_status_all.py`、`tests/test_settle_dispatch.py`、`tests/test_cancel_finalize.py`；`scripts/lifecycle.py` 只改第 5 条的 `classify_assignee` cancel_mode 分支（`idle_no_evidence`），其余不动。`dispatch_settled` 只 import `lifecycle.collect_turn_evidence` / `evidence_blocks_settled`。

1. `gitutil.is_landed(repo, sha, target_ref) -> tuple[bool, str]`：`git fetch` 由调用方负责；证明只有两种：`merge-base --is-ancestor sha target_ref` → `(True, "ancestor")`；`merge-tree --write-tree target_ref sha` 无冲突且结果树 == `target_ref^{tree}` → `(True, "tree_equal")`；其余 `(False, reason)`（`conflict`、`target_missing`、`sha_missing`、`tree_differs`、`git_error`）。不用 patch-id。它只证明「内容已在目标里」，不证明任务完成或验收有效。
2. `runtime.dispatch_settled(store, dispatch_id, client) -> dict`（**纯观察，不写任何文件**），返回 `{in_flight, reason, phase, watcher_alive, executor, evidence, holder, attributed_to, next_step}`。**真值表**（S0 临时脚本、`status --all`、`settle_dispatch` 的资格判定三处逐行相同）：
   - 读取：`phase`；`watcher_instance_alive`；执行者观察 = `client.agent_get(assignee.pane_id)`：`agent_not_found`/pane 不存在 → `gone`；其他 `HerdrError` → `query_failed`；返回后 `classify_identity(state.assignee, agent)`：`mismatch/missing` → 对本派单而言 `replaced`；`match/fill_session` → 取 `agent_status`（`working/blocked/unknown/idle/done/None`）；对 `idle/done/None` 再取原生回合证据 `evidence = collect_turn_evidence(task, agent, store, state, config, state.activity_scan)`（取证抛异常 → `evidence_failed`）；holder = `write-scopes/holders.json[dispatch_id]`（`settled_true / settled_false / missing`）；run 占用 = `runs/*/resources.json` + `resource-scopes/holders.json`。
   - 归属：执行者观察为 `working/blocked/unknown`、或 idle 但 `evidence_blocks_settled(evidence)` 时，若存在另一派单 B：`B.phase` 非终态、`B.assignee` 的 `pane_id`、`terminal_id`、`agent_session.value` 与本派单相同、且 B 的 `submitted` 事件更晚 → 活动归 B（`attributed_to=B`），本派单的执行者观察改记为 `attributed`；否则保持原观察。
   - 判定顺序（首个命中即返回）：
     1. state 损坏 / phase 未知 → 在飞 `corrupt`。
     2. `phase ∈ {registered, dispatched, running, result_ready}` → 在飞 `phase_active`。
     3. watcher 活 → 在飞 `watcher_alive`。
     4. 执行者 `query_failed` / `evidence_failed` → 在飞 `unknown`（**与 holder 无关**）。`evidence_failed` 包括 `collect_turn_evidence` 不抛异常但返回 limitation 的情形：该 harness 本应有原生来源（当前只有 Grok transcript）而文件缺失、不可读、身份缺失、或截断后无法判断回合状态（`grok_events_truncated` 且无活动布尔值）——取不到证据不等于没有活动。对本就没有原生来源的 harness（Codex、Claude；`evidence.source == none` 且 limitation 表示不支持）不算失败：进入第 5–7 步，只凭 `agent_status` 判定。S0 脚本、`status --all`、`settle_dispatch`、`finalize_cancel` 用同一规则。
     5. 执行者 `working/blocked/unknown`（未归属他人）→ 在飞 `executor_active`（**holder 是 settled_true 或 missing 都不能盖过**）。
     6. 执行者 `idle/done/None` 且 `evidence_blocks_settled(evidence)`（原生 `turn_active` / `waiting_on_tool`，未归属他人）→ 在飞 `executor_turn_active`。
     7. 到这里执行者对本派单已结束（`gone` / `replaced` / `attributed` / 无原生活动的 `idle/done/None`），phase ∈ 终态 `{accepted, needs_revision, failed, blocked, cancelled, needs_attention}`：holder `settled_true` → settled；holder `missing` → settled 并标 `holder_missing`（终态且执行者已结束，缺 holder 不算证据缺失）；holder `settled_false` → 在飞 `holder_unsettled`，`next_step = "status --all --settle"`（只读路径不结算）。
     8. 枚举层再查：holder 里有 state 目录不存在的孤儿记录 → 在飞 `orphan_holder`；run 占用未释放 → 在飞 `run_resources_held`。
   - `runtime.settle_dispatch(store, dispatch_id, client) -> dict`（**唯一写路径**）：锁顺序 `holders_lock` → `store.lock(dispatch_id)`（与 `_conflicting_holder` 现有嵌套一致），在锁内重新做上述观察；只有第 7 步命中且 holder 是 `settled_false` 时 `update_holder(assignee_settled=True, phase=<phase>)` 并返回 `{settled: True, reason}`；其余返回 `{settled: False, reason}` 且不写。因此 `replaced`、`attributed`、`needs_attention + gone` 都能收敛，而新 occupant 的 working 不再阻止旧 holder 收敛。`settle_holder_if_idle` 改为调用它（保留签名；`locked=True` 表示调用方已持 `holders_lock`，不再重复取）。调用点要显式改，不靠包装自动生效：`overlapping_holder`（`runtime.py:255-287`）对重叠 holder，phase ∈ 终态 ∪ {needs_attention} 且 `assignee_settled` 为 false 时调用 `settle_dispatch`（`locked=True`，锁顺序不变；现在 `:284` 只对终态调用，needs_attention + gone 的 holder 永远挡住同路径新派单）；`assignee_settled == true` 的缓存捷径（`:271`）保留（延后项见 §9）；`cleanup`（`runtime.py:1605`）同样改调 `settle_dispatch`。现有 `settle_holder_if_idle` 测试仍须通过。
3. `runtime.dispatch_wait_status(store, dispatch_id, pane_id, client) -> tuple[bool, str]`：主控「等待豁免」判定，**按顺序，首个命中即返回**：
   1. `s = dispatch_settled(...)`（只读）；`not s.in_flight` → `(False, "settled")`。**历史已结算派单到此为止，不再核对控制端身份**（控制端 session 换了也只是 settled）。
   2. `phase ∈ {accepted, needs_revision, failed, blocked, cancelled}` 但 `s.in_flight`（reason ∈ `executor_active / executor_turn_active / unknown / holder_unsettled / watcher_alive`）→ `(False, "status <id>：<reason>，执行者未结算，等待或 cancel")`——终态但仍在飞是 action_required，不能只看 phase。
   3. `phase == needs_attention` → 本轮 `watcher_failed` 事件未 claim → `(False, "collect --claim <id>")`；已 claim → `(False, "核对启动故障后 resume --retry-start <id>")`。plain `resume` 只重启 watcher，`attention_blocks_send` 仍禁止发送，不是恢复动作；运行期 `runtime_error` 不授予 retry-start。
   4. `phase == result_ready` → `(False, "collect --claim <id>")`。
   5. `state.controller.pane_id == pane_id`，且控制端身份与唤醒投递完全相同的调用——`agent = client.agent_get(pane_id)`，`identity_matches(state.controller, agent)`（`wakeup.py:917` 同款；`agent_not_found` 或不匹配 → `(False, "status <id> 后人工处置：控制端身份已变")`）。
   6. `watcher_instance_alive` 不成立 → `(False, "resume <id>")`。
   7. 本轮（`current_round`）`events.jsonl` 里存在**未领取的可领取事件**——`type ∈ COLLECTABLE_ERROR_TYPES` 且未 `store.is_claimed(id, event_id)`，或 `type == wakeup_retry_exhausted` 且其 `group_event_id` 未 claim（组事件是 `result_ready` / `cancelled` / `COLLECTABLE_ERROR_TYPES`，都可由 `collect --claim` 领取，领取即解除门禁；通知事件自己的 `event_id` 不参与判定）→ `(False, "collect --claim <id>")`。`state.last_error` 非空但本轮事件都已 claim（`collect` 只写 claim、不清 `last_error`，`runtime.py:859`）→ 不拦，reason 里附 `last_error` 摘要作诊断；无法映射到任何事件的 `last_error` 同样只诊断不拦。旧轮的事件不算。
   8. `wakeup.primary_wakeup(store, id, current_round)`：`status == skipped` → `(False, "status <id> 后人工处置：唤醒被跳过（controller_identity_changed）")`；`status == retry_exhausted` 不单独判定（第 7 步已按组事件 claim 状态处理，该记录只作显示）。
   9. 全部通过 → `(True, "waiting")`。
4. `herdr_dispatch.py status --all [--settle]`：无 `dispatch_id` 时枚举 state home 所有派单（跳过非派单目录），每行 `dispatch_id phase round watcher_alive executor holder attributed_to in_flight reason next_step`，再列 run 占用与孤儿 holder；末尾 `in_flight=<n>`；退出码：有在飞 → 1，否则 0。默认只读（**运行前后 state home 文件哈希不变**）；`--settle` 对每个 `holder_unsettled` 行调用 `settle_dispatch`，随后重新只读枚举，退出码以重新枚举为准。`watcher_instance_alive` 要有只读变体：锁文件不存在 → 直接 dead，不得 O_CREAT（`runtime.py:466` 现有实现会创建 `watcher.lock`）；零写入夹具比较运行前后 state home 的文件集合与哈希（含无 `watcher.lock` 的 registered 派单、缺 state 目录的孤儿 holder）。
5. **cancel 落地不依赖活 watcher**：把 `watcher.handle_cancel` 的完整事务（`watcher.py:741-890`：资格观察、`post_cancel_changes`、`result.cancelled.json` 与必要的 result/report/published 补齐、`ensure_group_event("cancelled")`、`completion_recorded`、`set_phase`、`update_holder`、窗口释放、`notify_once`、唤醒）抽成 `runtime.finalize_cancel(store, client, dispatch_id) -> {finalized, diagnostic, event_id}`，输出与现在逐字段等价。**资格**：同一执行者 `working/blocked/unknown`、`query_failed`、`evidence_failed`（第 2 条定义）、idle 但 `evidence_blocks_settled` → 不结算，返回 `{finalized: False, diagnostic}`，保留 holder，不写 phase；`gone` / `identity_changed` → 结算；idle/done/None 且原生证据不否决 → 现有 `classify_assignee(cancel_mode=True)` 只在 `evidence.turn_ended` 时给 `assignee_quiescent`（`lifecycle.py:445-458`），没有原生来源的 harness（Codex、Claude）永远 `idle_candidate → stalled`，cancel 只能靠执行者 gone 落地（2026-10-01 实测：Codex idle 19 分钟不落地，关 tab 后 1 分钟内 cancelled）。改为：cancel_mode 下 `evidence.source == none` 且 limitation 为「不支持」时，`agent_status` 持续 idle ≥ `settle_confirm_s`（沿用 `settle_candidate.first_idle_ts`）即 `assignee_quiescent`，diagnostic `idle_no_evidence`；有原生来源的 harness 规则不变。**幂等**：在 `store.lock(dispatch_id)` 下读 `cancel.json`：已 `completion_recorded` → 只重发唤醒，返回 `already_finalized`；否则写占位 `finalizing = {pid, nonce, ts}` 后释放锁，按这个顺序写：制品（按存在性跳过）→ `ensure_group_event("cancelled")`（按组键去重）→ `set_phase(cancelled)` → `update_holder(settled)` → 窗口释放（tab 不存在视为已释放）→ 最后在 `store.lock` 下写 `completion_recorded=True` → `notify_once`。每步幂等：任一步之后崩溃，再次调用从头重跑都只补齐、不产生第二个事件或第二次释放；`completion_recorded` 的含义是「phase/holder/制品/窗口已一致」，不能像现有 `handle_cancel` 那样写在 `set_phase`/`update_holder` 之前。占位存在、未 `completion_recorded`、且占位 `pid` 仍存活（`os.kill(pid, 0)`）→ 其他调用者返回 `finalizing`；占位 `pid` 已死 → 接手重跑。不用固定 TTL，不做 lease/fencing（单机单用户，延后见 §9）。锁内不调用会再取同锁的 `ensure_group_event` / `persist_lifecycle`。调用者：
   - `watcher.handle_cancel` 变成薄包装（每次轮询调用，行为不变）；
   - `cancel`：写 `cancel.json` 后，watcher 不活 → 调一次 `finalize_cancel`；`finalized` → 返回 `phase=cancelled`；否则返回 `phase=cancelling, diagnostic, next_step="resume <id>"`（恢复监督，不强行 finalize）；watcher 活 → 行为不变（watcher 落地）；
   - `resume`：cancel 已请求且未落地 → 先 `store.clear_stop`，再调一次 `finalize_cancel`；已落地 → 返回 `cancelled`；未落地 → 启动 watcher 继续观察（不再拒绝）。`--retry-start` 对 cancelled/cancel 已请求仍拒绝；
   - `watch_loop`：`cancel.json` 存在且未 `completion_recorded` 时，优先于**所有**提前退出（`watcher.stop`、phase 已 `accepted` 等，`watcher.py:1127-1136`）先跑现有轮询到 cancel 落地，再退出；
   - `cleanup`：cancel 未落地 → 不写 `watcher.stop`、不关窗口，返回 `cancel_pending` + `next_step="resume <id>"`。

验收：`is_landed` 测试覆盖 squash、rebase 后合并、autosquash、真实祖先、合并后 main 又有新提交（仍 landed）、冲突 → False、**add→revert 反例 → False**、只含空提交的分支（tree 相等 → True，测试写明这是预期）、部分 cherry-pick → False；`dispatch_settled`/`status --all` 夹具（假 Herdr + 假 transcript/`activity_scan` 供 `collect_turn_evidence` 用）逐行覆盖真值表：running；accepted + watcher 活；**accepted + watcher 死 + 同一执行者 working，holder 分别为 false / true / missing → 三种都 exit 1**；holder true + 查询失败 → exit 1；**accepted + 执行者 idle + 原生 `turn_active` 或 `waiting_on_tool`，holder true/missing → exit 1（`executor_turn_active`）**；accepted + 执行者 working 但同一会话已被更晚的非终态派单 B 复用 → 本派单 `attributed`、B 在飞；执行者被新会话接管（terminal/session 变）→ `replaced`；**holder false + replaced working、holder false + attributed、needs_attention + gone、cancelled + holder false + gone → 只读 exit 1 `holder_unsettled`，`--settle` 后再只读 → exit 0**；holder 孤儿记录、state 损坏、run 占用 → exit 1；全部可靠 settled → exit 0；**默认 `status --all` 运行前后 state home 所有文件哈希不变**；现有 `settle_holder_if_idle` 测试仍过；`dispatch_wait_status` 夹具：正常等待（含有 `name` 的 controller，`agent_get` 返回同 name）→ True；**已 settled 的历史派单 + 控制端 session 已变 → `(False, "settled")`**；accepted + 执行者 working（终态在飞）→ False 且 reason 含 `status`；`agent_get` 返回不同 terminal/session → False（人工处置）；`result_ready`、watcher 死、`needs_attention`、本轮未 claim 的 `runtime_error(identity_changed)` → False 且 reason 含对应下一步；**本轮 `wakeup_retry_exhausted` 且其 `group_event_id` 未 claim → False（`collect --claim`）；同一事件但组事件已被 `collect --claim` 领取且监督健康 → True**；`primary_wakeup.status == skipped`、别的 pane 控制 → False；旧轮已 claim/未 claim 的错误不影响新轮；无唤醒记录的初始监督 → True；`finalize_cancel`：watcher 死 + 执行者 gone + `cancel` → 立即 cancelled、holder settled，`result.cancelled.json` 与 `cancelled` 事件与现有 watcher 路径逐字段相同，`collect --claim` 可领取；**活 watcher 或同一执行者 working 时 `cancel`/`resume` → `cancelling`，holder 不释放、phase 不变；idle 但原生 `turn_active` → 同样不结算**；`cancel.json` 存在 + `watcher.stop` 存在 → `watch --detach` 先落地 cancel 再退出；`resume` 对未落地 cancel → 落地并返回 cancelled，不能落地则启动 watcher；**两个入口并发（线程夹具）→ 只有一个 `cancelled` 事件、一次窗口释放**；`cleanup` 对未落地 cancel → `cancel_pending`。r6 追加：占位 `pid` 存活 → 其他入口 `finalizing`，`pid` 已死 → 接手；在每个写入边界（事件后、phase 后、holder 后、窗口后）模拟崩溃再重调 → 补齐且只一个事件、一次释放；accepted + 同一执行者 working + cancel → `resume` 起的 watcher 不早退，执行者 idle 后落地；假 Codex agent（无 transcript）idle ≥ `settle_confirm_s` + cancel → cancelled（`idle_no_evidence`）；Grok transcript 缺失/不可读/截断且无活动布尔值 + accepted + watcher 死 + holder true/missing → `status --all` exit 1 `unknown`（不是 settled），cancel 不落地；`dispatch_wait_status`：needs_attention 未 claim → reason 含 `collect --claim`，已 claim → 含 `resume --retry-start`；`last_error` 非空但本轮事件都已 claim 且监督健康 → True，再次 `collect` 无事件不形成循环。

#### B2 轮次绑定、活动协议、状态标签、卡面头部（B1 的前置：冻结共享 schema 与生产端写入）

文件：`scripts/watcher.py`（`ensure_assignee`、`align_round_request`、`send_prompt`/`_start_agent`、`observe_once` 的 fill_session、停滞判定）、`scripts/runtime.py`（`fill_assignee_session` 扩展、`accept`/`cancel` settled/`cleanup` 的绑定清理）、`scripts/lifecycle.py`、`scripts/herdr_client.py`、`scripts/templates.py`（`render_request` 头部）、新 `scripts/paneguard.py`（pane 文件与轮次活动文件的读写、锁、CAS——B1 的 hook 只 import 它，不自己解析）。

1. **pane 绑定**（`~/.local/state/herdr-dispatch/turn-guard/panes/<pane_id>.json` 的 `worker` 字段，同目录 `<pane_id>.lock`）：`{dispatch_id, round, round_dir, fingerprint, assignee: {harness, agent, name, agent_session, terminal_id, pane_id}, generation, updated_at}`（`agent` = Herdr kind，`name` = `agent start` 的 NAME，都来自 `state.assignee`）。
   - 写入时机：每次向执行者发送本轮 request **之前**（首次启动后、复用会话 `align_round_request` 后、`resume --retry-start` 后），在 pane 锁下：先核对派单当前 round == 本轮且 `state.assignee` 身份与即将发送的目标一致（`classify_identity ∈ {match, fill_session}`），再写；`generation` 单调递增（从旧记录 +1）。`agent_session` 此时可能为 null（首个 prompt 后才有），这是合法的「待补全」绑定。
   - **session 补全的唯一入口** `paneguard.fill_session(store, dispatch_id, round, generation, session_value, observed) -> {filled | noop | fill_conflict | rejected: <why>}`（watcher 与 B1 hook 共用；B1 不得自己写 session）：`observed = client.agent_get(pane_id)`（不是 `pane_get`，后者无 `name`）；资格 = `observed` 的 `pane_id / terminal_id / agent / name` 与绑定 `assignee` **四项全等**（不经 `classify_identity`——它在期望 session 为 null 时先于 name/agent 比较就返回 `fill_session`），且 `observed.agent_session.value == session_value`（hook 传载荷 session、watcher 传观察值，两者都必须与 Herdr 当前报告一致）；任一不等 → `rejected`，不写。锁顺序固定：`store.lock(dispatch_id)` → pane 锁 → `<round_dir>/.activity.lock`；锁内核对 `(dispatch_id, round, generation)` 未变，然后**三处一起写**：`state.assignee.agent_session`、pane `worker.assignee.agent_session`、`activity.json.assignee_session`——每处只写 null；任一处已非 null 且 ≠ `session_value` → `fill_conflict`：三处都不写、记日志，调用方按「绑定无效 / identity_changed」处理；三处都已等于 `session_value` → `noop`。`runtime.fill_assignee_session` 改为调用它（`watcher.observe_once` 的 fill 分支不变）。
   - 清理时机：`accept`、取消 settled、`cleanup` 时，在 pane 锁下只有当现有 `worker` 的 `(dispatch_id, round, generation)` 与本派单登记的完全相同才清；否则不动。旧 watcher 迟到写入：写前核对 round 仍是当前 round，否则放弃。无头路径（B5）不写 pane 文件，用子进程环境 `HERDR_DISPATCH_ROUND`。
2. **活动协议**（Worker hook 与 watcher 共享；文件都在 `<round_dir>/`，锁 `.activity.lock`）：
   - `activity.json`（watcher 每次发送 prompt 前在锁下写）：`{generation, prompt_sent_at, assignee_session}`，generation 与 pane 绑定同值。
   - `turn.json`（**prompt 事件的 hook** 在回合开始时写）：`{generation: <此刻 activity.json 的 generation>, session_id: <载荷>, turn_seq: <上一值 + 1>, started_at}`。这是「回合来源 token」：它在活动开始时捕获，不在结束时读取。
   - `busy-state.json`：prompt hook 写 `{state: busy, token: <turn.json 全部字段>, ts}`；Stop hook 写 `{state: idle, token: <hook 进程开始处理时读取一次的 turn.json，不在写入前重读>, ts}`。写入在锁下做 CAS：token.turn_seq 必须 ≥ 文件现值的 turn_seq，否则丢弃。**这是对共享状态的读取，不是载荷携带的回合标识**：若 harness 不保证同一 pane 的 prompt/Stop 事件串行（§1「Hook 能力」：未核实），迟到的旧 Stop 可能读到新回合的 token 并通过 CAS。因此 idle 只是证据，不是判定。
   - watcher 消费规则：`busy` 且 5 分钟内 → 不判 stalled。`idle` **只是线索，不是 `turn_ended` 来源**（原生 `turn_active` / `waiting_on_tool` 永远先否决，`lifecycle.py:419/437`；hook 写的 idle 不得改写 `TurnEvidence` 的任何字段——若把它注入 `turn_ended`，`live_turn = turn_active and not turn_ended` 会让原生「回合仍活」失效）：当 `idle.token.generation == activity.generation`、`idle.token.session_id` 非空且 == `activity.assignee_session`、`idle.token.turn_seq == turn.json.turn_seq` 时，记 `activity_hint{kind: idle, matched: true}`，并把本派单的确认窗口从 `stall_timeout_s` 缩到 `settle_confirm_s`——缩窗只在 `classify_assignee` 走到「原生证据不否决、`agent_status` ∈ settled」的分支后生效，只影响等多久，不影响判什么；`agent_status` 为 `working/blocked/unknown`、原生 `turn_active`、`waiting_on_tool`、读前延迟读到新 token 的迟到 idle，都按原路径处理。token 任一项不一致、无 `turn.json`、无 session id（如 Codex）→ 记 `activity_hint{matched: false}`，不缩窗。`probes.json.serial_events` 只记录（A1 探针信息），不参与判定。
3. 状态标签：`herdr pane report-metadata <pane> --source herdr-dispatch --state-label DISPATCH=<text> --ttl-ms <n>` 在这些时刻更新：派出后 `🛠 <card> r<n>`；`publish` 后 `📬 已交卷`；主控 `accept`/`reject` 后 `✅ 已验收` / `🔁 返工 r<n+1>`；watcher 判 stalled 时 `⏳ 无响应`。失败只记日志，不影响派工。
4. `request.md` 头部加一行固定说明：`本轮目录：<round dir>。结束回合前必须交卷（publish）。`

验收：`tests/fake_herdr.py` 流程测试：首轮启动后 pane 文件 `worker.round == 1` 且写入早于 `agent_prompt` 调用；启动时 `agent_session=None` → 绑定写入 null → 首个 prompt 后 `observe_once` 的 fill 把 session 同步到 pane 文件与 `activity.json`（三处一致）；fill 迟到而派单已进入 r2 或 pane 已被新派单重绑 → 不写；同会话复用进入第 2 轮后 `worker.round == 2`、`generation` 递增、`fingerprint` 等于 r2 的 `request_fingerprint.txt`；**竞争负例**：旧派单 accept 清理晚于新派单重绑 → 新绑定保留；旧 watcher 迟到写 r1 → 被拒；同 pane 换了新 harness 会话（terminal_id 变）→ 旧绑定判无效；**同 terminal 换了 harness/name（`agent_get.agent` 或 `name` 不同）→ `fill_session` 返回 `rejected`，不写；`observed.agent_session.value` ≠ 传入 session → `rejected`；hook 先补全后 watcher `observe_once` → `noop`，三处一致；hook 与 watcher 并发补全（线程夹具）→ 三处一致且只写一次；某处已有不同 session → `fill_conflict`，三处都不写**；`mark-start` 与 worker 写入并发 → 两者字段都在；活动协议用可控时序的夹具：(i) 已读 token 后暂停再写的四类竞争（不同 session、上一 generation、新 prompt 后旧 turn_seq、较新 busy 后旧 idle）→ 不作为 `turn_ended`；(ii) **读前延迟**：旧 Stop 在新 prompt 写 `turn.json` 之后才读取（读到新 token 并通过 CAS）→ 此时 `agent_status=working` 或原生 `turn_active` → 不触发；(iii) token 一致 + `agent_status=idle` + 原生 `waiting_on_tool` 或 `turn_active`（transcript 显示回合仍活）→ 不触发，过 `settle_confirm_s` 仍不 `missing_report`；(iv) token 一致 + idle + 无原生活动 + 未交卷 → `settle_confirm_s` 后 `missing_report`（不再等 `stall_timeout_s`）；(v) `probes.json.serial_events` 为 true/false/缺失都不改变 (iii)(iv) 的结果；(vi) 无 session id 的 idle 与无 `turn.json` → 只记线索，窗口不变；标签调用在四个时刻各出现一次；缺 `report-metadata` 能力时优雅跳过；`render_request` 输出含头部说明。

#### B1 回合守卫 `scripts/turn_guard.py`

新文件 + `tests/test_turn_guard.py`；另改 `scripts/runtime.py`（`submit` 读 `probes.json` 打 `guard_unsupported` 警告）。依赖 B0（`is_landed`、`dispatch_wait_status`）与 B2（`paneguard.py` 的绑定/活动文件 schema 与锁）。状态目录 `~/.local/state/herdr-dispatch/turn-guard/`：`panes/<pane_id>.json` + 同名 `.lock`（`worker`（B2 定义）、`markers`（marker 路径列表）、`budget`、`stop_reason`）、`probes.json`（A1 写）、`turn-guard.log`。任务 marker 在 `$(git rev-parse --git-common-dir)/trellis-guard/<task>.json`：`{task, task_json_path, worktree, branch, base_sha, last_seen_head, pane, phase: started|archived, started_at, archived_at, pause_before_merge, block_log[]}`；`base_sha` = `mark-start` 时任务分支 tip。所有 pane 文件与 marker 的读改写都在对应 `.lock` 下进行，保留未涉及字段。

| 子命令 | 行为 |
| --- | --- |
| `hook --event {stop,prompt} --harness {claude,codex,grok}` | stdin 读 hook JSON。**先按 `--event` 分流**：`prompt` 只更新活动状态（Worker：写 `turn.json` + busy；主控：清预算），永远 exit 0。`stop` 才做守卫：allow（exit 0）或 block（stderr 一行原因 + exit 2；Grok 若其 Stop 载荷声明原生阻止能力，则按 `~/.cache/firstmate-ref/bin/fm-turnend-guard-grok.sh` 的方式输出）。未预期异常 → exit 0（fail-open）并把异常追加到 `turn-guard.log`；**已知校验失败（发布不匹配、绑定无效、git 命令失败）不是异常，按判定表处理** |
| `install [--dry-run]` | 幂等合并注册到 `~/.claude/settings.json`（Stop、UserPromptSubmit，命令前缀 `[ -z "${GROK_AGENT:-}${GROK_HOOK_EVENT:-}" ] \|\| exit 0;`）、`~/.codex/hooks.json`（Stop、UserPromptSubmit）、`~/.grok/hooks/turn-guard.json`（Stop、UserPromptSubmit，格式照 `~/.grok/hooks/moshi-hooks.json`）；改前备份 `<file>.bak.<UTC>`；不动已有条目；Codex 的 hook 信任（`[hooks.state."<path>:<event>:<i>:<j>"] trusted_hash`）不由脚本写入：`install` 打印「需要在 Codex 交互式会话里接受一次 hook 信任」并把这条列入 `doctor`；不反推哈希算法 |
| `doctor` | 检查三处注册、Codex 信任状态、`HERDR_PANE_ID` 是否存在、状态目录可写、日志最近 24h 异常数、`probes.json` 各 harness 结果（`supported/unsupported/untested`）；输出 JSON |
| `mark-start` | Trellis `after_start` hook 调用（env `TASK_JSON_PATH`）：写 marker（`phase=started`，`pane=$HERDR_PANE_ID`，`base_sha`），并在 pane 锁下把 marker 路径加入 `panes/<pane>.json.markers`；无 `HERDR_PANE_ID` 时不写 |
| `mark-archive` | `after_archive` 调用：**不删 marker**，按 `branch` 或 `task` 名找到 marker，更新 `task_json_path` 为归档后路径、`phase=archived`、`archived_at`、`last_seen_head` |
| `mark-merged` | 主控在 3.5 合并后调用：用下面「解除条件」验证，成立才删 marker 并从 pane 文件移除；不成立退出码 1 并打印哪一条不满足 |
| `pause-before-merge` / `resume` | 在当前 pane 索引里的 marker 上设/清 `pause_before_merge`；`resume` 同时清预算 |
| `stop --reason "<text>"` | 在 `panes/<pane>.json` 记 `stop_reason`（一次性）；下一次 `stop` 事件消费它并 allow |
| `status [--repo]` | 打印当前 pane 绑定与 marker；`--repo` 列本仓 `trellis-guard/*.json` 全部 marker（含归档后路径、分支、`pr_url`（从 task.json 读）、pane、phase、pause） |
| `adopt <task> [--force]` | 一次原子操作（先锁旧 pane 文件再锁当前 pane 文件，按 pane id 排序加锁避免死锁）：把 marker 路径从旧 pane 的 `markers` 移除、加入当前 pane 的 `markers`、再写 `marker.pane = $HERDR_PANE_ID`；保留归档路径、PR、`pause_before_merge`。当前 pane 有有效 Worker 绑定时拒绝（`adopt_conflicts_worker`），`--force` 才覆盖。旧 pane 从此不再守卫该任务 |
| `bind-test <round_dir>` / `unbind-test` | A1 探针用：把当前 pane 绑到一个临时轮次目录（写完整 `worker` 记录，assignee 取 `agent_get($HERDR_PANE_ID)`，session 可为 null 以测试补全路径）/ 解绑 |

**marker 解除条件**（守卫自检与 `mark-merged` 共用，全部成立才解除）：`phase == archived`；分支 tip（分支已删则 `last_seen_head`）≠ `base_sha`；`is_landed(该 head, origin/<default>)` 成立（`git fetch origin <default>` 每仓每 60s 最多一次，时间记在 marker）；`worktree` 目录若仍存在则 `git status --porcelain --untracked-files=all` 为空。`phase == started` 的 marker 永不自动解除。

`stop` 事件判定顺序：

1. `HERDR_PANE_ID` 为空 → allow（不在 Herdr）。
2. 解析 Worker 绑定：环境 `HERDR_DISPATCH_ROUND`（仅无头路径设置）或 `panes/<pane>.json.worker`。绑定有效 = `<round_dir>/task.json` 的 `dispatch_id/round` 与绑定一致；`<round_dir>/request_fingerprint.txt` == 绑定的 `fingerprint`；且执行者身份按下面规则确认（一律 `observed = client.agent_get($HERDR_PANE_ID)`，要求 `observed` 的 `pane_id / terminal_id / agent / name` 与绑定 `assignee` **四项全等**，任一不等或 `agent_not_found` → 无效；terminal 相同不是充分条件）：
   - 绑定 `assignee.agent_session` 非空：一律要求 `observed.agent_session.value` == 绑定；载荷带会话 id 时还要求载荷 == 两者。任一缺失或不等 → 无效（Herdr 当前报告的 session 是身份事实，载荷只是旁证；同 pane/terminal/agent/name 换了新会话 S2 时，迟到的旧载荷 S1 不能让旧绑定继续有效，`runtime.classify_identity` 对这一观察本来就是 mismatch）。
   - 绑定 `assignee.agent_session` 为 null（待补全）：载荷会话 id 必须 == `observed.agent_session.value`，然后调用 `paneguard.fill_session`（B2 唯一入口，三处一起写）：`filled`/`noop` → 有效；`rejected`/`fill_conflict` → 无效。同 terminal 的另一 harness 或另一 name、Herdr 报告的 session 与载荷不符 → 不是待补全，是接管 → 无效。
   无效绑定按无绑定处理（进入主控模式）并记日志；**不沿用旧 Worker 身份**。
3. 绑定有效 → **Worker 模式**（优先于主控模式）：调用 `runtime.load_published_snapshot(store, task)` 成功 → allow；返回 None 或抛 `DispatchError`（`published_tampered` 等）→ 视为未有效交卷 → block，原因给出交卷命令（`request.md` 最后一段）并说明「无法完成也要交卷 `status=blocked`」；若是 tampered 另加一句「已发布制品与 published.json 不符，需新一轮」。预算：Codex/Grok 载荷 `stop_hook_active`/`stopHookActive` 为 true → allow（同一回合只拦一次，camelCase 优先）；Claude 用 `<round_dir>/.guard-blocks` 计数，≤3 次拦，之后 allow。allow 时按 B2 活动协议写 `busy-state.json idle`（token 取自本回合开始时的 `turn.json`）。
4. 否则 **主控模式**：读 `panes/<pane>.json.markers`，逐个：marker 文件不存在 → 从列表移除；满足「解除条件」→ 删 marker、移除；否则更新 `last_seen_head` 并保留。
5. 没有剩余 marker → allow。
6. `stop_reason` 存在 → 消费并 allow。
7. 派工状态（**先收集再判断**）：枚举 `state.controller.pane_id == $HERDR_PANE_ID` 的全部派单，对每个调用 `dispatch_wait_status`，分成三类：`waiting`（True）、`settled`（False 且 reason == `settled`）、`action_required`（其余全部 False——含 `collect --claim`、`resume`、`status`（终态但执行者未结算）、人工处置）。有任何 `action_required` → block，原因列出每个派单的下一步；否则有任何 `waiting` → allow；否则（只有 settled 或没有派单）进入第 8 步。历史已结算派单永远不算 `action_required`。
8. 否则 block，原因：`任务 <name> 已开始且未完成（phase=<started|archived>, branch=<b>）；继续 3.4–3.5，或先 turn_guard.py stop --reason`；若 `pause_before_merge` 为 true 且分支已有 PR，原因改为提示「用户要求合并前停：先 stop --reason」。预算：30 分钟内最多 3 次 block（pane 文件记时间戳），超出 allow 并在原因里注明预算耗尽；`prompt` 事件清预算。

`prompt` 事件：Worker 绑定有效（含待补全）→ 写 `turn.json` 并按 B2 写 `busy`；主控 → 清预算；其余不动。

`submit` 警告：`runtime.submit` 读 `probes.json`，目标 harness 结果为 `unsupported` 或 `untested` 时在返回 JSON 里加 `warnings: ["guard_unsupported: <harness>"]`（不阻止派工）。

验收：单测覆盖判定表每一行（假 payload、临时 git 仓 + bare origin、假 marker、假 state home、假 Herdr；B2 已交付，绑定/活动文件用 `paneguard.py` 真实读写）：(a) prompt 事件三种 harness 都放行且只写状态；(b) Worker：合法发布 allow；未发布 block；report.md 缺失/被改、`published.json` 的 round/id 错、非法 result、任一哈希错 → 都按未交卷 block（不是 fail-open）；r1 已发布但绑定指向 r2 未发布 → block；**启动时 `agent_session=None`：Stop 先于 watcher 补全到达（载荷带 session、terminal 一致）→ 未交卷 block，且绑定被补全；Stop 后于 watcher 补全 → 同样 block；补全后合法 publish → allow；补全后载荷 session 换了 → 不进 Worker 模式；迟到补全不得写进已进入 r2/新派单的绑定；同 terminal 换了 harness（`agent_get.agent`/`name` 不同、terminal 相同）→ 待补全绑定不补全、不进 Worker 模式；同 harness 新会话（`agent_get.agent_session` 与载荷都是新值、绑定已非 null）→ 无效；hook 先补全后 watcher `observe_once` → 三处一致、不再改写；hook 与 watcher 并发补全 → 三处一致且只写一次**；载荷会话 id 与非空绑定不一致 → 不进 Worker 模式；绑定 S1、Herdr 当前 S2、迟到载荷 S1（同 harness/name/terminal）→ 无效、不进 Worker 模式、不补写；绑定 S1、Herdr S1、载荷 S1 → 有效；绑定 S1、Herdr S1、载荷无 session → 有效；(c) Codex/Grok `stop_hook_active` 第二次 allow、Claude 第 4 次 allow；(d) 主控在主检出与在任务 worktree 都受守卫；同仓另一 pane 无绑定 → allow；(e) **`phase=started` 且分支 tip == `base_sha` → 保留**；started + worktree 有未提交业务改动 → 保留；仅空提交 → 保留；归档后未落地 → block；PR OPEN → block；同名旧 PR 已合并但当前 head 未落地 → block；归档 + 落地 + worktree 干净 → marker 删除并 allow；归档 + 落地但 worktree 又有未提交成果 → 保留；(f) 派工状态：单派单 `waiting` → allow；单派单每种 `action_required` reason → block 且 stderr 含下一步；**同 pane 一张 waiting + 一张 result_ready → block；waiting + identity_changed 未 claim → block；waiting + accepted 但同一执行者仍 working → block（reason 含 `status`）；waiting + 本轮 `wakeup_retry_exhausted` 且组事件未 claim → block（`collect --claim`）；waiting + 已 settled 的历史派单（即使控制端 session 已变）→ allow**；只有 settled 派单 → 落到第 8 步；(g) 两个 marker 一落地一未落地 → block；`stop --reason` 消费一次后再 stop 又 block；(h) 预算 3 次/30 分钟；(i) 未预期异常（坏 JSON、git 不可用）→ exit 0 且日志有记录；(j) `install --dry-run` 对本机三个文件副本生成的合并结果不丢任何现有条目；(k) `adopt`：旧 pane 索引不再含该路径、新 pane 索引含、marker.pane 更新、pause 保留；当前 pane 有 Worker 绑定时拒绝；(l) `submit` 对 `probes.json` 标 unsupported 的 harness 返回 warning。真实 harness 探针不在本卡（见 A1）。

#### B4 卡片渲染清理

文件：`scripts/templates.py`、`scripts/runcmd.py:_clauses_notes`。现状（`~/.local/state/herdr-dispatch/trk-data-0925__C1/rounds/1/request.md`）：109 行 10.6KB，`write_rel=[...]`、`产物: [{'kind': …}]`、`验收命令: [...]` 是 Python repr；6 条规则原样出现两次（第 29–30 行与 79–80 行）；task.json 的 restrictions（如「除一个 trellis-check 子代理外不要开其他 agent」）与模板静态段落（「不要开新的 agent」）互相矛盾。

要求：列表字段渲染成缩进 bullet；每条规则只出现一次（静态段落与 `background_notes`/`restrictions` 去重，只去掉逐字重复的规则句，不合并有意义的重复结构；冲突时以 task.json 为准并删掉静态句）；卡面按「目标 / 工作目录与分支 / 写入边界 / 验收项 / 交卷方式 / 附加条款」固定顺序；典型执行卡 ≤ 6KB。

验收：把该 task.json 复制进 `tests/fixtures/`，新测试断言渲染结果不含 `['`、`{'`；夹具里那 6 条重复规则句各只出现一次（按句子断言，不对全文做「无重复行」断言——fence、空行等结构行允许重复）；含全部 acceptance、restrictions 与 publish 命令；现有渲染测试更新后全绿。

#### B3 单卡单 worktree 与统一布局

文件：`scripts/runcmd.py`（`create_run`、`_worktree_root`、`_prepare_git_card`、`ingest_card`、`accept_run`/`_units_ready`/`record_release_unit`/`_delivery_corresponds`、`cleanup_run`、`cmd_run` 的 accept-run 路由 `:2240`）、`scripts/herdr_dispatch.py`（`run accept-run` parser `:282-290`）、`scripts/gitutil.py`、`references/card-planning.md`、`references/trellis-adapter.md`、`SKILL.md`。

1. `project.layout`：`task_worktree` | `parallel`。`create_run` 自动判定：`git_enabled` 且 `len(cards)==1` 且 cwd 是 linked worktree（`git-dir != common-dir`）→ `task_worktree`；plan 可显式指定。`task_worktree` 下 `create_run` 同时记录 `project.trellis_task`：在 `<common-dir>/../.trellis/tasks/*/task.json` 里找 `worktree_path == cwd` 或 `branch == 当前分支` 的任务名（找不到 → null，并在 run.json 记 `trellis_task_reason`）；同时记录 `project.trellis_developer`：按 Trellis `common/paths.py:100-116` 的顺序自行解析（`TRELLIS_DEVELOPER` 环境变量 → cwd 的 `.trellis/.developer` → 主检出的 `.trellis/.developer`），不 import Trellis；解析不到 → null。
2. `task_worktree`：`integration_dir = cwd`，`integration_branch = 当前分支`，不 `create_branch`/`add_worktree`；`_prepare_git_card` 令 `card.git = {worktree: cwd, branch: 当前分支, base_sha: HEAD, head_sha: HEAD}`；`ingest_card` 不 merge，只校验 `source_head` 是集成 HEAD 或其祖先，`ingested_sha = source_head`；`cleanup_run` 不删任务 worktree（留给 `trellis_gc.py`），只扫窗口。
3. `task_worktree` 下的主控写入规则与顺序（写进 `card-planning.md` 与 `trellis-adapter.md`）：派卡前主控先提交任务文档改动；卡在执行期间主控不在该目录写文件；`publish → ingest → accept-card` 之间主控不得提交（`live_delivery_matches` 的精确快照要求保留）；`accept-card → accept-run` 立即连做（该布局下 `accept-run` 是内容验收，允许先于 `record-unit`；`parallel` 与旧 run 的「record-unit 在前」顺序不变），`parent_acceptance = {head: H0, ...}`；之后主控自己的收尾提交允许，但受**收尾允许清单**约束：
   - 允许清单（`project.trellis_task = <task>` 时）：`.trellis/spec/**`；`.trellis/tasks/<task>/**`；`.trellis/tasks/archive/*/<task>/**`；`.trellis/tasks/<child>/task.json`（`<child>` 限 task.json `children` 列出的任务）；`.trellis/workspace/<trellis_developer>/journal-*.md`；`.trellis/workspace/<trellis_developer>/index.md`（与 `add_session.py` `_auto_commit_workspace` 通过 `get_developer` 只暂存当前开发者一致；`trellis_developer` 为 null 时这两项不在清单，其他开发者的 workspace 路径永远不在清单）。`trellis_task` 为 null 时清单为空。
   - 核验：`git diff --name-status -M H0..<live HEAD>` 的每个条目路径（R 的旧新两侧都算）都在清单内；任何其他路径——包括 `.trellis/scripts/**`、`.trellis/workflow.md`、`.trellis/config.yaml`、其他任务目录、业务代码——→ `parent_acceptance` stale，`record-unit`、`_units_ready`、`cleanup_run` 都拒绝并要求对当前 HEAD 重新验收：`herdr_dispatch.py run accept-run <run_id> --head <HEAD> --evidence "<核对了什么>" --reason "<为何重新验收>"`。
   - **重新验收的语义**（S10/S12，r6 简化）：CLI（`scripts/herdr_dispatch.py:282-290` 的 `accept-run` parser）保留必填 `--evidence` 与可选 `--head`，新增可选 `--reason`；`runcmd.accept_run(store, run_id, *, evidence, head=None, reason=None)`，`cmd_run` 路由传 `reason`。`--head` 省略时取集成目录当前 HEAD。已有 `parent_acceptance` 且解析后的 head == 其 `head` → 幂等返回（现行为）；不同 → 直接覆盖为 `parent_acceptance = {head, prior_head, origin_head, changed_paths, reason, evidence, accepted_at, history}`——`origin_head` = 首次验收的 head（不变的原卡快照），`changed_paths` = `git diff --name-only -M origin_head..head` 的路径列表（相对原卡快照，R 的旧新两侧都列），`history` 追加 `{head, prior_head, reason, accepted_at}`；`--reason` 可选，缺省记 null（不设 `reaccept_requires_reason` 错误）。不改写卡片 `published.json`、receipt 与 `accepted_contents`。`record-unit` 核验 `is_landed(parent_acceptance.head, origin/<target_ref>)`；`changed_paths` 与卡片 `produces`/`accepted_contents` 重叠的路径，`_delivery_corresponds` 对该路径改用 `parent_acceptance.head` 树里的内容哈希（路径在该树里不存在 → 该产物允许在目标里不存在），并把这些路径记入 unit 证据 `amended_paths`（只记路径，不记 M/D/R 状态；主控对原卡产物的修正由父验收背书，可追溯）。不重叠时行为不变。
4. `parallel`：`_worktree_root` 默认改为 `<主检出父目录>/<主检出名>-wt/<run_id>/`（主检出取 `git worktree list --porcelain` 第一项），不再放 `~/.local/state/.../runs/<run>/worktrees/`。

验收：新测试：单卡 run 全流程（create → dispatch → publish → ingest → accept-card → accept-run → 主控追加一个只改 `.trellis/spec/` + `.trellis/tasks/<task>/` + journal/index 的归档提交 → 模拟 squash 到 main → record-unit → cleanup）只产生 0 个新 worktree、0 个新分支；`trellis_task` 由 worktree_path/branch 正确解析；负例：主控在 publish→ingest 之间提交 → `ingest` 拒绝；在 ingest→accept-card 之间提交 → `accept-card` 拒绝；**accept-run 之后：新增 `.trellis/scripts/x.py`、修改 `.trellis/workflow.md`/`config.yaml`、修改另一任务目录、把允许路径重命名到清单外、修改业务文件（即使已 squash 到正确的 main 且原卡产物不变）→ record-unit 与 cleanup 都报 stale 并 keep；对当前 HEAD 重新 accept-run（带 reason）后通过**；修改原卡产物 → stale → 重新 accept-run 后 record-unit 通过且 unit 证据含 `amended_paths`、原 `published.json` 字节不变；未重新 accept-run 直接 record-unit → 拒绝；**真实 CLI parser**：文档里的恢复命令原样可执行；缺 `--evidence` 仍拒绝；省略 `--head`：同 HEAD 幂等、新 HEAD 覆盖且 `history` 增一条；首次与重新 accept-run 都不强制 `--reason`；连续两次重新验收修改不同原卡产物 → `changed_paths` 累计两者、`history` 两条、`amended_paths` 两条；删除或重命名一个原卡产物 → 重新 accept-run 后 record-unit 通过，`amended_paths` 含涉及路径；**另一开发者的 journal/index 改动 → stale；`trellis_developer` 为 null 时任何 workspace 改动 → stale；当前开发者的 journal/index 正常收尾 → 通过**；多卡 run 的 worktree 落在 `<repo>-wt/<run_id>/`；旧 run.json（无 `layout`/`trellis_task` 字段）仍可 `status`/`cleanup`，`parallel` 布局的 record-unit/accept-run 顺序行为不变。

#### B6 清理的落地证明

文件：`scripts/runcmd.py:cleanup_run`、`_units_ready`（依赖 B0 `gitutil.is_landed`、B3 收尾允许清单）。B6 是 `card/*`、`run/*` worktree 与分支的唯一删除者（§0 #11）。

1. 卡片 worktree：`live_head != accepted_head` 时不再直接 keep，改为 `is_landed(live_head, integration HEAD)` 成立即可删；否则 keep 并给出原因。现有门禁保留并显式列出：本运行创建；干净；无活跃写入者（`resources.json`/`resource-scopes` 无占用、该卡派单 `dispatch_settled` 不在飞）；该卡已 accept；**`_units_ready` 成立（`runcmd.py:1915`：`parent_acceptance` 已记录且按 B3 清单不 stale、所有必需卡已验收、所有 unit 已 `record-unit`）——卡片 worktree 与集成 worktree 共用这一条，不为卡片放宽**。缺任一 → keep 并写明是哪一条；内容落地证明只替代 `live_head == accepted_head` 这一项。
2. 集成 worktree（`parallel` 布局）：目标不是 `project.base_branch`，而是每个已登记 release unit 的 `repo/target_ref`：`parent_acceptance` 已记录且按 B3 收尾允许清单仍有效、所有必需卡已验收、所有 unit 已 `record-unit`、且对每个 unit `is_landed(integration HEAD, origin/<unit.target_ref>)` 成立 → 删 worktree 与 `run/<id>/integration` 分支；没有 unit、任一 unit 未落地、验收 stale、脏树、有写入者 → keep 并说明。
3. 输出格式不变，新增 `proof` 字段。

验收：临时仓测试：从 `task/*` linked worktree 创建多卡 run，squash 到 main 并删除远端 task 分支后仍能清理；错误目标（unit.target_ref 不含成果）、缺 `parent_acceptance`、stale acceptance（H0 后有清单外改动）、未验收新增成果、脏树、写入中（该卡派单在飞）→ 均 keep 且原因准确；**从 main 新建、干净、HEAD==基线但派单仍 running 的卡片 worktree → keep `dispatch_in_flight`**；add→revert 反例 keep；对 `enf-0926` 跑 `run cleanup`（不加 `--apply`）把输出附在报告里。

#### B5 无头审核会话

文件：`scripts/adapters.py`（每个 adapter 新增 `headless_args(task, request_path, output_path)`；Claude adapter 返回 `None` 表示不支持）、新 `scripts/headless.py`（起进程、cwd=task cwd、env 加 `HERDR_DISPATCH_ROUND`、stdout/stderr 落轮次目录、超时默认 1800s、结束后按现有 publish 协议检查 `published.json`）、`scripts/watcher.py`（`session=headless` 时不建 tab、不做窗口清理、不写 pane 绑定）、`scripts/runcmd.py`（`create_run` 保留 `reviewer.session`；`_build_task` 把它写进 `task.assignee.session`；`session` 只接受 `headless`）、`scripts/protocol.py`（`assignee.session` 字段校验）、`references/protocol.md`、`references/adapters.md`、`references/review-card.md`。

1. 触发：task.json `assignee.session = "headless"`，或 plan `reviewer.session = "headless"`；仅 `role == reviewer`；`harness == claude` 时报错 `headless_unsupported`；旧 task/plan 没有该字段行为不变。
2. 命令以真实 `--help` 为准，预期：`codex exec --dangerously-bypass-approvals-and-sandbox -C <cwd> …`（stdin 喂 request.md）；`grok -p … --always-approve --no-subagents --output-format json`；`cursor-agent -p --output-format json --trust --yolo …`。Codex 信任仍走现有 `codex_trust_folder`。
3. 交卷协议不变：reviewer 仍需写 `report.md` + `result.json` 并跑 publish 命令；进程退出而未交卷 → 现有 `missing_report` 路径，并把 stdout 尾部 40 行附进事件。

验收：用假 harness 可执行文件（仿 `tests/fake_herdr.py`）测试三种 harness 的命令组装、超时、交卷/未交卷两种结局；**集成测试**：含 `reviewer.session=headless` 的 plan 经 `run create → run dispatch` 到假无头进程，断言不调用 `tab_create`/`pane_split`、任务 `assignee.session == "headless"`、子进程环境含 `HERDR_DISPATCH_ROUND`、结果可 `publish`/`collect`；普通 `submit` 路径同样断言；真实探针：对本计划文件派一张 `contract_review` 无头卡给 codex（用候选 CLI 路径 `~/.skills-manager/skills-wt/workflow-v2/herdr-dispatch/scripts/herdr_dispatch.py`），得到 `published.json`。

#### B7（可裁剪）转向收件箱

文件：新 `scripts/inbox.py`、`scripts/herdr_dispatch.py`（`steer <dispatch_id> "<text>"`、`inbox <dispatch_id>`）、`scripts/watcher.py`、`scripts/templates.py`（卡面加一段收件箱约定）。按 `~/.cache/firstmate-ref/bin/fm-task-inbox-lib.sh` 的语义：`<round>/inbox/NNN.msg`（三位序号、temp+atomic mv）；门铃是一行常量文本，通过现有 `client.agent_prompt` 投递；Worker `mv` 到 `inbox/handled/` 即 ack；watcher 90s 后未 ack 再响，最多 3 次后事件 `steer_unacked`；pane 为 dead/missing 不敲。验收：假 Herdr 测试三种结局（首响即 ack、第二次响后 ack、三次未 ack 升级）。

### S3 应用与本机

#### A1 本机用户级 hooks、真实探针门禁、trellis-wrap-up

前置：B0–B2 已合并到 skills `main`。

1. 主控运行 `turn_guard.py install --dry-run` 看合并结果，再 `install`；`doctor` 结构全绿；Codex 信任若需交互，让用户在本 Herdr 会话里 `! codex` 接受一次。
2. **真实探针（三 harness 门禁）**：对 Claude / Codex / Grok 各开一个 Herdr 标签页（`herdr tab create` + `herdr agent start`）。每个 harness 跑两条时序：(a) **先建立身份**：先发一句无关 prompt 让 harness 产生 session，再在该 pane 的 shell 里 `turn_guard.py bind-test <临时轮次目录>`（目录含合法 `task.json`、`request_fingerprint.txt`；assignee 取 `agent_get` 的完整身份），让它「什么都不做直接结束」，观察：第一次被拦且 stderr 原因正确、第二次放行（Codex/Grok）或第 4 次放行（Claude）；随后 `publish` 一份合法结果再结束 → 放行；(b) **补全路径**：新 pane 启动 harness 后立刻 `bind-test`（session 为 null），再发第一句 prompt 让它直接结束 → 必须被拦，且绑定的 `agent_session` 已被补全并与 Stop 载荷的会话 id 对应、`state`/pane/`activity.json` 三处一致。(c) **事件串行探针**：用探针专用 hook 配置（Stop 命令前加 `sleep 8`，不进正式注册）让 harness 结束一个回合，Stop hook 仍在睡眠时立刻提交下一句 prompt：prompt hook 写的 `turn.json.started_at` 晚于 Stop hook 退出时刻、且 Stop hook 读到的 `turn_seq` == 本回合 prompt hook 写入的值 → `serial_events=true`；否则 false。(d) **接管负例**：在 (b) 的 pane 里停掉执行者后用另一 harness 在同一 pane `herdr agent start`（terminal 不变），让它直接结束 → 不得被当作 Worker（日志 `fill_session: rejected agent/name`）；再用同一 harness 重新启动（新 session）→ 同样不得进 Worker 模式。每个 harness 记录：实际加载的 hook 配置文件、命令串、载荷（含会话 id 字段名）与退出码、四条时序的结论，写入 `~/.local/state/herdr-dispatch/turn-guard/probes.json`（每条 `{harness, stop_supported, prompt_supported, fill_supported, takeover_rejected, serial_events, payload_session_field, hook_files, checked_at}`；`doctor`、`submit` 警告与 B2 消费规则读它）。任一 `unsupported` 或 `serial_events != true`：不宣称全绿；B2 对该 harness 的 idle 自动只作线索；A3 不得依赖该 harness 的守卫。
3. `~/.skills-manager/local-src/trellis-wrap-up/SKILL.md`（56 行）改为恢复入口措辞（`turn_guard.py status --repo` → `adopt` → 续做 3.4–3.5），与 T2 一致；用 skills-manager 现有更新命令同步部署副本（先读 `skills-manager-cli skills --help`）。

验收：`doctor` 输出 JSON 附报告；`probes.json` 三条记录（每条含两条时序）；hooks 文件备份存在；三个 hooks 文件的原有条目一条不少（`install --dry-run` 前后 diff）。

#### A2 sanctions-radar 应用 PR

前置：T1–T4 已合并到模板仓 `main` 并打 tag `v1.6.0`；B 卡已合并到 `~/.skills-manager/skills` `main`。

cwd `~/Documents/Project/sanctions-radar`，分支 `chore/workflow-v2`（从最新 `origin/main`），worktree `~/Documents/Project/sanctions-radar-wt/chore-workflow-v2`：

1. `trellis workflow --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.6.0 --template solo-github-flow --create-new` 生成 `.trellis/workflow.md.new`，与现有文件 diff 确认只有预期差异（T1 表里「不上游」的项目专有内容此时应已在 `AGENTS.md`，若没有则补进 `AGENTS.md`），再替换。**不改** `.trellis/config.yaml` 的 `registry.spec`；安装的 workflow 版本以文件首行注释为准并写进 PR 描述。
2. `bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.6.0/scripts/setup.sh) --dry-run` 然后不带 `--dry-run` 运行。它会 `[UPDATE]` `scripts/trellis_gc.py`（`cmp` 与模板 tag 内容一致），并对三份已存在的 `trellis-setup/SKILL.md`（`.agents/`、`.claude/`、`.grok/`，现为 v1.2.1）打印 `[MANUAL]` diff 并 exit 2：主控逐份人工 diff，保留项目定制（如有），把 bootstrap URL 与正文更新到 v1.6.0 版本；exit 2 的每一条 `[MANUAL]` 都要在 PR 描述里写处置结果，不把 partial 当完成。installer 留下的 `.bak`/`.tmp` 文件移到 scratchpad（不进提交），最终 `git status` 只含本 PR 的文件。
3. `.trellis/config.yaml` 加：
   ```yaml
   hooks:
     after_start:
       - "python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py mark-start 2>/dev/null || true"
     after_archive:
       - "python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py mark-archive 2>/dev/null || true"
   ```
4. `python3 scripts/trellis_gc.py --archive-idle-days 7`（dry-run）输出贴进 PR 描述；不 `--apply`。
5. PR、CI 绿、squash 合并（主控做合并）。

验收：PR diff 只含 `.trellis/workflow.md`、`.trellis/config.yaml`（仅新增 `hooks:` 块）、`scripts/trellis_gc.py`、`.agents/skills/trellis-setup/SKILL.md`、`.claude/skills/trellis-setup/SKILL.md`、`.grok/skills/trellis-setup/SKILL.md`、可能的 `AGENTS.md`；`git diff` 里 `registry:` 块无变化；`head -1 .trellis/workflow.md` 含 `v1.6.0`；三份 SKILL.md 的 bootstrap URL 都是 `v1.6.0`；合并后再执行任一 SKILL.md 的 bootstrap 命令 `--dry-run`，`scripts/trellis_gc.py` 显示 `[SKIP] already current`（不会降级）。

#### A3 真实冒烟

前置：A1、A2 完成。新 Herdr 标签页起一个 Grok 主控（用户平时的主控之一），在 sanctions-radar 走一个极小的真实任务（例如 `README.md` 补一句 workflow v2 说明）：规划 → 「开始」→ 观察它不在报告后停下，一路到 squash 合并与 GC。记录：是否被守卫拦过、拦的原因是否正确、marker 是否在合并后消失、最终 worktree/分支是否清干净。同样流程再用 Claude 主控跑一次（用「开始，合并前停」验证暂停、`resume`、继续合并）。任一失败 → 回到对应 B/T 卡返工。

#### A4 残留清理（需用户看一眼再动手）

`python3 scripts/trellis_gc.py --archive-idle-days 7` 的 dry-run 输出给用户。按 §1 事实的**现时**预期：任务归档候选 0 个——`08-31`、`09-03` SKIP `planning`（且有 children），`09-10` SKIP `branch_is_default`，`09-13` SKIP `no_pr`（`pr_url=null`；其 `feat/event-source-translation` 本地 ref 也已不存在）；`task/*` 残留按生命周期证据 + 内容证明判定；4 个卡片 worktree（enf-0926 E1–E3、sr-empty-days-20260920-A）GC 只 INFO，由 `herdr_dispatch.py run cleanup` 按 B6 判定；按 B6 门禁与 Codex r4 只读核对的事实，**现时预期全部 keep**：

| worktree | 事实（2026-10-01） | B6 归宿 |
| --- | --- | --- |
| enf-0926 E1 / E2 / E3 | 干净；live head ≠ accepted head；对 integration `d66ab9a` tree_equal；已 record-unit `pr:82`/`pr:83`；`parent_acceptance = null` | keep（缺父验收；tree_equal 只解决 extra-commit 一项） |
| sr-empty-days-20260920 A | 干净；live == accepted `8cf26c6`；无 parent_acceptance、无 unit、登记的 integration 目录不存在 | keep（缺父验收/unit/integration） |

要让它们被清掉只有两条路，都由用户决定：(i) 主控用 `is_landed(live_head, origin/main)` 给出证据后，用户授权手动 `git worktree remove` + `git branch -D`（在 B6 门禁之外的人工操作，逐个列出）；(ii) 保留。实施时以新鲜 dry-run 为准，不把 task.json 的 branch 字符串当存在性证明。用户确认后 `--apply` / `run cleanup --apply` / 手动删除。这是本计划唯一需要用户确认的破坏性步骤。

## 4. 验证矩阵

| 场景 | 必须观察到 |
| --- | --- |
| 主控在 3.4 报告后 | 不停，直接进 3.5；守卫无动作 |
| 主控在报告后试图结束（非四种情形） | 被拦一次，stderr 原因正确；继续后完成合并 |
| 任务刚开始、分支 tip == 基线、或只有未提交改动 | marker 保留；试图结束被拦 |
| 主控归档后、push/PR/CI 前试图结束 | 仍被拦（marker `phase=archived`，未落地） |
| PR 已开未合并时试图结束 | 被拦 |
| 同名旧 PR 已合并、当前成果未落地 | 被拦 |
| 合并后（`mark-merged` 或守卫自检） | marker 删除；结束回合放行 |
| 主控遇到缺凭据 | `stop --reason` 后能结束；下一回合再结束又受守卫 |
| 用户说「开始，合并前停」 | 合并前停一次；`resume` 后合并 |
| 会话中断后在新 pane 恢复 | `status --repo` 可见；`adopt` 后新 pane 受守卫、`resume` 生效，旧 pane 不再持有 |
| 主控派工在飞、watcher 活、唤醒路径有效（含有 name 的控制端） | 结束回合不被拦 |
| 主控派工 `result_ready` 未领取 / watcher 已死 / `needs_attention` / `identity_changed` / 唤醒耗尽 / pane 换了 controller | 被拦，原因含 `collect --claim`、`resume` 或人工处置 |
| 同 pane 一张正常等待 + 一张需处置 | 被拦（需处置优先） |
| 同一仓另一个无关会话（无绑定） | 不被拦 |
| 主控在任务 worktree 里工作 | 与在主检出一样受守卫 |
| Worker 未交卷就结束 | 被拦，提示交卷；Claude 最多 3 次、Codex/Grok 每回合 1 次 |
| Worker 首个 prompt 后才有 session（Stop 先于/后于 watcher 补全） | 仍被拦；绑定补全；合法交卷后放行 |
| Worker 发布制品被改/缺失 | 视为未交卷，被拦 |
| Worker 同会话 r1 已交卷、r2 未交卷 | 被拦（绑定指向 r2） |
| Worker pane 被别的会话接管 | 绑定失效，不按旧 Worker 放行 |
| Worker 交卷后结束 | 放行；标签变 `📬 已交卷` |
| 用户/主控向 Worker 提交新 prompt | 放行，`turn.json` 推进，`busy-state` 变 busy |
| 迟到的旧回合/旧会话/旧 generation Stop，或读前延迟读到新 token 的旧 Stop | 不触发 `missing_report`（token 不一致 → 线索；token 一致但新回合 working / 原生 turn_active → 原生证据否决） |
| token 完整一致、`serial_events=true`、无原生活动、未交卷的 idle | `settle_confirm_s` 后 `missing_report`（不等 `stall_timeout_s`）；`serial_events` 非 true 的 harness 只记线索 |
| 主控派工已 accepted 但同一执行者仍在写 | 被拦，原因含 `status`；`status --all` 退出码 1 |
| 单卡运行 | 0 新 worktree、0 新分支；合并后 `trellis_gc.py` 删任务 worktree |
| 单卡运行 accept-run 后改了业务文件或 `.trellis/scripts`、workflow、config、其他任务 | record-unit/cleanup 报 stale，需重新 accept-run |
| 单卡运行 accept-run 后主控修正原卡产物 | stale → 重新 accept-run 后通过，unit 证据含 `amended_paths`，原 `published.json` 不变 |
| 多卡运行 | worktree 全在 `<repo>-wt/<run_id>/`；cleanup 用落地证明，目标是 unit 的 `target_ref` |
| squash 合并后残留卡片分支 | `run cleanup` 判 landed 并删除；GC 只 INFO |
| 派单仍 running 的干净基线卡片 worktree | `run cleanup` keep；GC 不碰 |
| 任务仍 in_progress 的干净基线 `task/*` worktree（另一主检出跑 GC） | SKIP `task_active` |
| 目标分支 add→revert、源分支独立 add | GC / cleanup 都保留 |
| 7 天无动静、已 completed 或 PR MERGED 且落地的任务 | GC dry-run 列为归档候选；`--apply` 归档并推送，远端只多归档提交 |
| planning / branch=null / branch=main / 无 PR 的 in_progress（含只有空提交） | GC SKIP 并给原因 |
| 从非默认分支、ahead、落后或分叉的主检出跑 GC `--apply`；区间里有 merge 提交 | 不归档不推送，给原因 |
| push 失败后再次运行（零候选） | pending 重试并清记录；`git status` 全程干净 |
| 无头 codex 审核（plan 与 submit 两个入口） | 无新 Herdr 标签页；`published.json` 产生 |
| 旧 run.json / 旧轮次 | `status`、`collect`、`cleanup` 仍可用 |
| 合并派工脚本前 | `status --all`（默认只读）退出码 0；accepted + 死 watcher + 同一执行者 working 或 idle 但原生活动时为 1，无论 holder 值；`holder_unsettled` 经 `--settle` 收敛后再只读为 0 |
| watcher 死后 `cancel` | 执行者 gone/replaced/静止 → 立即 cancelled 且 holder settled，制品与事件同 watcher 路径；执行者仍活 → `cancelling`，`resume` 恢复监督；有 `watcher.stop` 也能落地；并发只落地一次 |
| GC 遇到 linked worktree 的真实 `index.lock` | SKIP `git_locked`，目录/分支保留 |
| A2 之后再跑旧 setup skill | GC 不被降级（三份 SKILL.md 已指向 v1.6.0） |

## 5. 风险与回退

| 风险 | 处理 |
| --- | --- |
| 边改派工脚本边用它派工 | B 卡在 worktree 分支里做；主控只在门禁退出码 0 时快进合并；已在跑的 watcher 进程用旧代码，不热替换；skills `main` 被 auto backup 推进时先 rebase 再 ff（S0） |
| Codex 用户级 hook 信任模态挡住 Worker 启动 | B1 `install` 写 `trusted_hash`；不能反推则先由用户在交互会话接受一次，`doctor` 报告状态；未信任前不给 Codex Worker 派工 |
| Grok 加载 Claude settings 的 hooks 造成双触发 | 命令前缀 env 守卫；A1 探针验证 |
| 某 harness 拦不住 | A1 记 `unsupported`，`submit` 警告，不假装全绿 |
| 守卫误拦（预算） | 主控 30 分钟 3 次、Worker Claude 3 次 / Codex、Grok 每回合 1 次；`resume` 清计数；未预期异常 fail-open 且进日志 |
| marker 残留（合并后没跑 `mark-merged`） | 守卫每次评估都做解除自检；`status --repo` 可见；`mark-merged` 手动清 |
| pane 绑定竞争 / session 补全 | 锁 + generation + 身份核对；补全只走 `paneguard.fill_session`（`agent_get` 的 pane/terminal/agent/name 四项全等 + Herdr 当前 session 一致），三处一起写、只写 null、冲突不写；清理只清自己的记录 |
| harness 的 hook 跨事件不串行 / Stop 读到别的回合的 token | B2 消费规则不依赖串行：hook idle 永远只是线索（token 全匹配时把确认窗口从 `stall_timeout_s` 缩到 `settle_confirm_s`），不是 `turn_ended` 来源，原生 `turn_active`/`waiting_on_tool` 永远先否决；`serial_events` 只记录不门控（r6） |
| `finalize_cancel` 提前结算活执行者 | 资格：working/unknown/`query_failed`/`evidence_failed`/原生回合仍活 → 不结算；无原生来源的 harness 只凭 `agent_status` idle ≥ `settle_confirm_s`（`idle_no_evidence`）；幂等占位（pid 已死才接手）+ `completion_recorded` 最后写；`resume` 恢复监督而不是强行落地 |
| workflow.md 替换后 hook 解析失败 | T1/T2 都用 `inject-workflow-state.py` 的解析函数验证；A2 用 `--create-new` 先看 diff |
| 模板与项目专有内容混杂 | T1 表明确不上游项；专有内容进 `AGENTS.md` |
| 兜底归档误归档活任务 / 误推提交 | 只认 completed / PR MERGED；planning 一律 SKIP；同步前置 + 单提交合法性检查（拒 merge/root）；pending 在 git common dir；默认 dry-run；A4 先给用户看 |
| GC 删掉仍在用的 worktree | 生命周期证据先于内容证明；card/run 不归 GC；in_progress 任务 SKIP；守卫 marker 存在 SKIP |
| 升级到 Opus 时旧 Grok 还在写 | §6 升级流程先 `cancel` 等 settled 再派 |
| 回退 | 模板：`trellis workflow -m …#v1.5.1 --template solo-github-flow`；脚本：合并前记录 SHA，回退用 `git -C ~/.skills-manager/skills reset --hard <SHA>`（仅回退场景）；hooks：`install` 的 `.bak.<UTC>` 备份 |

## 6. 执行流水线（主控 = 本会话）

```text
P0  Codex 审计划（contract_review，只读，本文件）→ 主控取舍 → 改计划（r1–r4 + B 卡复审 r5 已完成；r6 起不再开计划审核轮，契约由各卡 code review 在代码上验证）
S0  主控建 worktree/分支、记基线、遗留派单清理（已做）；每次合并派工脚本前跑门禁
T1 → T2 → T3 → T4          每张：Grok 执行 → Codex code_review → 主控验收 → 合入模板 workflow-v2
B0 → B2 → B1 → B4 → B3 → B6 → B5 → (B7)   每张：同上 → 合入 skills-wt/workflow-v2
主控：模板 workflow-v2 → main（PR，沿用仓库习惯），打 v1.6.0；skills workflow-v2 → main（门禁退出码 0 时）
A1（探针门禁）→ A2 → A3 → A4
```

依赖说明：B0 → B1/B2/B6（`is_landed`、`dispatch_settled`、`dispatch_wait_status`、`finalize_cancel`）；B2 → B1（`paneguard.py` 的绑定/活动文件 schema、锁与生产端写入，B1 只消费；B1 交付后复跑 B1+B2 集成测试）；B3 → B6（收尾允许清单与 `parent_acceptance` 语义）；B2 → A1；T4 + tag → A2；A1 + A2 → A3；B 全部合并 → §6「审核卡改无头」；S0 清理 → 合并门禁。T 卡与 B 卡互不依赖，可交错派工（但同一时刻每个仓只有一张执行卡）。

规则：

- 派工方式：`herdr_dispatch.py submit` 单次派工，`cwd` 与 `allow_write` 按卡面；执行卡 harness `grok`，审核卡 harness `codex`。**审核卡改无头**只在 B 卡全部合并到 skills `main` 之后（用 `~/.skills-manager/skills/herdr-dispatch/scripts/herdr_dispatch.py`）；此前一律 Herdr 标签页。审核卡的 `monitor_paths` 只放被改的仓，不放含 `.codegraph/` 缓存的只读参考仓。
- 候选 CLI：B0 合入 `skills-wt/workflow-v2` 后、合并到 `main` 前，`status --all` 用 `python3 ~/.skills-manager/skills-wt/workflow-v2/herdr-dispatch/scripts/herdr_dispatch.py status --all`（只读命令，读同一个 state home）。
- 返工：Codex 审核 verdict 为 fail 或主控验收不过 → `reject` 带全部 findings，同一 Grok 会话第 2 轮。
- 升级到 Opus：第 2 轮仍不过 → `cancel` 该派工 → 等 `status` 显示 assignee settled（旧写锁释放）并 claim 取消完成事件 → `cleanup` 关页 → 新 dispatch_id 派 `claude` 模型 `claude-opus-5-5`（Herdr 标签页；模型 id 以当场 harness 列表核对）带原卡 + 全部 findings，再 Codex 审。
- 验收标准：卡面「验收」全部有证据；测试全绿；diff 只碰卡面文件；提交信息合规。主控自己复跑测试，不只看报告。
- 合并：主控在各自仓 `git merge --ff-only`；合并派工脚本前按 S0 门禁检查并留 log。
- 计划变更：执行中发现事实与本计划不符，先改本文件对应卡再派，不口头改要求。
- 审核意见取舍：reviewer 的每条 must_fix 分 采纳 / 不采纳（理由）/ 延后（§9），先给用户看表再改计划或再派；只在事实属实且不修会在本机用法（单用户、单机 Herdr、Codex/Grok/Claude）里出错时采纳。code review 的派单文件要写明阻塞项门槛（违反卡面/破坏契约/事实错误才算），风格与可做可不做的优化放建议项。

## 7. Codex 审核清单（P0 r5，历史记录；r6 起不再开计划审核轮）

请重点核对：(1) §8「第 4 轮」段每一条是否真正解决了对应 r4 项；(2) B0 第 2 条判定顺序 1–8 是否穷尽且与 `settle_dispatch` 的资格、S0 只读脚本三处一致；`holder_unsettled` 经 `--settle` 是否都能收敛（replaced / attributed / needs_attention+gone），只读路径是否真的零写入；原生证据（`collect_turn_evidence` / `evidence_blocks_settled`）在死 watcher 下是否可用（它依赖 `state.activity_scan`、transcript 等，指出取证失败时的行为）；(3) B0 第 3 条 1–9 顺序：历史 settled 派单、终态在飞、`wakeup_retry_exhausted` 按 `group_event_id` 领取、`primary_wakeup` 的处理，与 B1 第 7 步三分类是否一致，混合派单是否还有漏拦/误拦；(4) B2 第 2 条：idle 改为经 `classify_assignee` 的 `turn_ended` 来源后，是否仍存在绕过原生 `working`/`turn_active`/`waiting_on_tool` 否决的路径；`serial_events` 降级是否可机械实现；读前延迟夹具是否成立；(5) B2 `fill_session` 唯一入口（`agent_get` 四项全等 + Herdr 当前 session 一致、三处一起写、锁顺序）与 B1 第 2 步是否堵住同 terminal 接管与三处分裂；`classify_identity` 不参与补全资格是否有副作用；(6) B3：`trellis_developer` 解析与清单是否与 `add_session.py`/`paths.py:100` 一致；`accept-run` parser/路由/`accept_run` 签名改动是否闭合、`reaccept_requires_reason` 触发条件是否合理；`origin_head` 累计与 tombstone/rename 是否与 `_delivery_corresponds`（`runcmd.py:1852-1880`）相容；(7) T3 `git rev-parse --path-format=absolute --git-path index.lock` 在 linked worktree、主检出、bare origin 三种情况下的输出是否如预期，`--path-format` 在本机 git 版本是否可用；(8) B0 第 5 条 `finalize_cancel`：资格是否与 `handle_cancel` 逐项等价、占位/CAS 是否会双重 finalize 或死锁、`resume`/`cleanup`/`watch_loop` 改动是否闭合；(9) B6 `_units_ready` 共用与 A4 表是否一致；(10) §6 顺序与依赖说明。输出：阻塞项 / 建议项 / 已核对无问题项三段，每条给文件与行号。

## 8. 审核回应

### T2 code review r1（`workflow-v2-T2-review-1001`，对 `9131b9b`；verdict fail）

| 项 | 取舍 | 处理 | 位置 |
| --- | --- | --- | --- |
| T2R-01 3.5 第 9 步把兜底归档写成「独立维护命令、不属于本步」，与 T3 `--archive-idle-days` 默认 7 冲突 | 采纳（事实属实：§0 #1、T3 第 4 条已定默认 7，`--apply` 即启用；主控 T2 卡面第 4 条自己写错） | T2 r2：该句改为「同一次 `--apply` 也做兜底归档（默认 7、0 关闭），只认完成证据 + 闲置 + 同步前置，归档提交推到 origin/<default>」；不改 T3 默认值 | workflow.md 3.5 第 9 步 |
| T2S-01 pause/resume 作用于当前 pane 索引的 marker，建议说明多 marker 情形 | 不采纳 | 单用户单机下一个主控 pane 通常只有一个 started 任务；B1 `status` 本就列出该 pane 全部 marker，文案不再加分支 | — |
| T2S-02 3.5 末段「checks … fail → stop」比新规则宽 | 采纳（r2 顺手改，一句话） | 区分范围内（先修复再验证）与范围外（`stop --reason` 后停） | workflow.md 3.5 末段 |

### 第 5 轮（B 卡复审 r5 → r6，`workflow-v2-plan-bcards-review-1001`，按 §6「审核意见取舍」处理）

| 项 | 取舍 | 处理 | 位置 |
| --- | --- | --- | --- |
| WF2-002 claim 后 `last_error` 仍拦等待 | 采纳 | 第 7 步改看未领取的可领取事件；`last_error` 只诊断不拦 | B0 第 3 条第 7 步、验收 |
| WF2-004 hook idle 压过原生 `turn_active` | 采纳并简化 | idle 只做线索、只缩确认窗口，不注入 `turn_ended`；`serial_events` 只记录不门控 | B2 第 2 条、验收 (ii)–(vi) |
| WF2-015 取证失败返回 limitation 被放行 | 采纳 | `evidence_failed` 含 limitation 情形；无原生来源的 harness 只凭 `agent_status`；S0/`status --all`/`settle_dispatch`/`finalize_cancel` 同规则 | B0 第 2 条第 4 步、第 5 条、验收 |
| WF2-019 非空绑定不核当前 session | 采纳（理由是简化） | 一律要求 Herdr 当前 session == 绑定，载荷只是旁证 | B1 第 2 步、验收 (b) |
| WF2-021 ① 60s TTL 接手无存活证明 | 最小化 | 占位 pid 已死才接手；不做 lease/fencing（§9） | B0 第 5 条 |
| WF2-021 ② `completion_recorded` 写在 phase/holder 之前 | 采纳 | 最后写，前面各步幂等 | B0 第 5 条、验收 |
| WF2-021 ③ `watch_loop` accepted 早退 | 采纳 | cancel 未落地优先于所有早退 | B0 第 5 条 |
| WF2-024 needs_attention 的 next_step 是 plain `resume` | 采纳 | 未 claim → `collect --claim`；已 claim → `resume --retry-start` | B0 第 3 条第 3 步、验收 |
| WF2-025 `overlapping_holder` 不自动用新资格 | 部分采纳 | needs_attention 纳入显式结算、两个调用点显式改；缓存 settled_true 重观察延后（§9） | B0 第 2 条 |
| S15 `watcher.lock` O_CREAT | 采纳 | 只读变体不建文件；零写入夹具比较文件集合与哈希 | B0 第 4 条 |
| S16 re-accept 省略 `--head` 的歧义 | 不采纳 | B3 重新验收简化：省略取当前 HEAD，同 HEAD 幂等、不同直接覆盖；`--reason` 可选，无 `reaccept_requires_reason` | B3 第 3 条、验收 |
| S17 tombstone / rename 反例 | 不采纳 | `amended_paths` 只记路径，不追踪 D/R（§9） | B3 第 3 条、验收 |
| （主控自查）cancel 对无原生证据 harness 永不落地 | 采纳 | cancel_mode 下 `source == none` 且 idle ≥ `settle_confirm_s` → `idle_no_evidence` 结算 | §1「cancel 的落地」；B0 第 5 条、验收 |
| （主控自查）T1 `@@ -183` 契约句 | 采纳 | T1 表补注；T1 r2 已落地 | §3 T1 表 |

### 第 4 轮（r4 → r5）

| 项 | 处理 | 位置 |
| --- | --- | --- |
| WF2-002 历史派单与不可 claim 的重试通知 | `dispatch_wait_status` 改为顺序判定：第 1 步先用只读 `dispatch_settled`，已结算 → `settled`，不再核对控制端身份；终态但在飞（executor_active / executor_turn_active / unknown / holder_unsettled）→ action_required `status`；`wakeup_retry_exhausted` 按 `group_event_id` 的 claim 状态判定，恢复动作是 `collect --claim`（组事件都可领取）；`retry_exhausted` 记录只作显示；B1 第 7 步三分类改为「settled = reason==settled，其余 False 都是 action_required」 | §1「派工脚本」；B0 第 3 条与验收；B1 第 7 步、验收 (f) |
| WF2-004 Stop token 来源与原生活动优先级 | 明说 Stop 读 `turn.json` 是共享状态读取，不是载荷来源；idle 不再直接触发 `missing_report`，而是作为 `TurnEvidence.turn_ended` 来源交给现有 `classify_assignee`（working/blocked/unknown、`waiting_on_tool`、`turn_active` 都先否决，`settle_confirm_s` 后才 failed）；要求 `probes.json.serial_events == true`，否则只记线索；A1 加事件串行探针 (c)；夹具加「读前延迟」「原生活动为 true」负例；§1「Hook 能力」改为未核实 | §1「Hook 能力」；B2 第 2 条与验收；A1 第 2 条 (c)；§4；§5 |
| WF2-015 真值表 vs 旧结算函数 / 只读 / 原生活动 | `dispatch_settled` 纯观察，加原生证据一步（idle 但 `evidence_blocks_settled` → `executor_turn_active`）；holder false → 只读 `holder_unsettled` + next_step `--settle`；新增 `settle_dispatch` 唯一写路径（同一资格，支持 replaced / attributed / needs_attention），`settle_holder_if_idle` 改为调用它；`status --all` 默认零写入，`--settle` 后重新只读；S0 脚本同表只读 | S0；B0 第 2、4 条与验收 |
| WF2-016 journal/index 白名单含其他开发者 | `create_run` 记录 `project.trellis_developer`（按 `paths.py:100` 顺序自行解析）；清单限 `.trellis/workspace/<trellis_developer>/journal-*.md`、`index.md`；null → 不在清单；其他开发者路径 → stale | B3 第 1、3 条与验收 |
| WF2-019 待补全绑定只信 terminal / 三处分裂 | 绑定 `assignee` 加 `agent`、`name`；`paneguard.fill_session` 唯一入口：`agent_get` 的 pane/terminal/agent/name 四项全等且 Herdr 当前 session == 传入值，锁顺序 dispatch → pane → activity，三处一起写、只写 null、冲突不写；hook 与 watcher 都走它；B1 第 2 步不再以 terminal 为充分条件；A1 加接管负例 (d)；B2/B1 验收加同 terminal 换 harness、并发补全、`fill_conflict` | B2 第 1 条与验收；B1 第 2 步、验收 (b)；A1 (d)；§5 |
| WF2-021 `finalize_cancel` 资格与范围 | 抽取范围改为 `handle_cancel` 全事务（`watcher.py:741-890`），资格与现在逐项相同（working/unknown/turn_active/未静止 → 不结算）；占位 + `completion_recorded` CAS 幂等；`cancel` 只在 watcher 死时调一次，不成功返回 `cancelling` + `resume`；`resume` 先落地不成则恢复监督；`watch_loop` 在 cancel 未落地时忽略 stop；`cleanup` 返回 `cancel_pending`；验收加活执行者、并发、制品逐字段相同 | §1「cancel 的落地」；B0 第 5 条与验收；§4；§5 |
| WF2-022 linked worktree 的 `index.lock` | 用 `git -C <wt> rev-parse --path-format=absolute --git-path index.lock` 解析真实锁路径，存在 → SKIP `git_locked`，解析失败 → SKIP `lock_unknown`，删除前再查；真实 linked worktree 夹具必测 | §1「Git 命令事实」；T3 第 2 条与验收；§4 |
| WF2-023 `accept-run --reason` 不存在 | `scripts/herdr_dispatch.py` parser 纳入 B3 文件；保留必填 `--evidence`，新增可选 `--reason`；`accept_run` 签名与 `cmd_run` 路由传 `reason`；重新验收要求 `--head` + `--reason`（`reaccept_requires_reason`）；文档命令改为可执行形式；真实 parser 测试 | B3 文件、第 3 条与验收 |
| S12 `amended_paths` 累计与删除语义 | `origin_head` 固定为首次验收 head，`changed_paths` 相对它累计，`history` 链；D → tombstone，R → 旧新路径；验收含连续两次修改、删除、重命名 | B3 第 3 条与验收 |
| S13 B6 门禁与 A4 四个 worktree | B6 第 1 条显式包含 `_units_ready`（卡片与集成共用）；A4 加表：四个现时全部 keep 及原因，删除只能由用户授权的人工操作 | B6 第 1 条；A4 |
| S14 §1 归档暂存前缀 | 改写为实际 `git add` 前缀（archive 子树、源目录、子任务目录）与最终 diff 的区别；白名单核对最终 diff | §1「Trellis 归档」 |

### 第 3 轮（r3 → r4）

| 项 | 处理 | 位置 |
| --- | --- | --- |
| WF2-002 等待豁免身份源与多派单优先级 | 身份改用与唤醒投递相同的 `agent_get` + `identity_matches(state.controller, agent)`；唤醒记录用 `primary_wakeup(store, id, current_round)`；错误事件按 `store.is_claimed` 与当前轮过滤，含 `wakeup_retry_exhausted`；B1 第 7 步先收集分类，`action_required` 优先于 `waiting`，settled 派单忽略 | §1「身份核对」；B0 第 3 条；B1 第 7 步、验收 (f) |
| WF2-004 同 session 迟到 Stop | 回合 token 在 prompt 事件时捕获写入 `turn.json`（generation + session + turn_seq），Stop 携带自己回合开始时的 token，不在写入时重读；CAS 比较 turn_seq；立即 `missing_report` 需 generation/session/turn_seq 三者一致；无 token/无 session 只作线索 | B2 第 2 条与验收；B1 第 3 步 |
| WF2-007 merge-base 既误归档又漏正例 | 取消 merge-base 证据；只认 completed 或本任务 PR MERGED（`headRefName == task.branch` 且 head 落地）；无 PR 的 in_progress SKIP `no_pr`；空提交负例必测 | §0 #12；T3 第 4 条候选条件与验收 |
| WF2-008 merge commit 路径检查为空 | 单提交合法性检查：恰好一个父、`diff-tree -r --name-status -M <sha>^ <sha>` 所有路径（含 R 两侧）在 `.trellis/tasks/`、空输出不合法；归档后必须恰好新增 1 个合法提交；merge/root → 停止不推送并给恢复提示 | §1「Git 命令事实」；T3 第 4 条 |
| WF2-015 cached settled / 缺 holder 盖过活执行者 | B0 真值表：执行者观察独立于 holder；同一执行者 working → 在飞（holder true/false/missing 都一样）；查询失败 → 在飞；活动归属到更晚的非终态同会话派单；`settle_holder_if_idle` 返回 False 显式处理；S0 与 CLI 同表 | S0；B0 第 2 条与验收 |
| WF2-016 全树 `.trellis/**` 白名单过宽 | 白名单限定 spec、本任务源/归档目录、children 的 task.json、当前开发者 journal/index；`.trellis/scripts`、workflow、config、其他任务 → stale；`trellis_task` 由 create_run 记录；R 两侧核对 | §1「Trellis 收尾写入的路径」；B3 第 1、3 条与验收 |
| WF2-019 首个 prompt 后才有 session 的启动缺口 | B2：`fill_assignee_session` 同步 pane 绑定与 activity（仅 null → 非 null，核对 dispatch/round/generation/pane/terminal）；B1 第 2 步：待补全绑定按 terminal 判有效并由 hook 做一次补全；之后 session 不一致才算接管；A1 探针加补全时序 | B2 第 1 条；B1 第 2 步、验收 (b)；A1 第 2 条 |
| WF2-020 GC 内容证明删掉执行中的干净基线 worktree | GC 只清 `task/*`，且先要生命周期证据（任务 json 已归档 / 守卫 marker 不存在 / 找不到则只认旧规则），card/run 移交 B6 `run cleanup`（有派单在飞门禁） | §0 #11；T3 第 1、2 条与验收；B6 第 1 条；A4 |
| S09 B2→B1 依赖 | 顺序改为 B0 → B2 → B1；B2 交付 `paneguard.py` 作为共享读写层；§6 依赖说明 | §6 |
| S10 修改原卡产物后的验收关系 | 重新 accept-run 记 `{head, prior_head, changed_paths, reason}`；不改写 published/receipt；`_delivery_corresponds` 对重叠路径用父验收快照并记 `amended_paths` | B3 第 3 条与验收 |
| S11 A4 预期 | 现时预期改为 0 个归档候选（09-13 `no_pr`），实施时以新鲜 dry-run 为准 | §1 sanctions-radar 行；A4 |
| 主控自查：cancel 落地缺口 | B0 第 5 条 `finalize_cancel`；`watch_loop` 先处理未落地 cancel；`cleanup` 不在 cancel 未落地时写 stop；`resume` 落地而非拒绝 | §1「cancel 的落地」；B0 第 5 条与验收 |

### 第 2 轮（r2 → r3）

| 项 | 处理 | 位置 |
| --- | --- | --- |
| WF2-001 started marker 被基线祖先解除 | 新增 `base_sha`；解除条件 = `phase==archived` ∧ head≠base_sha ∧ is_landed ∧ worktree 干净；`started` 永不自动解除；`mark-merged` 同条件 | §0 #10；B1「marker 解除条件」、验收 (e) |
| WF2-002 活 watcher 不等于有效唤醒 | B0 `dispatch_wait_status`（r4 继续修订） | B0 第 3 条；B1 第 7 步 |
| WF2-003 发布校验缩水 | Worker 模式直接调用 `runtime.load_published_snapshot`；任何 DispatchError 按未交卷 block | B1 第 3 步、验收 (b) |
| WF2-004 idle 身份 | 活动协议 generation/session（r4 改为回合 token） | B2 第 2 条 |
| WF2-007 未执行 in_progress 归档 | r4 改为只认 completed / PR MERGED | T3 第 4 条 |
| WF2-008 pending 重试 | pending 存 git common dir；零候选也重试；要求 `origin/<default>` 是 HEAD 祖先且区间提交全在登记内（r4 加单提交合法性检查） | T3 第 4 条 |
| WF2-013 adopt 索引 | `adopt` 原子迁移旧 pane → 新 pane 索引再改 marker.pane；Worker 绑定冲突拒绝 | B1 表 `adopt`、验收 (k) |
| WF2-015 门禁漏 accepted+working | r4 真值表 | S0；B0 第 2 条 |
| WF2-016 祖先放宽放过业务改动 | 收尾允许清单（r4 收窄） | B3 第 3 条；B6 第 2 条 |
| WF2-017 pane 绑定竞争 | 绑定含 assignee 身份 + generation；锁下读改写；清理仅当 (dispatch, round, generation) 相同；迟到写核对 round | B2 第 1 条；B1 第 2 步 |
| WF2-018 v1.2.1 setup skill | A2 纳入三份 SKILL.md 的人工升级、exit 2 逐条处置、`.bak` 不入库 | A2 |
| S05 计数不实 | 文件清单；`rg` 排除 `docs/`、`.git/` | §1；T4 第 2 条 |
| S06 accept-run/record-unit 顺序 | task_worktree 下 accept-run 是内容验收可先做；parallel/旧 run 不变 | B3 第 3 条 |
| S07 候选 CLI 与警告落点 | §6 候选 CLI 路径；`guard_unsupported` 在 B1 | §6；B1 |
| S08 去重断言 | 只断言规则句唯一 | B4 验收 |
| 主控自查：遗留派单 | S0 清理步骤（已完成） | §1；S0 |

### 第 1 轮（r1 → r2）

| 项 | 处理 | 位置 |
| --- | --- | --- |
| WF2-001 守卫归档即失效 | marker 生命周期 `started → archived → 删除`；`mark-archive` 只更新不删 | B1 |
| WF2-002 作用域与等待豁免漏拦 | 删除「linked worktree 放行」；作用域只看 pane 绑定文件；豁免须本 pane 控制且监督有效 | B1、B0 |
| WF2-003 启动环境不可靠 | 放弃环境注入；watcher 写 pane 绑定文件；无头路径用子进程 env | §0 #7；B2 |
| WF2-004 busy-state 事件路由 | `hook --event` 先分流；Grok 也注册 UserPromptSubmit | B1 |
| WF2-005 探针顺序不闭合 | B1 只验单测；真实探针移到 A1 | B1；A1 |
| WF2-006 B5 plan 入口缺生产端 | `runcmd.py`、`protocol.py` 纳入 B5；集成测试 | B5 |
| WF2-007 兜底归档误归档 planning | planning/no_branch/branch_is_default SKIP | T3 |
| WF2-008 归档后推送风险 | 主检出、默认分支、干净、同步才归档；`push origin <default>` | T3 |
| WF2-009 patch-id 假阳性 | 全部移除；add→revert 反例必测 | T3、B0、B6 |
| WF2-010 base_branch 不是发布目标 | 集成清理目标改为 release unit 的 `target_ref` | B6 |
| WF2-011 v1.6.0 分发指针遗漏 | 全部分发源与契约测试；首行版本注释；smoke 覆盖变量 | T2 第 7 条；T4 |
| WF2-012 spec registry 误记 | `#v1.5.0` 是 `registry.spec.source`；A2 不改它 | §1；A2 |
| WF2-013 set-meta 参数错误 | 「合并前停」记在 marker | §0 #8；T2；B1 |
| WF2-014 workflow 安装命令缺模板 | 加 `--template solo-github-flow` | A2 |
| WF2-015 无派工在飞不可执行 | S0 门禁规则；B0 `status --all` | S0；B0 |
| S01 B3 快照假设 | publish→ingest→accept-card 之间不得提交（负例） | B3 |
| S02 升级流程 | `cancel` → settled → `cleanup` → 新 dispatch_id | §6 |
| S03 auto backup 推进 main | 记录 SHA；rebase 而非 reset | S0 |
| S04 GC 边界 | no-upstream 正例；锁定/当前/主检出 SKIP；`--force-*` 不变 | T3 |

## 9. 延后清单（backlog，不在当前任何卡内）

- `overlapping_holder` 对缓存 `assignee_settled=true` 的 holder 重新观察同一执行者是否仍活动（WF2-025 后半）。
- `finalize_cancel` 的 lease/fencing 与虚拟时钟夹具（WF2-021 ①）。
- `amended_paths` 的 D/R 语义、tombstone 与目标树反例（S12/S17）。
- `probes.json.serial_events` 作为判定输入（B2）。
- re-accept 强制 `--reason`（S16）。
- B1 `install` 反推 Codex `trusted_hash`。
