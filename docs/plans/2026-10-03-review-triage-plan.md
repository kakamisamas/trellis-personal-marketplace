# 审核分诊与返修收敛执行计划

日期：2026-10-03。状态：r2。r1 经 Codex 契约审核（`review-triage-plan-review-1003`，gpt-6-astra high，审核判定 1/3，fail：11 条阻塞、4 条建议），逐条处置见 §8。r2 相对 r1 的主要变化：分诊先判对错再分档，A/B 档重新定义并给出有序决策表；每种处置对应 ledger 状态；审核对象与判定序号由主控显式登记，模板按判定序号而不是投递 round 选尺度；修复预算改为逐提交累计；完整运行加审核 fail 后的收口路径；C1 写入范围加 `runcmd.py`。

本计划解决一件事：审核加返修的时间远超实现时间，且「修好后复审仍 fail」反复出现。改动落在两处：`herdr-dispatch` 技能（审核卡、修复卡、拆卡规则，审核请求模板，finding 处置状态，accept-card 收口，一个预算检查子命令）和本模板仓（`workflow.md` 的计划审核与主控验收段、README 一条）。不改 Trellis 原生技能。

## 0. 目标与边界

### 目标行为（主控视角）

审核 verdict=fail 回来后，主控不再把 must_fix 原样转成返工单，而是先判对错、再分档、再按有序决策表逐条处置，写出处置表。复审只因三类问题判 fail：未处置的历史阻塞 ID、回归、A 档问题。同一审核对象最多 3 次审核判定。修复累计规模超过预算即停修回计划。并发与状态机类设计先写不变量。

### 不做

- 不改审核者 harness、模型或推理强度。
- 不让脚本判断某条 finding 该不该修。分诊是主控的语义决定，脚本只记录、计数，并拒绝「A 档接受风险或延后」这一种机械可查的违规。
- 不做跨 dispatch 的审核判定自动计数。主控在卡面登记，见 §2.2。
- 不改 OCR 流程。
- 不追溯改写已有计划的处置表；状态栏计划只作为 §1 证据引用。
- 不动 `turn_guard.py`、`watcher.py`、状态栏代码。

### 已决定的事（2026-10-03 用户拍板）

| # | 决定 | 值 |
| --- | --- | --- |
| 1 | 同一审核对象的审核判定总数上限 | 3 次，跨 dispatch 累计；对象定义见 §2.2 |
| 2 | 修复预算 | 全部修复提交累计新增行 ÷ 被审原始 diff 新增行，含测试，阈值 30%；超过即回到计划 |
| 3 | 「接受风险」作为 must_fix 的正式处置 | 允许。与「拒绝」分开：拒绝 = 审核者判断错；接受风险 = 判断对但不值得修 |

### 已按默认值决定的事（用户可推翻）

| # | 决定 | 默认值 |
| --- | --- | --- |
| 4 | 修复预算保底 | 允许值取 30% 与 60 行的较大者。被审 diff 新增不足 200 行时实际按 60 行算 |
| 5 | A/B 档定义 | 见 §2.1；拿不准按 A 档 |
| 6 | 封顶与回到设计的决策人 | 主控。同一对象第二次需要回到设计时交用户 |
| 7 | 不变量条数 | 目标 3 到 5 条，不是上限 |
| 8 | 预算检查入口 | `herdr_dispatch.py fix-budget`，输出 JSON，只读 |

## 1. 事实基线

统计截止 2026-10-03 00:50（+0800），来源 `~/.local/state/herdr-dispatch/*/rounds/*/result.json` 与技能仓 `~/.skills-manager/skills`。「判定」只算带 `verdict` 的交卷；`status=failed` 且无 verdict 的交付单独注明。标「推断」的是机制假说，不是已测事实。

### 1.1 状态栏 B 轨道（`status-bar-*-1002`）

| 阶段 | 时间 | 提交 | 新增行 |
| --- | --- | --- | --- |
| 实现 B1→B3→B4→D1，`cbbe648..509d170` | 13:54–16:27 | 14 | 5216 |
| 审核与返修 R1→R9，`509d170..24a8749` | 16:28–次日 00:39 | 端点 35，含 B2 合并（+1578/−39）；主线非合并提交 31 | 端点 5725；主线非合并提交累计 4756 |

纯修复累计 4756 ÷ 5216 ≈ 0.91。三个审核 dispatch 共 9 次判定，前 8 次 fail，第 9 次（`status-bar-Btrack-review3-1002`）00:39 发布 pass，技能仓 main 已快进到 `24a8749`。主控处置：计划 §8 共 45 处「采纳」，must_fix 零拒绝。

