# Round 02 命令小抄

这份小抄用于最终验收。建议在 Web UI 中打开综合练习结果后，对照这里检查自己是否能解释每一步。

## 重定向与管道

```bash
echo "text" > file.txt
echo "text" >> file.txt
grep "error" app.log | wc -l
sort labels.txt | uniq
```

要点：

- `>` 会覆盖旧内容。
- `>>` 会追加到文件末尾。
- `|` 把左边命令的输出交给右边命令继续处理。
- 浏览器终端会拦截 `;`、`&&`、`||`，所以练习时把步骤拆成多条命令。

## 脚本执行

```bash
bash count_errors.sh
bash show_args.sh one two
```

参数速查：

- `$0`：脚本名
- `$1`：第一个参数
- `$@`：全部参数
- `$#`：参数个数

## Git 最小闭环

```bash
git init
git config user.name "Round 02 Learner"
git config user.email "round02@example.local"
git config commit.gpgsign false
git status
git add README.md
git commit -m "init repository"
git log --oneline
```

Git 边界：

- Round 02 只练本地仓库。
- 不做 `git clone`、`git pull`、`git push`、`git remote`。
- 每次提交前先看 `git status`，确认改动是不是你想提交的。

## 自检问题

- [ ] 我能解释 `>` 与 `>>` 的差异。
- [ ] 我能用一条管道统计错误日志条数。
- [ ] 我能解释 `$1`、`$@`、`$#` 的作用。
- [ ] 我能独立做出至少 2 次 Git 提交。
- [ ] 我能用 `git log --oneline` 解释提交历史。
