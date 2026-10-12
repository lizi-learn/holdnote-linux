# HoldNote — 按住 Super，把文字留下

面向 **Ubuntu 22.04 / GNOME / X11** 的小工具：长按 Windows/Super 键，拖选文字，松开后自动保存到自建 Memos，并收到桌面通知。

不用切到笔记窗口，不用每次点粘贴。只记录明确选择的文本，不持续收集剪贴板，也不记录日常打字。

**v0.1 仅做文字。截图未实现。Wayland 和其他桌面环境暂不支持。**

## 怎么用

1. 按住左/右 Super 约 450ms，等绿色提示出现。
2. 保持按住，用鼠标左键拖选文字。
3. 松开 Super，自动提交；成功后通知“已保存到 Memos”。

Esc 或其他非 Shift 按键会取消这次采集。没有新选择时不会误发之前的 PRIMARY 内容；选中的换行、缩进原样保留。默认追加 `#inbox #holdnote`，可在配置中将 `tags` 设为 `[]` 关闭。

短按 Super 仍打开/关闭 GNOME 概览。Super+其他按键的快捷键不用于保存。

**桌面设置变化：**运行期间关闭 GNOME 的原生单键 Super 概览处理，由程序保留短按行为；窗口移动从 Super+拖动改为 **Alt+拖动**，避免抢走文本拖选。停止服务时恢复原值。如果你手动改了这些设置，恢复过程不会覆盖你的新值。

## 安装客户端

只安装在自己的桌面电脑，不需要 root 守护进程或 `input` 用户组。

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 python3-xlib \
  python3-keyring python3-cryptography python3-requests python3-venv python3-wheel libnotify-bin
