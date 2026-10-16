# PCH 重构：交给执行 agent 的目标模式 Prompt

> 公开脱敏的历史计划/执行入口；动态事实仍以本机唯一 STATE 为准。2026-10-16 所有者后续指令要求每个实际执行轮核查全部待提交内容并正常交付安全候选到 main，取代下文任何延至 G 阶段的交付安排；最终 G 验收仍保留。路径占位值需由执行工作区提供。本文件存在不自动启动任务。

下面正文由所有者发送给执行会话时，才成为该会话的执行授权。本规划会话没有激活它。

```text
我授权你在 /path/to/personal-control-hub 启用并核实原生目标模式，按已准备的 PCH 重构计划持续实施。任务ID：PCH-REFACTOR-20261014；计划版本：1.0.0-20261014。

先读适用AGENTS.md、STATE.yaml.pch_refactor，以及：
/path/to/personal-control-hub/docs/pch_refactor_plan.md
/path/to/personal-control-hub/data/roadmap/pch_refactor_v1.yaml
计划按需读取当前轮次，默认上下文≤8192 bytes；不要恢复无关任务或完整历史。

唯一动态台账是STATE.yaml.pch_refactor；YAML任务目录只定义任务/依赖/基线，不建立第二状态机或调度器。保留P0已验规划和旧任务，从R1.1的PCH-R1-1-01开始；如果已有执行记录，从首个未完成且依赖满足任务续接，复用当前有效证据。

先get_goal核对；没有目标时用当前原生create_goal创建带本task ID和计划引用的短目标，并get_goal确认实际状态。已有本task目标则复用；其他task目标不覆盖。没有对应原生能力就如实说明，不能声称已激活，也不建Hook、/loop替代品或定时循环。不要自行填写token预算。仅全部约定验收通过才complete；暂停/阻塞按当前工具真实规则办理。

PCH必须成为无Agent、无模型接口依赖的本地项目管理系统。顺序：P1三真实项目同步到最小页面；P2逐项对账39个当前登记根及每日独立同步；P3任务树/关系配置/甘特与未排期；P4代表性预览；P5 Agent入口；P6整体运行验收。优先复用现有Python/SQLite/SourceResolver/ProjectService/API/Web，不重写栈、不另建同功能账本。发现、采集、查询、统计、配置编辑、进度与预览索引运行时不能调用LLM。

本授权允许本task所需PCH代码、页面、schema、测试、脱敏配置、文档和唯一状态的可逆改动。R1.1先按现有GOVERNED小范围流程完成本task入口/权限规则交接，局部解除旧“不做UI”限制并修task路由；普通产品实现按实际风险执行。不启动旧all_projects_governance v3、不修改全局Agent规则、不接管旧任务。首次写前复核runner实际支持的task ID与候选路径；runner只检查，不授新权限。

先核对Git脏树、精确native任务身份、owned文件及服务/数据写责，保护全部已有改动。不依据idle badge、进程名或缺锁判断无writer；仅重叠未解决组等待，继续独立工作。全部业务子项目默认只读；云项目排除；removed_local禁止探测；旧非当前路径和新增候选先核对身份/读资格，不默认注册或计入39分母。不要启动全部项目、GPU业务、占用用户前台或更改主机网络/时区。凭据只由所有者在本机受控入口处理。

每轮完成：复核相关输入→实现/配置→真实联调→必要测试→精确差异复核→更新唯一台账→安全主分支交付→汇报；有明确可行下一步就继续，不逐轮重复请求已给授权。测试/故障注入使用临时副本；页面验收分别核对数据、交互、持久化、视觉、console/network。截图、HTTP200、同步成功、Agent宣称完成和Git提交都不能替代功能/人工接受。

冻结基线111个等权叶任务、32计划轮；P0为3个规划任务，产品108项。每轮报告本轮IDs/实际可见效果、V/N与增量、待验证/待人工/阻塞/已交付main、接入覆盖与字段质量/新鲜度、实际检查及未运行、证据入口、唯一下一步。子项目业务分母另列。任务/覆盖集合变化先版本化，保留原111→当前N、原39→当前集合及原因；不重复计父子、取消、夹具或过期证据。

我授权正式执行各已验收轮次把本task拥有的安全改动正常交付对应GitHub主分支main。先实时核对origin、main、仓库可见性/保护与其他人的改动；仅精确stage本轮owned unit，禁止git add -A、force-push、改remote、reset/stash他人工作或绕过保护。需PR时按检查/审批流程并attach PR。push/merge后核对远端main包含本轮commit；本地或临时分支不报main已同步。禁止上传本机DB、真实任务消息、凭据、完整私人路径清单或敏感截图。若远端交付被阻塞，保存已验证本地候选与原因，继续不依赖它的工作；有正常交付路线则完成它。跨仓mutation/validation/state/Git/external effects串行。

R5.4的真实子项目改动必须等我明确给出project/file/change/acceptance；本Prompt不自动授予外仓写入。PCH管理配置不被导入覆盖；缺关键业务事实保持partial，分母未知不猜百分比；过期/历史不冒充当前。真实timer、注销持久运行和人工接受未发生时保留pending，不用手动启动、模拟时钟或静默替代；不空等或伪造完成，先完成独立可做项并保存恢复点。

真实权限/凭据拒绝、保护边界、缺失输入或不可绕过的main阻塞要准确报告，记录版本、失败指纹、原分母、证据和唯一恢复动作。连续无进展改变方法，不借重启/新任务重置累计失败或预算。没有可推进项时按原生工具规则结束该路线；未验项仍保留。用户直接stop/pause时立即停止本task effects并按精确身份interrupt本task全部live descendants，不停止其他owner任务。

完成全部约定验收、真实调度、必要人类接受、条件Agent闭环及授权交付后收口并停止；不得自行扩到外网访问、跨设备同步、外部渠道、所有子项目业务重写或发布/部署。
```

原生能力核验：规划时 CLI 为 `codex-cli 0.159.3`，Desktop 暴露 create_goal/get_goal/update_goal，实际 get_goal 为 null；本轮未创建目标。接口说明见 [官方 App Server 文档](https://learn.chatgpt.com/docs/app-server#api-overview)。执行时以该会话真实可用工具和返回状态为准，不把这条历史核验当作已激活。