根因归属（从各轮 result.json 核对）：

- R07、R09–R12、R16–R24 由 R01 修法新增的身份层（terminal 守卫 → epoch → sidecar → instance → 空业务判定与 clear 缓存）及其后续补丁触发。
- R08 是 R02 预算修法（deferred）的回归，与 R01 无关。
- R13、R14 属 B2 分类器。

影响不是一律「显示错一拍」。R07（首次绑定后合法写入被永久拒绝）、R11（一次锁竞争永久污染 epoch）、R12（删除后新占用者无法登记）、R21（正常 AskUserQuestion 被隐藏）都是持续失效、不能自行恢复，按 §2.1 属 A 档。它们都不损坏派工业务状态，影响限于状态栏展示与通知。

### 1.2 其他多轮审核

| 审核 | 判定 | must_fix 走势 | 备注 |
| --- | --- | --- | --- |
| `workflow-v2-plan-review-1001` | r1 交付 failed 无 verdict；r2–r4 三次 fail；r5 无结果 | （15）→ 11 → 8 → 8 | WF2-002/004/015 连续在榜，描述每轮变化 |
| `enforcement-plan-review-0926` 系列 | 首个无 verdict；r2–r5 四个 dispatch 各一次 fail | 9 → 8 → 3 → 1 | |
| `trk-progress-plan-review-0927` | 4 次 fail | 6 → 3 → 2 → 3 | CR-07 第 3 轮修法删掉了合法功能，第 4 轮因此再被打 |
| `enf-zh-plan-review-20260927` | 3 次 fail 后 pass | 6 → 3 → 1 → 0 | ENF-CR-002 连续 3 轮 |
| `tabclose-code-0926` | 3 次 fail 后 pass | 6 → 3 → 1 → 0 | |
| `radar-ui-plan-20260906` | 3 次 fail 后 pass | 7 → 5 → 1 → 0 | |

推断：复审轮频繁出现新 ID，一个原因是复审单主动开放新范围（状态栏 round 2 请求列了 6 个「是否引入新问题」的方向），另一个是修法新增机制带来新边界。已有规则「同一 must_fix 连续两轮未解决交用户」从未触发，推断原因是同一根因每轮以新 ID 或新描述出现。验证指标见 §4。

### 1.3 用 r2 规则回放状态栏案例（推断，条件性估计）

- 判定 1：R01 属 A 档（新占用者持续看到旧派单），采纳。R02–R06 采纳。
- 判定 2：R05 未修完整，继续修。R07 是 A 档，且由 R01 修法新增的 terminal 守卫触发，按决策表第 2 行回到设计：把占用者隔离改成一条规则「渲染进程发现 terminal 变化就清空整段，并拒绝旧 round 的一切写入」，开新卡。R08 是 R02 修法的回归，采纳。
- 后续：新卡是新审核对象 `status-bar-Btrack/redesign-1`，重新从 0/3 计。原对象的 R09–R24 链条不再产生。

条件：新卡的单规则设计能一次通过。若不能，同一对象第二次需要回到设计即交用户。节省的时间无法从现有数据测出，不给数字。

## 2. 规则

### 2.1 主控分诊（写入 `references/repair-card.md` 新节「分诊」）

审核 `verdict=fail`，或主控验收不通过后，派返工前对每条 must_fix 依次走三步，结果写进计划处置表。有 run 时同时 `run findings dispose`。

**第 1 步：判对错。** 结论不成立（引用的代码不存在、复现不了、误读契约）→ 拒绝，附可核实证据。成立才进第 2 步。

**第 2 步：分档。**

- A 档：损坏数据、工作树或合并结果；崩溃；拦坏 Stop hook；未通过已声明的验收项；或本功能的核心承诺持续失效、不能自行恢复（例：合法写入被永久拒绝，正常提问永久不显示）。
- B 档：偏差在下一拍或下一个事件自行纠正；性能偏差仍在预算内；或触发条件在本项目声明的拓扑下不出现。
- 拿不准按 A 档。

**第 3 步：有序决策表，从上往下取第一条命中的。**