git clone https://github.com/lizi-learn/holdnote-linux.git
cd holdnote-linux
bash install.sh
~/.local/bin/holdnote configure --url https://YOUR-MEMOS-HOST
~/.local/bin/holdnote session-start
```

`configure` 隐藏输入 Memos PAT，不把令牌放进命令参数。Memos 网页中在用户设置创建个人访问令牌；建议为此客户端单独建立一个令牌。不要使用浏览器 Cookie、管理员密码或临时登录 JWT 代替。

客户端登录桌面后自启动，后台由 systemd **用户服务**管理：

```bash
systemctl --user status holdnote
systemctl --user stop holdnote       # 停用，恢复桌面设置
systemctl --user restart holdnote    # 更新客户端/更换令牌后重启
holdnote doctor
holdnote status
holdnote retry
```

CLI 也可使用：

```bash
printf '%s\n' '今天值得记录的一段文字。' | holdnote send --stdin
holdnote send --selection   # 明确保存已有 PRIMARY 选区
holdnote send --clipboard   # 明确保存已复制的文字
```

部分应用不提供 X11 PRIMARY。它们可能不能通过拖选直接保存；可主动复制后运行 `send --clipboard`。v0.1 不自动发送 Ctrl+C，也不改写你原来的剪贴板。

## 部署 Memos

服务器用官方 Memos 界面，不重写一个简陋的笔记前端。当前客户端和部署示例锁定 **Memos 0.31.0**：

```bash
cd deploy
sudo install -d -m 700 -o 1000 -g 1000 data
docker compose up -d       # Debian 也可能使用 docker-compose 命令
```

只监听 `127.0.0.1:5230`。容器以 UID 1000 非 root 运行，根文件系统只读，数据目录持久化，512MiB 内存上限；通过自己的 Caddy HTTPS 网站访问，参考 `Caddyfile.example`。将站点块追加到现有配置，不要覆盖其他站点的 Caddyfile。

生产环境先通过 SSH 隧道完成首次管理员初始化，再公开网站。关闭开放注册、设为私人实例，为采集创建普通用户，再使用该普通用户的独立 PAT。不要把尚未初始化的管理员注册界面直接开放到公网。

`deploy/data` 是笔记数据库，不是源码；不会进入 Git。需要备份时，请另行保存自己的数据卷或使用 Memos 导出功能，**不要把个人笔记上传到公开仓库**。

## API 是怎么接的

Memos 0.31 使用 `/api/v1`，令牌通过 `Authorization: Bearer` 传递。本文不是 latest/main 的接口说明；升级大版本前请核对对应版本 API。

```text
GET  /api/v1/auth/me                         # 验证当前用户
POST /api/v1/memos?memoId=<capture UUID>     # 私密文字写入
GET  /api/v1/memos/<capture UUID>           # 重试冲突时确认已保存
```

POST 的 JSON：

```json
{"content":"要记录的文字", "visibility":"PRIVATE"}
```

`holdnote/client.py` 展示完整实现：HTTPS 校验、禁止跟随重定向、有限超时、401/403 不反复尝试、稳定 ID 与冲突后的内容核验。失败时不把服务器响应、令牌或所选文字写进日志。

## 隐私与可靠性

- 只有完成长按并新选中文字才发送；短按、组合键、空白和取消不发送。
- X11 监听的是输入事件元数据，非 Super 手势相关事件立即丢弃；没有逐字键盘日志，也不采集应用历史或浏览器 Cookie。
- 本地令牌、待发队列密钥放在 **GNOME Secret Service**。拒绝回退到明文 keyring。
- 断网时文字在本地先加密，再进入 SQLite 队列；目录 0700、文件 0600。网络恢复后退避重试。
- 同一采集始终使用同一 Memos ID，避免请求结果不确定时重复入库。成功通知仅在服务器确认后出现。
- 成功发送后删除本地队列中的正文，只保留操作状态和回执；失败正文仍加密保留。
- 所有笔记以 `PRIVATE` 写入，客户端使用普通用户 PAT。

**不是端到端加密。**HTTPS 保护传输，待发队列保护本地暂存；Memos 服务器仍能读取笔记正文。服务器管理员权限、主机安全和数据备份仍由实例所有者负责。成功清除的旧 SQLite 页也不构成安全擦除承诺。

## 文件位置

| 用途 | 路径 |
| --- | --- |
| 非秘密配置 | `~/.config/holdnote/config.json` |
| 待发加密队列 | `~/.local/share/holdnote/outbox.db` |
| 原桌面设置（仅运行时） | `~/.config/holdnote/desktop-state.json` |
| 程序环境 | `~/.local/share/holdnote/venv` |
| 用户服务 | `~/.config/systemd/user/holdnote.service` |
| 登录启动 | `~/.config/autostart/holdnote.desktop` |

卸载前先停止服务，确认待发内容已处理，再移除上述程序/配置。不要直接删除加密队列或密钥，否则未发送文字可能无法恢复。

## 扩展到其他目标

`holdnote/destinations.py` 提供 `TextDestination.send(text, capture_id) -> Receipt` 合约，队列与桌面手势不绑定某个发送平台。

- **Telegram Saved Messages**需要用户自己的 MTProto 会话，普通 Bot 不能代替“收藏夹”。后续适配器必须使用稳定的 `random_id`，会话放在私有路径。
- **微信 ClawBot**应对接已有授权会话/桥接服务，保持认证与限流，不在源码放 Cookie 或密钥。

这两项当前**只预留接口，未启用、未联调**。v0.1 不会偷偷把文本多投到 Telegram 或微信。截图也是后续方向，不属于这次发布。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

24 项单元测试覆盖短按、长按、取消、键盘组合、私密写入、禁止凭据重定向、稳定 ID 重试、认证失败暂停、队列加密/恢复和文件权限。真实桌面另外完成了 Ubuntu 22.04 / GNOME 42.9 / X11 的左/右 Super 拖选与松键入库、Esc 取消不入库、短按概览和停用恢复设置验收；CI 不假装证明 Wayland 或所有应用都可用。

## 上游

- [Memos](https://github.com/usememos/memos) / [0.31 API 定义](https://github.com/usememos/memos/tree/v0.31.0/proto/api/v1)
- [Memos API 访问说明](https://usememos.com/docs/integrations/api-access)
- [python-xlib](https://github.com/python-xlib/python-xlib)

本项目 MIT；Memos 和各依赖分别遵循上游许可证。
