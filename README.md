# Loop

Loop 是一个面向 Loop Engineering 的本地记忆和协作 skill。

它的目标不是让用户写复杂命令，而是让你在一句自然语言之后，把当前会话切进更稳定的 loop 工作模式：root agent 会把目标、上下文、角色、gate、证据和交接信息写进本地状态仓库，后续即使上下文压缩、任务变长，或者换一个会话接手，也能从同一份 loop state 继续推进。

状态默认写入：

```text
${LOOP_ROOT:-~/.loop}
```

不要把密钥、token、凭证或隐私信息写进 loop state。

## 安装

Codex：

```bash
./install.sh codex
```

不带参数时也默认安装到 Codex 使用的 skills 目录：

```bash
./install.sh
```

Claude Code：

```bash
./install.sh claude
```

当前项目采用 **Codex-first + 协议兼容** 的路线：底层状态、Markdown skill 和 Python helper 不依赖 Codex 专有 API，安装脚本也支持 Claude Code；但主要使用和验证仍发生在 Codex 环境中。Claude Code 的 skill 发现、subagent 调度习惯和实际体验还有待更多真实使用反馈。

如果你是 Claude Code 用户或开发者，欢迎提交 PR，补充 Claude Code 场景下的文档、测试、安装细节或工作流适配。

安装后重启或重新加载对应 agent，让它发现新的 loop skills。

## 场景模拟

### 代码编写

用户：

```text
这个登录流程有点乱，用 loop 帮我改完，能测的也跑一下。
```

looper 会把当前需求整理成 loop brief，判断是否需要先补计划或 review，然后按任务创建角色，例如实现、review、稳定性检查等。用户不需要手写 agent 分工；中途如果上下文变长，新的会话也能通过 loop state 接上。

### 辩论

用户：

```text
用 loop 来场辩论赛吧，题目是 AI 会不会让程序员变少。
```

looper 会进入 `debate` profile，拆出正反双方、论点、反驳、自由辩论和总结陈词。用户不需要写完整赛制，给一个辩题或大概方向即可；如果规则、立场或时长会影响效果，looper 再追问必要信息。

### 资料收集

用户：

```text
用 loop 帮我查一下 Loop Engineering 到底怎么理解，最后给我一页结论。
```

looper 会把资料收集目标、可信来源要求、未确认问题和输出格式写进 loop state，再根据需要分配检索、整理、质疑和汇总角色。用户不需要提前写完整研究计划，只要说清楚想得到什么结果。

## 兜底入口

一般直接用自然语言触发即可：

```text
进入 loop 模式
用 loop 推进这个
同步 loop 状态
交接 loop root
```

也可以使用兜底命令：

- `$loop`
- `$loop-sync`
- `$loop-status`
- `$loop-handoff`