| # | 条件 | 处置 |
| --- | --- | --- |
| 1 | A 档，且由本审核对象修复期间新增的机制（状态字段、锁、身份、世代、守卫、清理分支）触发 | 回到设计 |
| 2 | A 档，修法需要新增上述机制，且同一根因已经回过一次设计 | 交用户 |
| 3 | A 档 | 采纳 |
| 4 | B 档，触发条件在本拓扑不出现 | 接受风险 |
| 5 | B 档，会出现但能自行纠正 | 接受风险或延后，写明理由 |
| 6 | B 档，其余 | 采纳；修法需要新增机制时改为延后 |

每条处置都写一句理由，「采纳」也不例外。A 档不得接受风险或延后，脚本拒绝这两种组合。

**处置与 ledger 状态：**

| 处置 | ledger 状态 | 进返工单 | 阻塞 accept-card |
| --- | --- | --- | --- |
| 采纳 | `accepted`，修好并验证后 `fixed` | 是 | 是，直到 `fixed` |
| 拒绝 | `rejected` | 否 | 否 |
| 接受风险 | `risk_accepted`（新增） | 否 | 否 |
| 延后 | `deferred`（新增），同时写入计划延后清单 | 否 | 否 |
| 回到设计 | `redesign`（新增） | 否 | 是。本卡不再派修复，由新卡取代 |

接受风险与延后必须带 `--class B`。接受风险另记场景、拓扑假设、影响、被审版本（head 或计划文件 sha256）。后续出现新证据，表明影响升级到 A 档或拓扑假设不再成立，审核者可以引用该风险 ID 重新提出，主控重新分诊。这不算重复报告。

主控自己的指令错误照旧单独登记，不算执行者问题。

### 2.2 审核对象、判定计数、封顶、回到设计、修复预算（扩写 `references/repair-card.md`「预算」节）

**审核对象**：主控在派审核时登记稳定的 `object_id`。计划审核取计划文件的仓库相对路径。完整运行的代码审核取 `<run_id>/<card_id>`。普通派工的代码审核由主控起一个短名（如 `status-bar-Btrack`），整个审核周期沿用。换 dispatch id、换 base、改措辞或换卡片都不产生新对象。只有回到设计产生新对象（见下）。

**判定计数**：只计带 verdict 的交卷。`failed`、`blocked` 且无 verdict 的交付不计。主控把当前序号写进审核任务的 `card.review.verdict_seq`，并在计划处置表标题写「审核判定 n/3」。

**封顶**：同一对象最多 3 次判定。第 3 次仍 fail 时：

- 剩余项全部已处置为 `fixed`、`rejected`、`risk_accepted` 或 `deferred`，且没有 A 档项未修：主控收口。对修好的项用测试和自查验证，不派第 4 次审核；完整运行用 §2.5 的收口路径。
- 仍有 A 档未修：回到设计。若该对象已经回过一次设计，交用户。

**回到设计**：

1. 冻结当前修复分支在被审 HEAD，不再按 finding 开返工单。相关 finding 置 `redesign`。
2. 主控改计划里该机制所在的设计段。目标是让机制变简单（删机制、缩场景、改约束），不是再加一层守卫。改动涉及用户已拍板的决定时先问用户。
3. 按新设计开一张新卡，只实现这个机制。它的审核对象是 `<原 object_id>/redesign-1`，判定从 0/3 计。原对象未解决的 finding 原样带入新卡卡面。
4. 旧分支上该机制的补丁由新卡执行者按新设计取舍。
5. 计划文件作为契约审核对象的计数不因局部重设计而重置；局部重设计不默认重跑契约审核，由新卡的代码审核覆盖。
6. 同一对象最多回一次设计。第二次需要时交用户。

**修复预算**（只适用于代码审核对象）：

```bash
herdr_dispatch.py fix-budget --repo <git 仓> --base <base_sha> --reviewed <第 1 次判定的被审 head> [--head HEAD] [--limit 0.30] [--floor 60]
```

- `original_added`：`git diff --numstat base..reviewed` 的新增行合计。
- `fix_added`：`git log --first-parent --no-merges --numstat reviewed..head` 每个提交新增行之和。反复改同一段、先加后删、回退都逐次计入。合并进来的其他分支不计。
- `allowed = max(limit × original_added, floor)`；`over_limit = fix_added > allowed`，等号不算超。
- `original_added = 0` 时 `ratio` 为 `null`，`allowed = floor`。
- ref 不存在，或 `reviewed` 不是 `head` 的祖先（例如历史被改写）：退出码 2，输出 `{"ok": false, "error": ...}`，不按 0 计。
- 输出字段：`ok`、`original_added`、`fix_added`、`ratio`、`allowed`、`over_limit`、`commits`。
- 超限即回到设计。

