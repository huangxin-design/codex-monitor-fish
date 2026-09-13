<div align="center">
  <img src="https://github.com/user-attachments/assets/3b64cc5e-c18b-4941-8929-5831eabfbd1f" width="76" height="76" alt="H 品牌标识">
  <h1>Codex 监控小鱼</h1>
  <p>给每一次创作，留下一份看得见的 Token 记录。</p>
  <p>给 Codex 一句话，选个项目，就能查看 Token 用量。</p>
</div>

Codex 监控小鱼是一个 **Codex Skill**：由 Codex 安装并启动本地网页，用户在网页选择项目，持续查看任务与子任务的 Token 用量。你和朋友都无需手动下载程序、复制文件夹或输入命令。

本项目是独立社区工具，并非 OpenAI 官方产品。

## 发给朋友的一段话

让朋友把下面这段话发给自己电脑上的 Codex：

```text
请从 https://github.com/huangxin-design/codex-monitor-fish 安装 skills/codex-token-monitor 这个 Skill，安装后立即读取它的 SKILL.md 并继续执行，打开项目选择网页，我选好项目后就开始监控 Token。请复用本机或 Codex 提供的 Python 运行时，帮我完成安装和启动，不要让我手动下载、输入命令或安装 EXE。
```

流程是 **发送这段话 → Codex 打开网页 → 选择项目 → 开始监控**。以后说“用 $codex-token-monitor 打开监控网页”即可；它会记住上次的项目。需要重新选择时说“打开 Codex 监控小鱼的项目选择页”。

安装和运行需要仓库可访问、本机有 Codex 使用记录，以及可用的 Python 3.10+。运行时由 Codex 查找系统或宿主提供的版本，程序只使用标准库，无需 API Key 或 pip 包。若远程仓库不可访问，Codex 应明确报告安装尚未完成，不能把 GitHub 链接当作已运行的监控网页。

给 Codex 的执行说明见 [INSTALL.md](INSTALL.md)，日常操作见 [朋友使用指南](docs/使用说明.md)。

