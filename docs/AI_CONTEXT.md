# AI CONTEXT

> 项目长期上下文。
>
> 所有 AI Agent 开始工作前应读取。
>
> 只记录长期有效的信息。
> 不记录临时任务过程。

---

# 1. Project Overview

## Name

项目名称。

## Type

例如：

- Desktop Application
- Web Application
- CLI Tool
- Developer Tool
- Automation Tool

## Purpose

项目主要解决的问题。

---

# 2. Development Philosophy

本项目采用：

- 简单优先
- 实用优先
- 小步迭代
- 优先复用
- 避免过度设计
- 避免无意义重构
- 保持代码清晰

不要为了“企业级架构”而增加不必要的复杂度。

---

# 3. Technology Stack

## Frontend

例如：

React + TypeScript

## Backend

例如：

Python + FastAPI

## Database

例如：

SQLite

## Build

例如：

Vite

## Package Manager

例如：

pnpm

---

# 4. Project Structure

```text
project/
├── frontend/
├── backend/
├── database/
└── docs/
```

根据实际项目填写。

---

# 5. Important Architecture

记录项目长期有效的架构信息。

例如：

```text
Frontend
   ↓
API
   ↓
Backend
   ↓
Database
```

---

# 6. Important Modules

## Module A

职责：

待填写。

## Module B

职责：

待填写。

---

# 7. Important Decisions

记录长期有效的技术决策。

## Decision 001

### Decision

采用 XXX。

### Reason

XXX。

### Date

YYYY-MM-DD

---

# 8. Coding Rules

## General

- 优先复用
- 小范围修改
- 不无意义重构
- 不删除未知代码
- 不修改无关模块
- 保持现有代码风格

## Frontend

- 组件化
- 类型明确
- 避免重复
- 优先使用现有组件

## Backend

- 保持职责清晰
- Controller 不堆复杂业务
- Service 负责核心业务
- 数据访问独立

## Database

- 谨慎修改已有结构
- 避免危险操作
- 关注数据兼容性

---

# 9. UI Rules

如果存在已经确认的 UI：

记录：

页面：

待填写。

保护：

- Layout
- Visual Style
- Component Structure

允许：

- API
- Data
- State
- Business Logic

如果没有：

No confirmed UI yet.

---

# 10. Environment

只记录项目运行需要知道的环境。

例如：

- macOS
- Node.js 22
- pnpm
- Python 3.13
- Docker

不要记录：

- Password
- Token
- API Key
- Secret

---

# 11. Dependencies

记录真正重要的长期依赖。

例如：

- Electron
- React
- FastAPI
- SQLite

普通 npm/pip 依赖不需要全部记录。

---

# 12. Known Constraints

记录项目长期约束。

例如：

- 必须支持 macOS
- 不使用云端数据库
- 数据必须本地保存
- 不允许增加重量级依赖

---

# 13. AI Rules

所有 Agent 必须遵守：

1. 先理解项目，再修改。
2. 优先读取现有代码。
3. 优先复用已有能力。
4. 不编造不存在的信息。
5. 不进行无关重构。
6. 不删除未知代码。
7. 不修改无关文件。
8. 不把 Mock 当真实数据。
9. 不伪造测试结果。
10. 遇到不确定信息必须明确说明。

---

# 14. AI_CONTEXT Update Proposal

只有长期有效的信息才需要更新本文件。

例如：

- 技术栈变化
- 架构变化
- 重要模块变化
- 重要技术决策
- 长期约束变化

普通功能开发不需要更新。

如果需要更新：

输出：

AI_CONTEXT UPDATE PROPOSAL

包括：

- Change
- Reason
- Impact
- Proposed Update