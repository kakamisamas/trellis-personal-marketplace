# 工作流可靠性修复执行计划

状态：实现与独立验收已完成。Cursor 第二轮审查 pass，Grok 实现，Codex 独立验收通过（普通 unittest 73 项、显式 CodeGraph CLI/MCP 集成、候选安装 smoke、语法与 diff 检查）。用户已授权提交、合并和收尾；版本交付记录见 [v1.4.0](https://github.com/kakamisamas/trellis-personal-marketplace/releases/tag/v1.4.0)。审查全文见同目录 `2026-09-05-workflow-reliability-review.md`。

## 目标与授权

修复 task worktree 中 CodeGraph 不可用、开发阶段行数漏算、首次安装污染协调 worktree、测试 CI 准备不完整，以及这些行为缺乏集成验证的问题。

用户要求的顺序：Codex 编写计划 → Cursor 只读审查 → Codex 修订必须改项并取得通过 → Grok 实现 → Codex 独立验收。

实现阶段在当前仓库工作，不创建额外工作区或 worktree，不提交、推送、发布版本或合并 PR。测试可以在临时目录创建和删除自己的 fixture worktree。验收后用户明确要求“提交合并收尾结束吧”，授权进入提交、PR、检查、合并、v1.4.0 同批发布和分支清理。

用户确认小任务可以拒绝创建 Trellis 任务，这项不属于缺陷。本轮保留任务创建询问、规划审批、TDD、OCR、收尾授权和 GC 删除规则。保留 2500 行规划预算、3500 行 CI 硬上限，报告时明确两者含义。

基线：`main` / `v1.3.0`，commit `4c190bea3b3038f03e606845294a7413e20fbc37`。本仓库没有 `.trellis/` 或 `.codegraph/`；不能把模板内容误当作本次已启动的 Trellis 任务。

## 已验证的问题

| 问题 | 证据 | 预期结果 |
| --- | --- | --- |
| 新 worktree 不继承本地索引 | 临时仓库同目录切分支可查询，新 worktree 报无 `.codegraph/`；独立 init 后 CLI、MCP 恢复 | 新 worktree 的索引初始化、查询路径和刷新契约完整 |
| 源码与符号图可能不同步 | 函数改名后源码已更新，符号列表仍旧；sync 后一致 | 使用调用关系前按需同步，查询明确指向当前 worktree |
| 行数漏算 | workflow Phase 2.2 用 `<base>...HEAD`，修改已有文件并新增文件后仍报 0 | 统计提交、暂存、未暂存和未跟踪文件构成的最终净变化 |
| bootstrap 后无法满足 clean gate | 干净基线运行 setup 后新增三个未跟踪文件，随后 Phase 1.0 要求协调目录干净 | 自动安装发生在任务 worktree，协调目录保持干净 |
| 测试 CI 无完整安装路径 | setup 只下载 GC、size gate、skill；skill 引用未分发的 `assets/ci/tests-python.yml` | 缺失测试 CI 在规划结束前被发现，模板可获得并按实际项目配置 |
| CodeGraph 缺真实回归测试 | `test_marketplace.py` 检查文案及顺序，smoke 没运行 CodeGraph | 测试实际索引、路径隔离和修改后刷新 |

既有验证：37 项 unittest 通过；使用固定 Trellis 0.6.12、marketplace v1.3.0 的安装 smoke 通过。它们不覆盖以上缺陷。

## 实现步骤

### 1. 明确安装与 worktree 顺序

修改 `workflow.md` 的 Phase 1.0、对应 breadcrumb、README 和 setup skill：

1. 先只读检查协调 worktree 状态、远端、基线和工具是否存在。干净性检查发生在任何安装写入之前。就位清单必须同时包含 `scripts/trellis_gc.py`、`scripts/trellis_codegraph.py`、`scripts/trellis_diff.py`、PR gate、setup skill 和分发的测试 CI 模板；Phase 1.0、setup skill 的触发描述与资产清单同步。旧项目即使已有原三件套，只要缺任一新资产，仍须在新任务目录运行 setup。
2. 已安装 GC 时按原规则清理；未安装时使用原有逐个核验的保守回退，或保留旧 worktree 继续。不要为了 GC 提前向协调目录安装文件。
3. 按原规则更新基线、检查冲突、创建任务 worktree。
4. 在任务 worktree 运行 release-pinned setup，安装文件属于本任务，随后随正常任务提交。已安装且一致时保持幂等。
5. 从任务 worktree 完成 CodeGraph 准备、developer 初始化、task create 和元数据记录。

现有 setup 作为显式安装工具仍可在调用者指定的仓库运行；本次修复自动工作流的调用位置，并在 README 说明首次采用应在任务目录完成。失败保留可恢复的 worktree，重试已知本任务时验证路径、分支、base 后继续；不重建任务或接受无关路径冲突。

验收：真实临时 Git 仓库模拟“已有 Trellis、缺少 marketplace tooling”，执行新顺序后 base 分支及文件保持不变且干净，task worktree 有全部工具；重复准备无重复 task、分支或不必要备份。

### 2. CodeGraph 生命周期与查询契约

新增一个小型 Python CLI `scripts/trellis_codegraph.py`，由 setup 安装，workflow 调用。建议接口：

```bash
python3 scripts/trellis_codegraph.py prepare --base-worktree <base-path> --worktree <task-path>
python3 scripts/trellis_codegraph.py sync --worktree <task-path>
```

行为：

- base 和 task 都未启用 CodeGraph：明确输出 skipped，成功退出，不索引当前项目。
- base 已启用且 task 未启用：检查 CLI，再在 task 建立独立索引；task 已有索引则验证并按需 sync。
- task 已启用但 base 未启用：尊重 task 的现有启用状态，验证它。
- 调用所有 CLI 时显式给绝对路径；读取 `status --json` 并核验 initialized、实际项目根、待更新文件和可用的完整性字段。进程退出 0 不等于索引健康。
- 对可选 JSON 字段兼容处理；支持范围根据当前 CLI 与测试固定版本记录，不能假设所有版本字段完全相同。不自动升级 CodeGraph。
- init、sync、JSON 解析、项目根不符或索引不完整均给出明确失败和修复提示，不继续报告准备成功。
- 不复制或软链接 base 索引。不改 MCP 用户配置，不给本仓库建立生产索引。
- 在 task 初始化前确认根 `.codegraph/` 是未跟踪且被 Git 忽略的工具状态；若缺规则，仅向 task worktree 的 `.gitignore` 幂等追加 `/.codegraph/`，不改 base 文件或用户全局 ignore。若索引已被 Git 跟踪，明确报告不能当作本地状态处理，不自动删用户文件。此规则属于工具状态管理，不属于为绕过预算排除业务改动；`.gitignore` 自身改动正常计数。

workflow 的所有执行方式明确：MCP `projectPath` 必须使用任务 worktree 绝对路径，CLI 必须传路径。保留 dispatch 前两行 `Active task:`、`Workdir:` 原有格式；后续指令添加 CodeGraph 路由约束。

在首次使用索引和最终质量检查前完成健康检查/必要 sync；源码改动后继续依赖符号或调用关系时先刷新。已有自动刷新已使索引干净时避免重复全量 init。此要求同步到 `[workflow-state:in_progress]`、`[workflow-state:in_progress-inline]`，并用分块测试验证；CodeGraph 为 skipped 时不强制运行 CLI。

验收：分别覆盖未启用、缺 CLI、错误 JSON、错误索引路径、init/sync 失败；真实 CodeGraph 测试查询 task 独有的符号，base 查询不得返回它；task 函数改名并同步后符号图与源码一致。至少一个测试经 MCP stdio 查询明确的 `projectPath`，不能只测 CLI。

### 3. 修复并统一行数统计

新增 `scripts/trellis_diff.py`，由 setup 分发，作为本地统计与 CI gate 的共同实现：

```bash
python3 scripts/trellis_diff.py --base <base-ref>
python3 scripts/trellis_diff.py --base <base-sha> --head <head-sha> --check
```

- 本地默认：以 `merge-base(base, HEAD)` 为起点，计算到当前工作目录的净新增与删除行数，并加上未被忽略的 untracked 文件；已暂存的新文件只能计一次。
- CI `--head`：计算 merge-base 到指定 head 的提交差异，不能把 runner 工作目录文件混入。
- 共同排除现有 lock、dist、minified 文件；二进制不计行。不得新增排除 Trellis 文档等范围来绕过预算。上一步保证未跟踪 `.codegraph/` 被 Git 忽略，行数工具仍遵循统一 Git ignore 语义，不另加只在本地生效的特殊排除。
- 正确处理有空格、tab、换行的路径和 rename；使用 Git NUL 分隔结果。统计过程不改真实 index、暂存区或工作目录。
- 保持 Git 的 numstat 文本/二进制语义；新增文件、无末尾换行、已被 .gitignore 忽略的文件都应有测试。
- 默认报告总行数、2500 规划预算、3500 硬上限；`--check` 在超过 3500 时非零退出，3500 恰好通过。无效 base/head、Git 失败须非零，不能降级为 0 行。
- Phase 2.2 改用此命令；`.github/workflows/pr-gate.yml` 和 `assets/ci/pr-gate.yml` 保持一致并调用共同实现。runner 明确准备 Python 3。

验收：提交、暂存、未暂存、新文件混合后得到明确期望值；取消修改的净变化不重复计数；本地全提交后的统计与 CI 相同（fixture 必须包含 prepare 生成的 `.codegraph/`，并验证真实索引产物不进入计数）；3500 通过、3501 拒绝；保留原有缺失 SHA 的报错语义或等价清晰提示。

### 4. 提前完成测试 CI 准备

让 setup 将 `assets/ci/tests-python.yml` 分发到目标项目可获取的、明确记录的模板位置（例如 `.trellis/templates/ci/tests-python.yml`），并纳入 dry-run、幂等和已有定制保护测试。

setup skill 和 Phase 1 规划 gate 增加实际 CI 核查；同步到 `[workflow-state:planning]`、`[workflow-state:planning-inline]` 及 Phase 1.5 完成条件表，并在 `tests/test_marketplace.py` 按状态分块断言：已有项目测试 workflow 时读取其执行命令确认覆盖测试；只有 size gate 不算。缺失时在任务 worktree 内根据实际测试框架建立测试 workflow，规划报告记录路径和真实测试命令。

模板只适用匹配的 Python/pytest 项目；不能给未知项目或纯 unittest 项目盲装 `pip install -r requirements.txt`。根据实际项目调整安装命令；未知技术栈依据现有项目测试说明配置，不凭文件扩展名猜测，也不新增通用 CI 生成器。

只读自动检查可报告候选 workflow，不能仅凭名为 tests.yml 宣称测试已覆盖。Phase 3.5 的真实 GitHub checks 核验继续保留。README Requirements 与新前置条件同步。

验收：模板确实存在于安装后的项目；已有定制内容不被覆盖；有测试 CI 的项目不新增重复 workflow；无测试 CI 在 Phase 1 提示并完成配置，而非首次在 merge 时发现；提供 pytest、unittest 或非 Python 的契约/fixture 检查，证明没有无条件套用 Python 模板。

### 5. 集成测试与交付一致性

- 保留必要的 workflow breadcrumb/分发一致性测试，以真实 subprocess 和临时 Git 仓库补齐以上行为测试。
- `scripts/smoke-install.sh` 使用工作区的 workflow 和 assets 验证候选改动，不要求修改先发布到远端。固定版本的 Trellis 仍用于获得原生骨架。
- 增加真实 CodeGraph 集成测试的显式开关或独立入口；未启用时注明 skipped。在专用 CI job 中固定安装已验证 CodeGraph 版本并强制运行，缺 CLI 必须失败而非悄悄跳过。
- PR 阶段就运行候选 workflow 的集成验证，不只在 merge 后的 push 验证已发布版本。
- setup 的新增下载目标、安装目标、README 与 tests 同步；已有 pr-gate 和 skill 的人工 diff/保守更新策略保留，并说明升级时需要共同更新 gate 与 helper。增加分发契约测试：workflow 和分发 gate 引用的每个 marketplace `scripts/*.py`（不含原生 `.trellis/scripts/`）都必须在 setup 下载与安装目标中；真实安装 fixture 检查文件可执行，并覆盖“旧三件套已装、新 helper/模板缺失”的升级情形。
- 采用同批发布策略：将 README、workflow、setup、setup skill 等产品引用统一更新为 `v1.4.0`，它是包含新 helper 的最低工具版本。源代码改动和 v1.4.0 发布必须作为同一发布单元，不单独分发新 workflow 搭配 v1.3.0 installer。实现阶段只准备候选；用户授权收尾后移除候选文案，经 PR 检查、合并后为合并提交发布 v1.4.0，并验证远端安装入口。最终交付不得声称下游已升级。
- 增加 release 契约测试：产品引用全部一致且不得低于 helper 最低版本 v1.4.0；真实候选 bundle 安装后能运行全部引用的 helper；模拟旧 installer 仍缺 helper 时就位核验应明确报版本/缺资产，不能继续到 Python 文件不存在。
- 本地候选测试通过 `TRELLIS_SETUP_ASSET_ROOT` / `TRELLIS_WORKFLOW_FILE` 使用本地 workflow 与资产；v1.3.0 仅作为测试原生 Trellis 骨架的已发布来源，标注为 fixture pin，不能再用作产品下载引用。CI 候选验证不访问尚未发布的 v1.4.0；发布传输测试与候选集成测试分开。

## 执行范围和验收命令

可改：`workflows/solo-github-flow/workflow.md`、`scripts/`、`assets/ci/`、`assets/skills/trellis-setup/SKILL.md`、`.github/workflows/`、`tests/`、`README.md`，以及本计划的执行状态。helper 运行时仅可按 §2 更新目标 task 的 `.gitignore`。保持 `specs/`、用户级配置、全局技能、现有版本 tag 不变。

按以上顺序实现；每步先建立会在旧行为上失败的测试，再修改并验收。预计实现与测试共 1800–2400 changed lines；实际接近 2500 时报告范围，3500 硬限制不变，不能靠删除验收来降行数。

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m py_compile scripts/trellis_gc.py scripts/trellis_codegraph.py scripts/trellis_diff.py
bash -n scripts/setup.sh scripts/smoke-install.sh
TRELLIS_WORKFLOW_FILE="$PWD/workflows/solo-github-flow/workflow.md" \
  TRELLIS_MARKETPLACE_SOURCE=gh:kakamisamas/trellis-personal-marketplace#v1.3.0 \
  npm exec --yes --package=@mindfoldhq/trellis@0.6.12 -- bash scripts/smoke-install.sh
```

执行者还必须运行新建的真实 CodeGraph 集成入口并给出准确命令、版本和退出码。失败测试修复后重跑对应测试及必要完整验证。

## 审查与交接

Cursor 只读审查本计划和所引用的实际代码，重点找：安装顺序仍有死锁、统计语义遗漏、MCP 未真实验证、CI 或 release 分发依赖不完整、无意改变用户保留的流程。返回 `verdict: pass | fail`、`must_fix`、`suggestions`；建议不阻塞。当前没有实现 diff，不得把本计划当作已实现代码。

Cursor fail 时由 Codex 只修必须改项并在同一个 reviewer 会话复审；取得 pass 后才给 Grok 发执行任务卡。

Grok 按计划实现，不启动新 agent，不自行提交或扩大范围。完成后覆盖写 `/tmp/herdr-dispatch-executor.md`，包含 `status`、`paths`、`commands`、`blocker`，回复仅给该路径。

Codex 读取完整回禀、检查实际 diff 并亲自运行验收；发现必须改项交回同一个 Grok 会话修复。完成后关闭本轮自己创建的 Herdr tab，汇报实现、验证与剩余限制。