现有规则「同一 must_fix 连续两轮未解决交用户」保留，「同一」按根因判断，不按 ID 或措辞。

### 2.3 审核尺度（写入 `references/review-card.md`，并改 `scripts/templates.py` 的「尺度」段）

主控派审核时在任务的 `card.review` 填：

```json
{"object_id": "...", "verdict_seq": 2, "prior_ids": ["..."], "accepted_risks": [{"id": "...", "scenario": "..."}]}
```

- `verdict_seq` 缺省或为 1：首次判定。审核者按「机制 × 场景」穷尽列出，一次报完，报告必须含「已核对无问题」段。
- `verdict_seq ≥ 2`：复审。只有三类问题判 fail：
  1. `prior_ids` 中尚未处置的历史阻塞项未修好。这里包括此前任何一次判定的阻塞项，不只第 1 次。
  2. 回归：修复改坏了原来能工作的行为，或修复路径本身违反已声明的契约或验收项。
  3. 任何 A 档问题，包括此前漏报的。
- 其余一律进建议项，包括不违反契约的 B 档新边界和修复所加机制的扩展场景。A 档与契约违反不因属于新机制而降级。
- `accepted_risks` 中的场景不得再列为阻塞，除非有新证据表明影响升级到 A 档或拓扑假设失效，此时引用风险 ID 并给证据。
- 主控写复审的「本轮目标」时，不列「请检查以下 N 个方向」。

模板只按 `card.review.verdict_seq` 选尺度，不看投递 `round`。复审尺度段渲染 `prior_ids` 和 `accepted_risks` 列表。审核卡背景必须写本项目拓扑。

### 2.4 不变量（写入 `references/card-planning.md` 新节「不变量」）

- 涉及多写入者、锁、身份、世代、CAS、跨进程状态文件的卡，计划写不变量，目标 3 到 5 条，形如「任何时刻……」，每条对应一个测试名。执行者交卷附不变量与测试的映射。
- 不变量是验证契约的工具，不替代契约。审核者的反例违反已声明需求、验收项或 A 档底线时，即使没有对应不变量也是阻塞，修复时补不变量和测试。只有提出新需求的反例才进建议项。
- 设计先减少需要的不变量条数，再谈守卫。

### 2.5 完整运行的审核 fail 收口（写入 `references/repair-card.md`，改 `runcmd.py`）

现状：`accept_card` 读到最近一次审核 `verdict=fail` 就报 `review_failed`，处置完所有 finding 也无法验收。新增：

```bash
herdr_dispatch.py run accept-card <run_id> <card> --review-override --evidence "<验证了什么>"
```

条件全部满足才放行：

1. 最近一次审核判定为 fail。
2. 该次审核的每条 must_fix 在 ledger 中为 `fixed`、`rejected`、`risk_accepted` 或 `deferred`。没有 `open`、`accepted`、`redesign`。
3. `risk_accepted`、`deferred` 均为 B 档（dispose 已强制）。
4. `--evidence` 非空。卡片 head 与被审 head 不同时，evidence 必须说明修复后跑了哪些测试。

放行时在卡片记录 `review_override`：审核 dispatch、round、被审 head、当前 head、evidence、时间。审核的原始 published 结果不改。`open_findings` 检查的阻塞状态加入 `redesign`。

### 2.6 模板仓 `workflow.md`（英文）

插入四处，固定原文：

- 两个 planning breadcrumb 的「Optional contract review」句后各加一句：`The controller triages each contract-review finding (reject, adopt, accept risk, defer, or back to design, one-line reason each); a plan gets at most 3 review verdicts.`
- 主控验收段（「The controller then verifies the handoff…」后）加：`Before dispatching rework, the controller triages every must_fix per herdr-dispatch references/repair-card.md: reject, adopt, accept risk, defer, or back to design. A reviewed object gets at most 3 review verdicts. Cumulative fix additions above the fix budget send the card back to design instead of another rework round.`
- Review scope 段末加：`A re-review fails only on undisposed earlier blocking IDs, regressions (including a fix path that violates the declared contract), or A-class problems; other new edges are suggestions.`

## 3. 工作单元

### C1 技能规则与脚本（`~/.skills-manager/skills`，一张卡，一个 PR）

写入范围：`herdr-dispatch/references/{repair-card,review-card,card-planning}.md`、`herdr-dispatch/SKILL.md`、`herdr-dispatch/scripts/{findings,templates,herdr_dispatch,runcmd}.py`、`herdr-dispatch/tests/`。

