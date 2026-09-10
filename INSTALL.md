# 给 Codex 的 Skill 安装与启动说明

用户要的是：给 Codex 一段话，Codex 安装 **Codex 监控小鱼 Skill**，在本次任务里立即打开项目选择网页，用户选好项目后开始监控。下载文件、安装 Skill 和启动服务由 Codex 完成；最终交付可点击的本地网页。

## 安装 Skill

1. 仓库为 `https://github.com/huangxin-design/codex-monitor-fish`，Skill 路径为 `skills/codex-token-monitor`。检查是否已安装同名 Skill；已有时先读它的 `SKILL.md` 并使用，不盲目覆盖用户修改。
2. 优先使用当前环境可用的 `skill-installer`，按仓库与上述路径安装完整 Skill。没有安装工具时，可获取仓库完整源码，只将 `skills/codex-token-monitor` 文件夹放到用户的 `~/.agents/skills/codex-token-monitor`；遵循宿主实际的技能目录和文件权限。不要只保存 `SKILL.md`，运行脚本、网页与解析器都在同一 Skill 内。
3. **安装后立即读取已安装的 `SKILL.md` 并继续执行启动流程。** 不止回复“已安装”，也不把重启 Codex 当作本次打开页面的前置条件；当前任务可以直接读取并运行刚安装的文件。技能目录与自动发现方式见 [OpenAI 官方文档](https://learn.chatgpt.com/docs/build-skills)。

## 本次任务里打开网页

1. 查找并验证可用的 **Python 3.10+**：先使用宿主提供的运行时发现工具或已知系统解释器，再检查宿主提供的运行环境。不要假定每台电脑都有相同的缓存路径。只需 Python 标准库，无 API Key、pip 依赖或 EXE 安装步骤；没有可用运行时时，如实说明缺少运行条件。
2. 用已经验证的解释器和 Skill 的绝对路径启动。用户要求选择项目时使用 `--choose-project`：

   ```text
   python "<已安装 Skill 的绝对路径>/scripts/start_monitor.py" --choose-project --no-browser
   ```

   将 `python` 替换为实际解释器。脚本在后台运行服务，输出 JSON，其中有 `url`、`data_dir`、`reused` 和 `pid`。首次运行自动发现项目；日常启动省略 `--choose-project` 会恢复上次选择。
3. 校验返回的地址属于 `http://127.0.0.1:<端口>/`，检查 `/api/health` 的应用标识为 `codex-monitor-fish-desktop`，然后在宿主浏览器面板打开返回的完整 `url`。没有面板时用系统浏览器；省略 `--no-browser` 也可由脚本自动打开系统浏览器。
4. 让用户在网页上选择项目并点击 **开始监控**。无需先执行 `setup_monitor.py` 或等待 `monitor.py --snapshot` 扫完历史。大记录由网页显示真实进度，读完一个任务就先显示一个。

Skill 的运行设置保存在 Windows 的 `%LOCALAPPDATA%\CodexMonitorFishSkill`，macOS/Linux 的 `~/.local/share/CodexMonitorFishSkill`；与原 EXE 版分开。默认端口为 `18776`，被其他程序占用时自动换可用端口。已运行的同一实例会被复用，不终止其他进程。头像保存在当前浏览器，更换浏览器或端口后可能需要重新选择。

`--data-dir`、`--codex-home` 和 `--port` 用于用户明确指定目录、迁移后的 Codex 数据或端口时。普通使用沿用默认值，不把这些参数变成用户必须完成的配置。

## 出错时

- GitHub 返回 404、下载失败或限流：如实报告仓库或网络不可访问，不能声称远程 Skill 已安装，更不能把登录失败作为未经验证的结论。若用户已提供完整的本地 Skill，可直接安装该副本。
- 没有可用的 Python 3.10+：先检查宿主是否附带可用运行时；仍没有就说明实际缺项，不改成让用户手动下载 EXE，不虚构已运行的网页。
- 找不到项目：保留项目选择页空状态，说明需要本机 Codex 使用记录。只有确认数据目录迁移后，才在 **没有找到项目？** 中设置实际目录。
- 已有旧版生成目录及 `config.json`：用户要求继续旧看板时沿用原范围、头像和配置，按 Skill 的兼容说明启动；不将旧设置覆盖成新的默认值。
- 服务或浏览器无法启动：报告实际报错和已完成的步骤。仅有文件安装成功，不等于网页可用。

## 部署边界与验收

- 只安装这一个 Skill 并启动本地服务，不修改模型配置、登录凭据或开机启动项。用户的会话记录、CSV、路径和头像留在本机。
- 检查服务健康、项目选择页可访问后，交付实际本地链接。没有记录时展示真实空状态；长时间读取时展示进度，不用演示数字填充真实看板。
- 数字是本机日志可见的历史 Token 处理量，不是订阅剩余额度或账单。监控刷新不调用模型，不需要另建周期性 Codex 任务。
- Windows 已有实机启动与浏览器验证；macOS/Linux 路径与后端可测试，尚无实机浏览器验证，不承诺所有宿主都已实测。

<details>
<summary>兼容：原 Windows EXE 部署</summary>

EXE 仅保留给明确选择独立程序的用户，不是 Skill 安装流程。已部署用户可继续使用原程序与 `%LOCALAPPDATA%\CodexMonitorFish` 设置，Skill 不迁移或覆盖它们。

维护原安装时，下载并检查已确认 Release 标签下的 `tooling/install_windows.ps1`，再以同一 `-ReleaseTag` 运行本地脚本；不要把网络内容直接交给 `Invoke-Expression`。安装脚本校验 EXE 的 SHA-256，安装到原应用目录并保留设置。校验失败停止；系统不允许未签名程序时不关闭防护。SHA-256 证明文件一致性，不等于代码签名。

</details>
