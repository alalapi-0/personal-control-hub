# Round 00 · Week 1 学习笔记

> **目标**：不怕黑窗口。只练 `pwd`、`ls`、`cd` 三个命令，建立"我现在在哪"的感觉。

---

## 本周核心概念

### Terminal 和 Shell 的区别

| 名词 | 是什么 |
|------|--------|
| **Terminal** | 一个应用程序（macOS 里就叫 Terminal.app），打开之后你会看到一个黑色或白色窗口 |
| **Shell** | Terminal 里面接收你输入命令的程序，默认是 `zsh`（macOS）或 `bash`（Linux） |

类比：Terminal 是"房间"，Shell 是"房间里帮你干活的助手"。

---

### 提示符长什么样

打开 Terminal 后，你会看到类似这样的内容：

```
username@MacBook-Pro ~ %
```

- `username`：你的用户名
- `MacBook-Pro`：你的主机名
- `~`：当前目录（`~` 代表你的 home 目录，即 `/Users/你的用户名`）
- `%`：提示符，表示"你可以输入命令了"

---

## 本周三个命令

### `pwd` — 我现在在哪

```bash
pwd
```

输出示例：
```
/Users/username/cli-lab/round0
```

**记法**：Print Working Directory（打印工作目录）

---

### `ls` — 这里有什么

```bash
ls
```

输出示例：
```
notes  practice
```

常用参数：
- `ls -l`：以列表形式显示，含权限、大小、时间
- `ls -a`：显示隐藏文件（以 `.` 开头的文件）
- `ls -la`：两个合用

**记法**：LiSt（列出）

---

### `cd` — 去别的地方

```bash
cd 目录路径
```

几种常见写法：

| 写法 | 含义 |
|------|------|
| `cd notes` | 进入当前目录下的 notes 子目录 |
| `cd ..` | 回到上一级目录 |
| `cd ~/cli-lab/round0` | 直接跳到这个绝对路径 |
| `cd ~` | 回到 home 目录 |
| `cd -` | 回到上一次所在的目录 |

**绝对路径 vs 相对路径**：
- 绝对路径：从根目录开始，如 `~/cli-lab/round0/notes`
- 相对路径：从当前位置出发，如 `../notes`（回上级再进 notes）

---

## 本周资源

1. **MIT Missing Semester Lecture 1**（只看前 20 分钟）  
   https://missing.csail.mit.edu/2020/course-shell/

2. **Apple Terminal 入门**（看"Open or quit Terminal"和"Execute commands"部分）  
   https://support.apple.com/guide/terminal/

---

## 本周完成后，你应该能回答

- [ ] Terminal 和 Shell 是同一个东西吗？
- [ ] `~` 代表什么？
- [ ] `cd ..` 和 `cd ~` 有什么区别？
- [ ] 绝对路径和相对路径有什么区别？
