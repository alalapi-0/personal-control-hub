# StorageGovernance B011 Milestone Sync

## 文档职责

- 读者：所有者、后续审计者和本轮独立审查者。
- 目的：保存 B011 已接受里程碑同步的来源、边界与验证证据；不作为当前状态或存储治理授权。
- 更新触发：仅当本次同步候选、来源身份或 Git 交付结果改变。

## 已接受来源

- 外部唯一规范：`~/Documents/StorageGovernance/STORAGE_GOVERNANCE.md`。
- 外部当前状态：`~/Documents/StorageGovernance/STATE.yaml`，SHA-256 `8117fd35223483637cceef490e5ffd8b0c2f8c34301a9148e058c313b40d2058`。
- 已接受里程碑：B011 `accepted_post_clean`。
- 本批释放本机空间：157,114,368 bytes；累计已接受释放：10,318,594,048 bytes。
- 删除后证据：`/Volumes/AI_WORK_SSD/_governance/evidence/B011_POST_CLEAN_EVIDENCE.md`，SHA-256 `b8d41b182595faa7c93ec6b970bd27a16f84eccd38858dc84b86dbb867d73cf2`。
- 接受时存储映射 SHA-256：`c634cc68823b29039047fa01641a4373e17f50b370aee0f838250da3610f8484`。
- B011 本机来源 `~/PycharmProjects/computer_study_plan/.venv-ui` 已在完整删除后验证中保持缺失；其清理授权已经消耗并关闭，不能复用。

## Hub 同步

- `STATE.yaml` 只保存上述当前摘要和唯一下一步。
- Hub 继续只读指向外部规范与当前状态；不复制规范、历史、证据正文或项目数据。
- 注册表和存储适配器的通用边界已经覆盖 B011，因此本次不改它们。

## 边界

本次 Hub 同步不写 StorageGovernance 或 `computer_study_plan`，不执行删除、迁移、真实 API、飞书调用、主分支合并、强推或远端配置修改。离线视频盘继续仅允许从既有权威原样复制路径字符串，不连接、不探测、不猜测、不迁移、不清源。

## 验证

StorageGovernance 最终十文件账本通过 YAML/JSON 解析、旧映射引用清零与新映射引用一致性检查；冻结账本聚合 SHA-256 为 `8640b1401d34cb19a0501bd00fabbc0605320ce66160f02da0a126c435cfd0b0`，fresh Judge/Governor 已分别返回 PASS/APPROVE。Hub 候选冻结后的本地验证、独立 Judge/Governor 结论和最终 Git 身份由 Root 登记；本报告不自证，也不嵌入会造成自引用的候选或提交哈希。