内容：

- §2.1–§2.5 的规则文字。`SKILL.md` 只在「调度要点」加一条指向分诊与封顶，不超过 3 行；细节放 references。
- `findings.py`：`FINDING_STATUSES` 与 `dispose_finding` 加 `risk_accepted`、`deferred`、`redesign`。`dispose` 加 `--class A|B`，`risk_accepted`、`deferred` 必须带 `--class B`，带 A 或缺省时报 `invalid_finding`。
- `runcmd.py`：
  - `_repair_must_fix` 以 ledger 为权威：卡片 `open_findings` 中同 ID 在 ledger 已是非 open 状态的项排除；ledger 里没有的卡片项保留。
  - `card_meta` 渲染同样排除已处置项。
  - `accept_card` 实现 §2.5 的 `--review-override`。
  - 阻塞状态集合加入 `redesign`。
- `herdr_dispatch.py`：注册 `fix-budget`（§2.2），注册 `run accept-card --review-override --evidence`。
- `templates.py`：尺度段按 `card.review.verdict_seq` 分首次与复审两套，复审渲染 `prior_ids`、`accepted_risks`。

验收（cwd 为 `~/.skills-manager/skills/herdr-dispatch`）：

- `python3 tests/run_tests.py` 全绿。新增测试至少覆盖：
  - dispose：`risk_accepted`、`deferred` 缺 `--class` 或带 A 被拒；`redesign` 可写。
  - 真实链路：完整运行里审核 fail 被 accept（`on_dispatch_decision` 同时写 ledger 和卡片 `open_findings`），再 dispose 为 `risk_accepted`。之后下一次修复派卡的 `must_fix` 和卡面渲染都不含该 ID。同 ID 同时存在于两处存储。
  - `--review-override` 三种情况：全部已允许处置，放行并记录；仍有 `accepted` 或 `redesign`，拒绝；head 变化，记录两个 head。
  - `fix-budget` 夹具仓库：同段反复修改按逐次累计；回退提交计入；合并分支不计；`original_added = 0` 时 `ratio` 为 null 且 `allowed = 60`；等于允许值不超限；无效 ref 与非祖先退出码 2。
  - 模板：`verdict_seq=2` 且 `round=1` 渲染复审尺度；无 `card.review` 且 `round=2` 渲染首次尺度；复审尺度含 `prior_ids` 与 `accepted_risks`。
- 真机核对：`python3 scripts/herdr_dispatch.py fix-budget --repo ~/.skills-manager/skills --base cbbe648 --reviewed 509d170 --head 24a8749` 输出 `original_added` 5216、`fix_added` 4756、`over_limit` true。

### C2 模板仓（本仓，一张卡，一个 PR）

写入范围：`workflows/solo-github-flow/workflow.md`、`README.md`、本计划 §8。

内容：§2.6 四处原文。README 在 OCR 那条后加：`Review findings are triaged by the controller (reject, adopt, accept risk, defer, back to design); each reviewed object gets at most 3 review verdicts, and fixes have a cumulative size budget.`

验收（cwd 为本仓根目录）：`python3 -m pytest tests` 全绿；`grep -c "accept risk" workflows/solo-github-flow/workflow.md` 为 3；`grep -c "3 review verdicts" workflows/solo-github-flow/workflow.md` 为 3；`grep -c "A re-review fails only on" workflows/solo-github-flow/workflow.md` 为 1；README 新条目存在。

顺序：C2 不消费 C1 的代码，可以并行准备。C2 的文字引用 C1 新增的 references 内容，所以在 C1 合并后再合并。

## 4. 验证矩阵

| 规则 | 验证方式 |
| --- | --- |
| 分诊 | 下一次 fail 审核的处置表每行都有「对错 → 档位 → 决策表行号 → 处置 → 理由」；`run findings dispose` 支持新状态与 `--class` |
| 判定计数与封顶 | 下一个审核任务卡面有 `card.review`；计划处置表标题有「审核判定 n/3」 |
| 修复预算 | `fix-budget` 真机核对数值与 §3 一致 |
| 复审尺度 | 复审 `request.md` 同时列出三类 fail 来源（未处置历史 ID、回归、A 档），并把其余归为建议；首次判定的 `request.md` 含「穷尽」与「已核对无问题」要求 |
| 收口 | 完整运行中审核 fail 且全部处置后，`--review-override` 可验收 |
| 不变量 | 下一张并发类卡的计划含不变量表 |
| 机制假说 | 后续 3 个多轮审核记录：复审新 ID 中属于「修复新增机制的 B 档边界」的比例；该比例应降为 0 个阻塞 |