![首次选择项目，模拟数据](https://github.com/user-attachments/assets/7b68776d-a196-4daa-a2c4-8db4fbc05cd2)

![Codex 监控小鱼桌面预览，全部为模拟演示数据](https://github.com/user-attachments/assets/d9e732bc-7221-45c7-9a2a-618b0cd71244)

> 截图使用模拟数据，不含真实用户的任务、路径或用量。

## 功能

- **按项目统计**：任务本体与子任务分别显示，归档后保留历史用量。
- **可核对的数字**：区分输入、缓存、输出与推理，支持亿/完整整数，CSV 导出保留整数。
- **订阅成本摊算**：人民币金额直接显示在总 Token 旁边。按自定 20x 方案：每周 20 亿 Token × 4 周、200 美元，支持修改汇率。所有模型采用同一平均成本；缓存和推理不重复相加，缺失日志只计算已确认部分。
- **下一轮重置**：顶部显示 Codex 周额度的预计重置时间与倒计时，读取官方 App Server 或 Codex 提供的账户快照。过期或缺少时间时提示刷新，不把 Spark 的重置时间当作 Codex 周额度。
- **持续更新**：默认每 10 秒刷新；支持暂停、立即刷新、排序与展开任务详情。
- **边读边看**：显示真实读取进度、记录份数和当前任务；读完一个任务就先显示一个，等待期间仍可切换项目。
- **旧记录不挡住整个页面**：无法准确统计的任务单独说明原因；其他任务继续显示，缺失用量不会冒充 0。
- **H 品牌标识与个人头像**：左侧固定 H 标识；右侧点击更换自己的头像。
- **桌面与窄屏**：大屏表格，窄屏任务卡片，合计始终可见。
- **黑红看板**：近黑背景、红色柔光、细描边卡片和大数字，集中展示处理量、订阅成本与下一轮重置。
- **本地运行**：Python 标准库，无 API Key、无第三方运行依赖，监听 `127.0.0.1`。

<details>
<summary>查看逐步读取的效果（模拟数据）</summary>

![读取进度与逐个加入的任务，全部为模拟数据](https://github.com/user-attachments/assets/17a05bfc-1957-4905-90c8-8b232d8c52cd)

</details>

<details>
<summary>安装位置与运行条件</summary>

完整 Skill 的源目录为 `skills/codex-token-monitor`。Codex 优先使用可用的 `skill-installer` 从 GitHub 安装；个人目录通常是 Windows 的 `%USERPROFILE%\.agents\skills\codex-token-monitor` 或 macOS/Linux 的 `~/.agents/skills/codex-token-monitor`。目录和自动发现方式参见 [OpenAI 官方文档](https://learn.chatgpt.com/docs/build-skills)。

Skill 自带解析器、服务与网页，首次启动自动发现本机项目，不需要提前扫描完所有历史。设置保存在 Windows 的 `%LOCALAPPDATA%\CodexMonitorFishSkill` 或 macOS/Linux 的 `~/.local/share/CodexMonitorFishSkill`，运行产物与 Skill 文件分开。同一后台实例会复用；默认端口被占用时自动换端口。

它在用户电脑上运行，需要本机 Codex 使用记录和 Python 3.10+；普通云端聊天网页无法读取电脑记录。缺少运行时或下载失败时，应如实说明实际缺项。已经有完整本地 Skill 的用户，也可让 Codex 直接安装那一份。

</details>

<details>
<summary>兼容：原 Windows 独立程序</summary>

之前使用 EXE 的用户可继续双击已有程序，项目设置仍保存在 `%LOCALAPPDATA%\CodexMonitorFish`。新 Skill 使用独立数据目录，不覆盖原安装。

只有明确需要独立程序时，才使用 [Windows 免安装启动版](https://github.com/huangxin-design/codex-monitor-fish/releases/latest/download/CodexMonitorFish-Windows.exe)。它适用于 Windows 10/11 64 位，自带运行环境；当前未做代码签名。Skill 安装不需要下载它。

</details>

<details>
<summary>开发者方式：Python 启动与配置参数</summary>

## 不通过 Skill 运行

在仓库根目录执行；将示例路径替换为自己的实际项目，输出目录应为新目录或空目录。Windows 可把 `python` 换成 `py -3`，macOS/Linux 可换成 `python3`。

```text
python skills/codex-token-monitor/scripts/setup_monitor.py --project "/path/to/project" --output "/path/to/project/codex-monitor-fish"
python "/path/to/project/codex-monitor-fish/monitor.py" --snapshot
python "/path/to/project/codex-monitor-fish/launch.py"
```

Windows 示例：

```powershell
py -3 .\skills\codex-token-monitor\scripts\setup_monitor.py --project "D:\My Projects\Demo" --output "D:\My Projects\Demo\codex-monitor-fish"
py -3 "D:\My Projects\Demo\codex-monitor-fish\launch.py"
```

之后 Windows 可双击生成目录中的 `打开监控器.cmd`。输出目录非空时，初始化会拒绝覆盖；已有应用直接启动即可。

| 选项 | 用途 |
| --- | --- |
| `setup_monitor.py --codex-home 路径` | 指定迁移后的 Codex 数据目录；默认取 `CODEX_HOME` 或 `~/.codex` |
| `--name 名称` | 设置页面显示的项目名称 |
| `--port 18767` | 配置其他本地端口 |
| `--avatar 图片路径` | 设置右侧默认头像，支持 JPG、PNG、WebP |
| `--exclude-task 任务编号` | 排除该任务及其所有子任务，可重复指定 |
| `launch.py --no-browser` | 只启动并输出地址，供应用内浏览器打开 |
| `launch.py --port 18767` | 临时使用其他端口，不修改配置文件 |

右侧头像还可直接点击上传，最大 2 MB，保存在当前浏览器；换浏览器或端口后需重新选择。左侧品牌图保持不变。

</details>

## 统计口径与兼容性

**这是本机日志可见的历史 Token 处理量，不是订阅剩余额度，也不是计费账单。** 缓存输入已包含在输入中，推理输出已包含在输出中，不能再次相加。图片、视频等外部工具费用不在统计范围。

人民币金额按用户设定的订阅方案分摊：`Token × 200 美元 ÷ (20 亿 × 4 周) × 汇率`。每百万 Token 约 0.025 美元，每亿 Token 约 2.50 美元；默认汇率 7.00 时，每亿约 ¥17.50。20x、每周 20 亿及四周周期是本工具采用的自定估算口径，不是对官方固定 Token 配额的声明。所有模型、长短上下文采用相同平均成本；缓存包含在输入中、推理包含在输出中，不重复计数。

默认 `1 USD = 7.00 CNY` 是可编辑示例值，不是实时汇率；输入无效时保留上次有效值。页面的“统计说明”内可查看公式和修改汇率。金额代表按已处理 Token 分配的订阅成本，不代表按次扣费、额外支出或剩余额度；历史累计跨越多个周期时不会按 200 美元封顶。CSV 继续导出原始 Token，不包含浏览器内设置的汇率。

重置时间独立于所选项目，按[官方 App Server](https://learn.chatgpt.com/docs/app-server) 的 `account/rateLimits/read` 读取；不会触发模型任务或兑换重置券。顶部优先显示 Codex 的七天窗口，以浏览器本地时间展示下一轮重置，倒计时根据官方时间计算。缺少周窗口时仅可使用同一 Codex 额度的窗口，不替换成其他模型。没有官方重置时间、时间已过或快照超过 15 分钟时不推演未来日期，提示获取最新数据。

独立 CLI 与桌面应用的登录状态可能不同。官方接口不可用时，可由 Codex 的账户额度工具获取快照，通过 Skill 中的 `scripts/save_account_snapshot.py --data-dir <启动器返回的目录>` 从标准输入保存；程序只保留计划类型、额度窗口和重置次数，不保存邮箱、账户编号或凭据。重新登录或切换账户后应刷新快照，快照不代表对当前登录身份的实时认证。

当前解析器要求 `state_5.sqlite` 中的任务与父子关系，以及日志中的逐次 `token_usage_record`。它按任务和请求编号去重，避免分叉历史和重复记录被重复计算。具体约定见 [统计说明](skills/codex-token-monitor/references/accounting.md)。

- 只匹配配置项目目录的根任务；子任务通过父子关系归并。其他项目、独立 worktree、其他设备与云端记录不会自动并入。
- 旧版累计 `token_count` 不能代替完整的逐次请求记录。旧版或缺失日志的任务单独标注，汇总只显示已确认的用量下限；无法确认时显示“—”，不导出不完整报表。未知数据库结构仍会提示无法读取。
- 新请求写入日志后才会显示。日期按服务电脑的本地时区。
- 自动刷新本身不会调用模型；调用 Codex 创建、维护这个 Skill 会正常产生任务用量。

Windows 已进行真实启动、浏览器和头像交互验证。macOS/Linux 路径逻辑通过合成数据测试，尚未进行实机浏览器验证；仓库提供三平台 CI 以便发布后持续检查。

## 数据与隐私

项目统计只读任务索引和会话日志，不上传记录、不修改这些源数据。账户刷新会调用本机 Codex CLI 的官方账户接口，由 CLI 使用它自己的登录状态；监控器不自行读取或向网页返回登录密钥。网页没有远程脚本、字体或图片依赖；官方说明链接仅在点击时打开。

生成的配置、快照、CSV、日志、截图可能包含使用者的任务名称和本机路径，请勿提交到公共仓库。仓库的忽略规则与发布脚本会排除运行产物；用于展示的截图均为模拟数据。头像选择保存在浏览器本地。

Skill 把项目选择保存在 `%LOCALAPPDATA%\CodexMonitorFishSkill` 或 `~/.local/share/CodexMonitorFishSkill`；不会添加开机启动项或改写 Codex 的原始记录。退出后不再后台读取数据。

SQLite 在读取正在使用的数据库时可能创建共享内存辅助文件（`-shm`）；程序不执行数据库写入语句，也不改动会话日志。安全检查与已知限制见 [对抗性审核记录](docs/安全审核.md)。

## 开发与发布

运行后端及初始化测试：

```text
python -m unittest discover -s skills/codex-token-monitor/assets/app -p "test_*.py"
python -m unittest discover -s tests -p "test_*.py"
python -m unittest discover -s desktop -p "test_runtime.py"
node --test tests/test_insights.cjs
```

Windows 安装器的离线对抗测试：`powershell.exe -NoProfile -File tests/test_installer.ps1`。也可用 PowerShell 7 运行同一文件。

构建独立 Skill 包：

```text
python tooling/build_release.py
```

输出位于 `dist/`，包含 Skill ZIP 与 SHA-256 校验文件。GitHub Actions 会在提交和拉取请求时运行测试并验证打包；实际 CI 结果以仓库运行记录为准。

在 Windows 构建免安装 EXE（仅开发者需要打包工具）：

```text
python -m pip install -r tooling/requirements-build.txt
python tooling/build_windows.py
python desktop/test_executable.py --exe dist/CodexMonitorFish-Windows.exe
```

EXE 内含 Python 与 PyInstaller 引导程序的许可证声明。免安装启动版仅提供 Windows 64 位；其他平台目前使用 Skill 或源码方式。

提交问题或贡献前，请阅读 [贡献说明](CONTRIBUTING.md) 和 [问题报告说明](SECURITY.md)。

## 许可与品牌

开源许可证尚待作者选择，当前 [LICENSE](LICENSE) 未授予开源许可。已取得的明确分享许可继续有效。H 标识和项目名称单独见 [品牌说明](BRANDING.md)。
