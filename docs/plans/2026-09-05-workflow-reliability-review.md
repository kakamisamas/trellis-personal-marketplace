# Cursor 计划审查记录

## 第一轮

以下为 Cursor 回复正文，由协调者读取并保存。

审查完成。结论是 fail，四项必须改，主要是新 helper 的分发链没闭合、面包屑没同步、以及本地行数会把 .codegraph/
  之类工具产物算进预算。

  先回答你点名的五项核查：bootstrap 死锁已被计划解掉（干净性检查前置、安装移到任务 worktree、GC
  未装时走原保守回退，不再为拿 GC 而污染协调目录）；本地 diff 的 staged/unstaged/untracked 语义和 CI 对齐写得完整，A...B
  只算提交的老 bug 有正确替代；CodeGraph 侧确实要求了 status --json 字段核验、绝对路径、以及至少一个 MCP stdio + 显式
  projectPath 的测试，方向对，但没给包名/版本/入口；测试 CI 模板的分发和规划期落实写清楚了，缺的是面包屑与 1.5
  完成表的同步；release 与本地候选测试的衔接是缺口最大的一处。

  verdict

  fail

  must_fix

  1. Phase 1.0 的工具就位检查和 setup skill 的资产清单没有纳入新 helper

  • 位置：workflows/solo-github-flow/workflow.md 348-353 行（只枚举
    scripts/trellis_gc.py、.github/workflows/pr-gate.yml、trellis-setup skill
    三项）；assets/skills/trellis-setup/SKILL.md 9-14 行；计划 §1 与 §5。
  • 问题：计划 §5 只说「setup 的新增下载目标、安装目标、README 与 tests 同步」，没要求同步这个「缺哪个才重跑
    setup」的判定清单。已经装过旧三件套的项目，检查恒为通过，setup 永远不会再跑，trellis_diff.py / trellis_codegraph.py
    永远不会出现，而 Phase 2.2 和 Phase 1.0 已经改成调用它们，直接报文件不存在。
  • 应变成什么样：计划显式要求把两个 helper（以及测试 CI 模板的落地路径）加进 Phase 1.0 第 1 步的核验清单和 SKILL.md
    的清单，并新增一条测试，断言 workflow.md 与 assets/ci/pr-gate.yml 中调用的每个 scripts/*.py 路径都在 setup.sh 的
    assets 数组和安装目标里。

  2. 本轮不 bump release ref，但 workflow 已开始调用只有未来 tag 才分发的 helper

  • 位置：计划 §5「保持现有 release 引用不变，本轮不指向尚未发布的 tag」；scripts/setup.sh:4 的
    RELEASE_REF="v1.3.0"；workflow.md:352 的 pinned curl URL。
  • 问题：workflow.md 让人从 v1.3.0/scripts/setup.sh 引导，而 v1.3.0 的 setup.sh 里没有这两个新脚本。合并到 main
    后，任何走 #main（smoke-install.sh:5 的默认 SOURCE 就是无 ref）的消费者，以及本仓库自己的下一个任务，都会在 Phase
    1.0/2.2 撞上缺文件。test_release_references_stay_aligned 只比对 URL 与 ref 是否一致，捕捉不到这个断裂。
  • 应变成什么样：计划二选一并写死——要么 Phase 2.2 保留现有内联 git 命令作为 helper 缺失时的显式降级路径（helper
    存在才用），要么声明 workflow 对 helper 的调用与 tag bump 同批发布，并加一条在 RELEASE_REF 早于 helper
    时就失败的测试。当前「记录发布前需要 bump 的文件」只是报告约束，挡不住 head 自相矛盾。

  3. 新增的必做步骤没有同步到 [workflow-state:*] 面包屑和 1.5 完成表

  • 位置：计划 §2（「首次使用索引和最终质量检查前完成健康检查/必要 sync」）和 §4（「Phase 1 规划 gate 增加实际 CI
    核查」）；对照 workflow.md 113-119 行的 breadcrumb 契约与 workflow.md:574-582 的 1.5 完成条件表。
  • 问题：workflow.md 自己写明「Every workflow-walkthrough step marked [required · once] must have a matching enforcement
    line in its phase's [workflow-state:*] block」，并点名 Phase 1 规划 gate 漏跳就是从这个缺口来的。计划只在 §1
    提到「对应 breadcrumb」，§2 和 §4 新增的强制行为完全没提，落地后会被逐轮静默跳过。
  • 应变成什么样：计划要求把 CI 核查写进 [workflow-state:planning] 和 [workflow-state:planning-inline]，把 CodeGraph
    健康检查/改动后刷新写进 [workflow-state:in_progress] 和 [workflow-state:in_progress-inline]，在 1.5 表格加一行测试 CI
    条件，并在 tests/test_marketplace.py 现有的分块断言里补上对应短语。

  4. 本地统计会把 worktree 里的工具产物计入预算，且与 CI 不一致

  • 位置：计划 §3「加上未被忽略的 untracked 文件」；对照 workflow.md:387-393，Phase 1.0 自己会在任务 worktree 里
    codegraph init 生成未跟踪的 .codegraph/，而本仓库 .gitignore 和 workflow 都没有忽略它。
  • 问题：.codegraph/ 永远不会进提交，却会被本地计数枚举，直接违背计划自己的验收「本地全提交后的统计与 CI
    相同」，并可能把任务假性顶到 2500 预算之上。计划同时禁止「新增排除」，执行者会在两条要求之间卡住。
  • 应变成什么样：计划明确区分「用户改动」与「永不提交的工具状态」——由 trellis_codegraph.py prepare
    保证索引目录被忽略，或由 trellis_diff.py 在 untracked
    扫描中跳过它，并写清这不属于被禁止的「绕过预算的排除」；验收里加一条：任务 worktree 存在 .codegraph/
    时，本地总数与同一批提交的 CI 总数相等。

  suggestions

  • §2 直接写死包名与版本：npm i -g @colbymchenry/codegraph@<pinned>，MCP 入口是 codegraph serve
    --mcp（stdio），省掉执行者的探索。
  • 默认 MCP 只暴露 codegraph_explore，其余工具需 CODEGRAPH_MCP_TOOLS 打开；MCP 测试要么用 explore，要么显式设这个变量。
  • 集成测试设 CODEGRAPH_NO_DAEMON=1：MCP server 有文件事件自动 sync 和 connect-time catch-up，「sync
    前索引仍旧」这类断言容易 flaky。
  • 明确 setup.sh 退出码 2（已有 pr-gate/skill 保留待人工 diff）是非阻塞的部分成功，否则定制过 gate
    的项目每个任务都会卡在引导步。
  • 计划里点名 Phase 3.4 的 dirty 路径分类要把引导安装的文件视为本任务文件，避免提交步骤停在「未知文件」。
  • .github/workflows/ci.yml:21 的 py_compile 要加上两个新脚本；tests/test_marketplace.py:128-148 对 codegraph init
    "<absolute-worktree-path>" 的字面断言应改指新 helper 而不是删掉。
  • 测试 CI 模板落地位置建议放在 .trellis/ 之外（.trellis/ 由 Trellis 拥有、trellis update 会管），或在计划里说明为什么
    .trellis/templates/ 安全。
  • scripts/smoke-install.sh:38 的 cmp（已发布 workflow 与工作区一致性）在改成候选模式后，建议在 push job
    里保留，否则没有任何东西再验证发布传输。
  • 提一句预算影响：首次采用时引导安装的工具（GC + gate + skill 约 300 行）现在会落在该任务自己的 2500 预算内。

## 第一轮处理

四项 must_fix 已写回计划：完整资产就位清单与升级验证；v1.4.0 同批发布策略和候选/已发布 fixture 区分；状态 breadcrumb 与完成条件同步；CodeGraph 工具状态 ignore 与本地/CI 一致性测试。等待复审，尚未允许执行。

## 第二轮

以下为完整第二轮回复及读取计划的上下文，结论 pass，must_fix 为空。

[Pasted text #2 +14 lines]


    Read docs/plans/2026-09-05-workflow-reliability.md

  复审完成，四项都已实质解决，verdict 是 pass。

  同批发布方案符合"准备但不发布"的边界：产品引用统一到 v1.4.0 是纯文本改动，setup.sh 的 RAW_BASE 虽然会指向尚未存在的
  tag，但所有验证路径都走 TRELLIS_SETUP_ASSET_ROOT（tests/test_setup.py:45 和 smoke-install.sh:43 都无条件设它），CI
  里没有任何一处会去 curl v1.4.0；.github/workflows/ci.yml 的两个 job 也不会。既有 tag 不动，不提交不打 tag，禁止"新
  workflow 配旧 installer"的组合也写死了。剩余的唯一残留（合并到 main 后、打 tag 前 curl 会 404）被"同一发布单元 +
  文档标注候选未发布 + 旧 installer 缺 helper 时明确报版本/缺资产"三条兜住，在不发布的授权下这是能做到的最好状态。

  verdict

  pass

  must_fix

  []

  四项逐条确认：

  1. §1 第 1 条把 trellis_gc.py / trellis_codegraph.py / trellis_diff.py / PR gate / skill / 测试 CI
     模板全部列进就位清单，并明确「旧项目即使已有原三件套，只要缺任一新资产，仍须运行 setup」；§5
     的分发契约测试（排除原生 .trellis/scripts/ 这一限定是对的）加上「旧三件套已装、新 helper 缺失」升级
     fixture，堵住了清单恒真导致 setup 永不重跑的路径。
  2. §5 的同批发布三条 + release 契约测试成立，见上。
  3. §2 点名 in_progress / in_progress-inline，§4 点名 planning / planning-inline 加 1.5
     完成表，都要求分块断言；「CodeGraph 为 skipped 时不强制运行 CLI」这条补得好，避免面包屑逼非 CodeGraph 项目跑 CLI。
  4. §2 改成让忽略规则真实存在（仅向 task worktree 的 .gitignore 幂等追加
     /.codegraph/，已被跟踪时只报告不删），比在计数工具里开特例更干净：本地和 CI 共用同一套 Git ignore 语义，.gitignore
     自身改动照常计入预算，§3 验收还要求 fixture 含真实 prepare 产物并核对本地/CI 一致。执行范围里那句「helper
     运行时仅可按 §2 更新目标 task 的 .gitignore」把写入边界也钉住了。

  suggestions

  • §1 建议直接点名 [workflow-state:no_task]：workflow.md:190 那句「before creating a task worktree, bootstrap missing
    solo-github-flow tooling」正好是被本次反转的顺序，只说「对应 breadcrumb」容易漏。
  • §5 的分发契约测试可加反向断言：setup 的每个安装目标都必须出现在 Phase 1.0 就位清单和 SKILL.md
    清单里，防止清单将来再次落后于资产。
  • 明确两个 helper 走 install_replaceable（与 trellis_gc.py 一致）：install_conservative 不做 chmod +x，「真实安装
    fixture 检查文件可执行」会在那条路径上失败。
  • 验收命令块里的 #v1.3.0 是 marketplace fixture pin，原生骨架其实来自 @mindfoldhq/trellis@0.6.12，§5
    的措辞可以分清这两者。
  • bump 时需要一并改的版本字面量：tests/test_marketplace.py:153、:252、:282，以及 README 里 spec registry 的几处
    #v1.3.0。

## 交接状态

Cursor 计划审查通过，允许 Grok 按修订后的计划实现。建议项不是额外任务，可在实现相应步骤时处理相关细节。