## 5. 风险与回退

- 接受风险被滥用：只允许 B 档，脚本强制；必须写场景与拓扑假设；证据升级时可以重新提出。
- A 档定义偏宽，导致必修项变多：拿不准按 A 档是有意的取舍。控制返修长度的是「新增机制触发的 A 档回到设计」和封顶，不是把问题降档。
- `--review-override` 被用来跳过真问题：放行条件要求所有 must_fix 都有 ledger 处置且无 A 档风险接受；记录不可省。
- 回退：技能仓回退 C1 的 PR，涉及 references、`SKILL.md`、`templates.py`、`findings.py`、`runcmd.py`、`herdr_dispatch.py`；模板仓回退 C2 的 PR，涉及 `workflow.md`、README。ledger 中已写入的 `risk_accepted`、`deferred`、`redesign` 状态在回退后作为未知状态保留，`open_findings_for_card` 的白名单不会把它们当 open。

## 6. 执行流水线（主控 = 本会话）

1. 用户已确认方向；r2 按审核修订。本计划的契约审核判定已用 1/3，r2 不再送审，直接实现，由 C1 的代码审核覆盖。
2. C1：在 `~/.skills-manager/skills` 开分支，派 Codex `gpt-6.1-sol`（推理强度 high）实现。之后按本计划 §2.3 尺度做代码审核，上限 3 次。
3. C2：本仓开分支改四处，PR，在 C1 合并后合并。
4. 两处合并后，把本计划 §8 补执行记录。

## 7. 延后清单

- 审核判定的跨 dispatch 自动计数，需要稳定识别同一审核对象。先靠主控在 `card.review` 手记。
- 「同根因」识别辅助，例如按 finding 文本相似度提示。先靠主控判断。

## 8. 审核回应

### 契约审核（`review-triage-plan-review-1003` round 1，gpt-6-astra high，对 r1）

审核判定 1/3：fail，11 条阻塞、4 条建议。交付被发布器标成 `status=failed`，原因是审核期间状态栏计划文件被另一个主控会话修改（mtime 00:40:31，审核 00:40:10 开始）。审核者没有写项目文件，报告内容完整。按协议 `reject` 结束该轮，按报告修订。

| ID | 对错 | 档 | 处置 | 落地 |
| --- | --- | --- | --- | --- |
| TRP-001 事实基线合取条件与 R08 归因错 | 成立 | 契约 | 采纳，缩范围：只改错误结论、标推断，不为 24 条逐条写触发条件 | §1.1、§1.3 |
| TRP-002 缺拒绝、决策表冲突、ledger 未映射 | 成立 | 契约 | 采纳 | §2.1 三步与有序表、ledger 表 |
| TRP-003 封顶可放过 A 档、风险豁免永久 | 成立 | 契约 | 采纳 | §2.1 风险重提、§2.2 封顶分支 |
| TRP-004 复审「新机制边界」与回归冲突 | 成立 | 契约 | 采纳：边界改按 A/B 档划分 | §2.3 |
| TRP-005 不变量当唯一阻塞依据 | 成立 | 契约 | 采纳 | §2.4 |
| TRP-006 投递 round 不等于判定序号 | 成立 | 契约 | 采纳：`card.review.verdict_seq` | §2.2、§2.3、C1 |
| TRP-007 回到设计可无限重置 | 成立 | 契约 | 采纳：对象定义、最多回一次设计 | §2.2 |
| TRP-008 端点 diff 不是累计 | 成立 | 契约 | 采纳：逐提交累计、零分母、无效范围、等号 | §2.2、C1 |
| TRP-009 风险项经 `runcmd` 进返工单 | 成立 | 契约 | 采纳：`runcmd.py` 入 C1 | C1 |
| TRP-010 完整运行无收口路径 | 成立 | 契约 | 采纳：`--review-override` | §2.5、C1 |
| TRP-011 矩阵与规则矛盾 | 成立 | 契约 | 采纳 | §4 |
| TRP-S01–S04 | 成立 | 建议 | 采纳 | §1、§3、§5 |

11 条全部是计划内部矛盾或漏掉的代码调用方，不是罕见时序边界，所以没有接受风险或延后的。
