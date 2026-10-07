# Round 06 Linux 自动化小抄

## Web UI 验收方式

1. 点“综合练习：批量日志处理流水线”的“运行脚本”，查看自动生成的 `batch_process.sh`。
2. 点“产出：完成 Round 06 Linux 自动化小抄”，阅读并补充自己的备注。
3. 点“验收：解释批处理、进程查看和远程排练边界”，能讲清下面的问题后点击“记录并完成”保存记录。

## 命令速查

- `find ... -print0 | xargs -0 ...`：安全处理带空格文件名。
- `sed 's/old/new/g'`：适合轻量替换；批量替换前先备份。
- `awk '{print $1}'`：常用于按列提取、统计日志字段。
- `ps aux | grep name`：只读查看进程，确认任务是否存在。
- `nohup cmd > out.log 2>&1 &`：断开终端后继续运行任务。
- `tmux new -s name` / `tmux attach -t name`：会话级任务保活。
- `rsync -avz src/ dest/`：增量同步优先于重复拷贝。
- `crontab -l` / `crontab -e`：查看或编辑定时任务；真实机器上使用前必须确认影响。

## Web UI 安全边界

- 可以练：`find`、`xargs`、`sed`、`awk`、`ps`、脚本阅读、命令排练文件。
- 不在 Web UI 里直接练：`ssh`、`scp`、`rsync`、`crontab`、`kill`、真实 VPS 操作。
- 真实远程任务必须先走远程操作 checklist，不把命令排练误当成已经部署。

## 最终自问

- [ ] 为什么 `find -print0 | xargs -0` 比普通管道更安全？
- [ ] `sed` 和 `awk` 分别更适合什么场景？
- [ ] `ps` 看到的是进程快照，为什么不是任务保活方案？
- [ ] `nohup` 和 `tmux` 的差异是什么？
- [ ] 为什么真实 `ssh` / `crontab` 操作需要单独授权？
