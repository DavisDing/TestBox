# DESIGN

> Architecture Agent 维护。
> 描述当前需求的技术实现方案。

---

# 1. Overview

## Goal

本次实现的目标。

## Approach

总体实现思路。

---

# 2. Technology

## Frontend

- Framework:
- Language:
- UI:

## Backend

- Language:
- Framework:

## Database

- Database:
- Version:

## Other

其他关键技术。

---

# 3. Architecture

```text
待 Architecture Agent 根据实际项目填写
```

---

# 4. Modules

## Module 1

### Responsibility

模块职责。

### Dependencies

依赖。

### Main Components

核心组件。

---

## Module 2

### Responsibility

模块职责。

### Dependencies

依赖。

### Main Components

核心组件。

---

# 5. Layer Responsibilities

## Frontend

负责：

- UI
- 用户交互
- 页面状态
- 数据展示
- 基础校验

## Backend

负责：

- 核心业务
- 权限
- 数据处理
- 数据校验

## Database

负责：

- 数据持久化
- 数据一致性

---

# 6. Data Flow

```text
待填写
```

---

# 7. API Design

如果没有 API：

NOT_REQUIRED

如果有：

## API-001

### Name

接口名称。

### Method

GET / POST / PUT / DELETE

### Path

/api/xxx

### Request

```json
{}
```

### Response

```json
{}
```

### Error

错误情况。

### Permission

权限要求。

---

# 8. Database Design

如果不需要数据库：

NOT_REQUIRED

如果需要：

## Table: example

### Purpose

用途。

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| id | bigint | Yes | Primary Key |

### Index

待填写。

### Relations

待填写。

---

# 9. Frontend Design

## Pages

### Page 1

#### Purpose

用途。

#### Components

- Component A
- Component B

#### User Actions

- Action 1
- Action 2

#### States

- Loading
- Empty
- Error
- Success

---

# 10. UI Constraints

如果存在已经确认的 UI：

列出：

- 页面
- 文件
- 不应主动修改的部分

例如：

```text
MainPage

Protected:
- Layout
- Visual Style
- Component Structure

Allowed:
- API
- Data
- State
- Business Logic
```

如果没有：

No confirmed UI yet.

---

# 11. Error Handling

列出关键异常：

| Scenario | Expected Behavior |
|---|---|
| Invalid Input | Show validation message |
| API Failure | Show error |
| No Data | Show empty state |

---

# 12. Security

仅记录真正相关的安全要求。

例如：

- Authentication
- Authorization
- Input Validation
- Sensitive Data Protection

如果没有特殊要求：

Standard project security.

---

# 13. Performance

仅记录真正重要的性能要求。

如果没有：

No special performance requirement.

---

# 14. Testing Focus

重点测试：

- Core Flow
- Error Flow
- Boundary
- Permission
- Data Consistency
- Regression

---

# 15. Risks

只记录实际风险。

| Risk | Impact | Mitigation |
|---|---|---|
| Example | Medium | Example |

如果没有：

No major known risks.

---

# 16. Open Questions

需要确认的问题。

如果没有：

None.

---

# 17. Design Decisions

记录重要设计决策。

## Decision 1

### Decision

采用 XXX。

### Reason

因为 XXX。

### Date

YYYY-MM-DD