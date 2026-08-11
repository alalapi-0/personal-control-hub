#!/usr/bin/env node
/**
 * MCP 配置检查脚本 — 不依赖第三方 npm 包
 * 用法: node scripts/check_mcp_config.js
 */

const fs = require("fs");
const path = require("path");
const os = require("os");

const REPO_ROOT = path.resolve(__dirname, "..");
const MCP_JSON = path.join(REPO_ROOT, ".cursor", "mcp.json");
const ENV_EXAMPLE = path.join(REPO_ROOT, ".env.example");
const SETUP_DOC = path.join(REPO_ROOT, "docs", "MCP_SETUP.md");
const TROUBLE_DOC = path.join(REPO_ROOT, "docs", "MCP_TROUBLESHOOTING.md");

const REQUIRED_SERVERS = ["filesystem"];

const REGISTERED_CANDIDATES = [
  "chrome-devtools",
  "context7",
  "filesystem",
  "github",
  "playwright",
  "stitch",
];

const ENV_KEYS = [
  "GITHUB_TOKEN",
  "GITHUB_PERSONAL_ACCESS_TOKEN",
  "STITCH_API_KEY",
];

let passCount = 0;
let warnCount = 0;
let failCount = 0;

function pass(msg) {
  passCount += 1;
  console.log(`✅ ${msg}`);
}

function warn(msg) {
  warnCount += 1;
  console.log(`⚠️  ${msg}`);
}

function fail(msg) {
  failCount += 1;
  console.log(`❌ ${msg}`);
}

function info(msg) {
  console.log(`ℹ️  ${msg}`);
}

function readText(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    return null;
  }
}

