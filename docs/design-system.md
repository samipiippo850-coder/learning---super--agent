# ExamOS 视觉设计说明（v0.2）

参考的公开设计规范：
[anthropics/skills · frontend-design](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md)、
[awesome-claude-ui-armory](https://github.com/ezra-y/awesome-claude-ui-armory)。

## 设计主张

ExamOS 的使用场景不是"数据看板"，而是**每天在纸上与真题对抗的备考作战室**。
因此视觉语言取自「试卷 + 批注」：墨色底、纸色字、朱批红作为唯一行动色，靛青作为完成/稳定色。
面板不做圆角卡片堆叠，而做**有横线的表格纸**：细线分隔、左上角收角标记、数据用等宽字体对齐。
这避免了"近黑底 + 单一亮青 + 圆角卡片"的通用 AI 生成风格。

## Token

| 角色 | 值 | 用途 |
|---|---|---|
| `--bg` | `#12110E` | 墨底 |
| `--panel` | `#171511` | 纸面 |
| `--border` | `#2C2A22` | 横线 |
| `--text` | `#EDE6D6` | 纸色正文 |
| `--muted` | `#A79E8B` | 次级文字 |
| `--accent` | `#C8543A` | 朱批红：主行动 / 错误 / 高风险 |
| `--accent-2` | `#4F9D8F` | 靛青：稳定掌握 / 通过 |
| `--warn` | `#C9A227` | 考分金：进行中 / 待办 |
| `--bad` | `#C8543A` | 与朱批红同源 |

## 字体与排版

- 标题与知识点名：衬线（Georgia / Songti / SimSun），承担"教材/试卷"气质。
- 正文：无衬线（Segoe UI / PingFang SC / 微软雅黑）。
- 数字、代码、状态：等宽（Cascadia Mono / Consolas），并启用 `font-variant-numeric: tabular-nums`，保证分数、ROI 列对齐。
- 标签不做全大写英文眉标；中文标签直接使用句式短语（"今日计划"而非"TODAY PLAN"）。
- 标题层级用字重与字号拉开，不靠彩色单词强调。

## 结构

- 面板 = 1px 横线 + 左上/右上收角刻度；不使用投影。
- 只有真正有顺序的内容使用编号：今日任务顺序、ROI 排名、测试题号。
- 侧栏是一张"作战目录"：朱印 Logo、纵向导航、底部版本信息。
- 动效仅两处：首次进入页面的一次性淡入；用户操作后的即时反馈（按钮/展开）。尊重 `prefers-reduced-motion`。
- 空状态写成行动邀请（"还没有测试记录 → 生成第一份微测"）。

## 可访问性

- 键盘焦点可见（2px 朱批红 outline）。
- 颜色不作为唯一信息载体：状态同时给出文字（"前置未达标"/"可学习"）。
- 对比度：正文 ≥ 7:1，次级文字 ≥ 4.5:1。
- 动效可被系统设置关闭。
