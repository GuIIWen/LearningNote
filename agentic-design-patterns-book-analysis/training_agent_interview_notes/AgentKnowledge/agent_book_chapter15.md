# Inter-Agent Communication (A2A) - 智能 Agent 设计模式

## 1. 核心理念与定位
### 解决的核心痛点
- **框架孤岛**：使基于不同技术栈（如 LangGraph, CrewAI, AutoGen, ADK）构建的 Agent 能够互操作。
- **能力瓶颈**：通过任务委派（Delegation），让单体 Agent 借助其他垂直领域 Agent 的能力。
### A2A 的本质
- 一个基于 HTTP(S) 和 JSON-RPC 2.0 的开放通信标准，专为“AI 智能体之间的对话与任务协同”设计。

## 2. A2A 协议的基础支柱 (核心概念)
### 核心参与者
- **User (用户)**：任务的最终发起人。
- **Client Agent (客户端)**：代表用户发起 A2A 请求的一方。
- **Server Agent (服务端)**：接收请求、执行任务并返回结果的一方（对客户端而言是黑盒）。
### Agent 卡片 (Agent Card)
- **定义**：Agent 的“数字身份证”（JSON 格式）。
- **内容**：暴露了 Agent 的 Endpoint URL、版本、身份验证方式（如 API Key）、输入输出模式，以及它所掌握的具体 `skills`（技能）。
### Agent 发现机制 (Discovery)
- **知名 URI**：托管在标准路径下（如 `/.well-known/agent.json`），供公开扫描。
- **策展注册表 (Registry)**：企业内部的集中式 Agent 目录，支持权限控制与条件查询。
- **直接配置**：在私有系统中硬编码或私下共享卡片信息。

## 3. 通信机制与任务交互 (Interaction Mechanisms)
面对 AI 推理不可预测的耗时，A2A 提供了 4 种底层交互范式：
- **同步请求/响应 (Sync)**：适用于快速出结果的简单操作。客户端“阻塞”等待完整结果。
- **异步轮询 (Async Polling)**：服务端先返回一个 `Task ID`，客户端随后定期发送请求查询任务进度（适用于较长时间推理）。
- **流式更新 (SSE - 服务器发送事件)**：建立持久单向连接，服务端将思考过程或部分结果持续 Push 给客户端（`sendTaskSubscribe`）。
- **推送通知 (Webhook)**：针对极度耗时的任务，客户端留下回调 URL，彻底释放资源，等待服务端完工后主动通知。

## 4. 架构对比：A2A vs. MCP
- **MCP (Model Context Protocol)**：
  - **定位**：Agent <--> 外部工具/数据。
  - **目的**：为 Agent 构建上下文（连接数据库、API），让其感知物理世界。
- **A2A (Agent-to-Agent)**：
  - **定位**：Agent <--> Agent。
  - **目的**：专注于复杂系统中的任务委派、协调、异步并行处理与智力协作。

## 5. 安全性考量 (Security)
- **mTLS (双向 TLS)**：在 Agent 之间建立极其严格的加密加密通信。
- **审计日志 (Audit Logs)**：详细记录哪个 Agent 在何时将什么任务委派给了谁，保证问责制（这在工业级场景中不可或缺）。
- **凭据隔离**：通过 HTTP 头传递令牌，避免敏感密钥在消息正文或 URL 中泄露。