function main() {
  console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log("MCP 配置检查");
  console.log(`仓库路径: ${REPO_ROOT}`);
  console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

  // 1. mcp.json 存在性
  if (!fs.existsSync(MCP_JSON)) {
    fail("缺少 .cursor/mcp.json");
    printSummary();
    process.exit(1);
  }
  pass(".cursor/mcp.json 存在");

  // 2. JSON 可解析
  const raw = readText(MCP_JSON);
  let config;
  try {
    config = JSON.parse(raw);
  } catch (err) {
    fail(`.cursor/mcp.json JSON 解析失败: ${err.message}`);
    printSummary();
    process.exit(1);
  }
  pass(".cursor/mcp.json JSON 格式有效");

  // 3. mcpServers 存在
  if (!config.mcpServers || typeof config.mcpServers !== "object") {
    fail("缺少 mcpServers 根字段");
    printSummary();
    process.exit(1);
  }
  pass("包含 mcpServers 字段");

  const servers = config.mcpServers;
  const serverKeys = Object.keys(servers);

  // 4. 当前项目配置只要求最小本地 filesystem；其余是登记候选，不是强制启用项。
  for (const key of REQUIRED_SERVERS) {
    if (servers[key]) {
      pass(`已配置 server: ${key}`);
    } else {
      fail(`缺少必需 server: ${key}`);
    }
  }

  for (const key of REGISTERED_CANDIDATES) {
    if (!servers[key]) info(`已登记但当前项目配置未启用: ${key}`);
  }

  // 5. stitch（若配置则检查结构）
  if (servers.stitch) {
    const stitch = servers.stitch;
    const hasCommand = stitch.command && Array.isArray(stitch.args) && stitch.args.length > 0;
    const hasPlaceholder =
      stitch.env &&
      stitch.env.STITCH_API_KEY === "<SET_IN_CURSOR_OR_SHELL_ENV>";
    if (hasCommand) {
      if (hasPlaceholder) {
        warn("stitch 已配置但 STITCH_API_KEY 仍为占位符，需用户填入密钥");
      } else {
        pass("已配置 server: stitch");
      }
    } else {
      warn("stitch 配置不完整（缺少 command/args），需用户补充");
    }
  } else {
    info("stitch 未出现在当前项目配置中（登记候选，默认不启用）");
  }

  // 6. filesystem 路径检查
  if (servers.filesystem) {
    const args = servers.filesystem.args || [];
    const allowedPaths = args.filter((a) => typeof a === "string" && a.startsWith("/"));
    const home = os.homedir();

    if (allowedPaths.length === 0) {
      fail("filesystem 未指定允许目录路径");
    } else {
      for (const p of allowedPaths) {
        if (p === "/") {
          fail("filesystem 错误地允许了根目录 /");
        } else if (p === home) {
          fail(`filesystem 错误地允许了整个用户主目录: ${home}`);
        } else if (p.startsWith(home) && p !== REPO_ROOT && p === home) {
          fail(`filesystem 允许路径过宽: ${p}`);
        } else if (p === REPO_ROOT || REPO_ROOT.startsWith(p + path.sep) || p.startsWith(REPO_ROOT)) {
          if (p === REPO_ROOT || REPO_ROOT === path.resolve(p)) {
            pass(`filesystem 允许目录包含当前仓库: ${p}`);
          } else if (REPO_ROOT.startsWith(path.resolve(p) + path.sep) || REPO_ROOT === path.resolve(p)) {
            pass(`filesystem 允许目录覆盖当前仓库: ${p}`);
          } else {
            warn(`filesystem 允许路径 ${p} 与当前仓库 ${REPO_ROOT} 关系需人工确认`);
          }
        } else {
          warn(`filesystem 允许路径 ${p} 与当前仓库 ${REPO_ROOT} 不一致，新 clone 后需更新`);
        }
      }
    }
  }

  // 7. github token 占位符检查
  if (servers.github) {
    const token = servers.github.env && servers.github.env.GITHUB_PERSONAL_ACCESS_TOKEN;
    if (!token || token === "<SET_IN_CURSOR_OR_SHELL_ENV>") {
      warn("github 的 GITHUB_PERSONAL_ACCESS_TOKEN 仍为占位符，需用户在 Cursor 或 shell 环境配置");
    } else if (/ghp_|gho_|github_pat_/.test(token)) {
      fail("github 配置中疑似包含真实 token，请勿提交到 git");
    } else {
      pass("github token 配置已设置（请确认非明文真实密钥）");
    }
  }

  // 8. 检查是否含疑似真实密钥
  if (raw && /ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}/.test(raw)) {
    fail(".cursor/mcp.json 中疑似包含真实 API key/token，请立即移除");
  } else {
    pass(".cursor/mcp.json 未检测到常见 token 明文模式");
  }

  // 9. .env.example
  const envExample = readText(ENV_EXAMPLE);
  if (!envExample) {
    fail("缺少 .env.example");
  } else {
    pass(".env.example 存在");
    for (const key of ENV_KEYS) {
      if (envExample.includes(`${key}=`)) {
        pass(`.env.example 包含 ${key}`);
      } else {
        fail(`.env.example 缺少 ${key}`);
      }
    }
    if (/ghp_|github_pat_|sk-[A-Za-z0-9]{10,}/.test(envExample)) {
      fail(".env.example 中疑似包含真实密钥");
    }
  }

  // 10. 文档
  if (fs.existsSync(SETUP_DOC)) {
    pass("docs/MCP_SETUP.md 存在");
  } else {
    fail("缺少 docs/MCP_SETUP.md");
  }

  if (fs.existsSync(TROUBLE_DOC)) {
    pass("docs/MCP_TROUBLESHOOTING.md 存在");
  } else {
    fail("缺少 docs/MCP_TROUBLESHOOTING.md");
  }

  info(`登记候选 ${REGISTERED_CANDIDATES.length} 个: ${REGISTERED_CANDIDATES.join(", ")}`);
  info(`当前项目配置 ${serverKeys.length} 个: ${serverKeys.join(", ")}`);
  info("运行时可用性：未验证；配置存在不代表已加载、健康或获得动作授权");

  printSummary();
  process.exit(failCount > 0 ? 1 : 0);
}

function printSummary() {
  console.log("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
  console.log(`检查完成: ${passCount} 通过, ${warnCount} 警告, ${failCount} 失败`);
  if (failCount > 0) {
    console.log("请修复失败项后重新运行: node scripts/check_mcp_config.js");
  } else if (warnCount > 0) {
    console.log("存在警告项，请查阅 docs/MCP_SETUP.md 完成手动配置。");
  } else {
    console.log("仓库级 MCP 配置检查全部通过。");
  }
  console.log("\n注意: 通过检查 ≠ 当前 Agent 线程已暴露 MCP 工具或获得调用权限。");
  console.log("仅当用户明确要求运行时验证时，再由用户在 Cursor Settings → Tools & MCP 查看实际状态。");
  console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
}

main();
