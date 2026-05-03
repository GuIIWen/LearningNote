 一、整体架构概览

  用户请求 → FastAPI (main.py) → LangGraph StateMachine → 各节点执行 → 结果返回
  
  ---
  二、核心流程代码详解

  1. 入口层 (main.py)

  关键代码：
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      ensure_default_workspace()
      await init_db(DEFAULT_WORKSPACE_ID)
      yield

  app = FastAPI(lifespan=lifespan)

  # 中间件注入 workspace 和 request_id
  @app.middleware("http")
  async def inject_request_log_context(request: Request, call_next):
      request_id = f"req_{uuid.uuid4().hex[:8]}"
      workspace_id = validate_workspace_id(request.headers.get(WORKSPACE_HEADER))
      request.state.request_id = request_id
      request.state.workspace_id = workspace_id
      # ...

  核心要点：
  - Workspace 隔离：通过请求头 X-Workspace-Id 实现多租户
  - 数据库初始化：每个 workspace 独立的 SQLite 数据库
  - 日志上下文：通过中间件注入 request_id 和 workspace_id

  ---
  2. 流式处理层 (consume.py)

  SSE 流式核心代码：
  @router.post("/agentic/runs/stream")
  async def consume_onto_agent_format(request: Request, data: CompatStreamRequest):
      progress_queue = asyncio.Queue()  # 进度队列

      def on_progress(payload: dict):
          """进度回调，将技能执行的进度推入队列"""
          _req_loop.call_soon_threadsafe(progress_queue.put_nowait(payload))

      config = {
          "configurable": {"thread_id": session_id},
          "metadata": {"on_progress_callback": on_progress, "run_id": run_id},
      }

      async def event_generator():
          """SSE 事件生成器"""
          async for event in agent.astream_events(invoke_state, config=config, version="v2"):
              # 处理 LangGraph 事件
              if event_type == "on_chain_end" and event_name == "aggregator":
                  output = data.get("output", {})
                  if output.get("awaiting_user_input"):
                      # ask_user 处理
                      await transport_queue.put(wrap_v2("special_tool.ui", {...}))

          # 同时处理进度队列
          while True:
              msg = await progress_queue.get()
              await transport_queue.put(wrap_v2(msg["type"], msg["payload"]))

      return StreamingResponse(event_generator(), media_type="text/event-stream")

  流式协议格式：
  packet = {
      "protocol": "agent-stream.v2",
      "event_id": f"{run_id}:{seq}",
      "seq": seq,
      "ts": int(time.time() * 1000),
      "session_id": session_id,
      "run_id": run_id,
      "type": "reasoning.delta",  # 事件类型
      "source": {"node": "planner", "component": "workflow"},
      "payload": {...}
  }

  ---
  3. Guardrail 节点 (guardrail.py)

  核心逻辑：
  async def node_guardrail(state: OntoConsumeState) -> Dict[str, Any]:
      # 1. 优先处理 ask_user 恢复（确定性分支）
      resolved = resolve_ask_user_response(pending_user_action, user_action_response)
      if resolved.get("ok"):
          # 提取参数
          extracted_params = await _extract_resume_params_with_llm(...)
          # 判断是否切换新问题
          should_escape = await _should_escape_resume_with_llm(...)
          if should_escape:
              # 清空状态，按新问题处理
              return {"user_query": resolved_text, "awaiting_user_input": False, ...}
          # 合并参数，恢复执行
          merged_execution_params.update(extracted_params)
          return {"execution_params": merged_execution_params, ...}

      # 2. 正常闲聊检测（LLM 分支）
      chain = GUARDRAIL_PROMPT | llm
      response = await chain.ainvoke({"user_query": user_query})
      is_chit_chat = result.get("is_chit_chat", False)
      return {"is_chit_chat": is_chit_chat, ...}

  ask_user 恢复机制：
  def _build_resumed_query(response_text: str, resume_ctx: Dict[str, Any]) -> str:
      """构建恢复查询，保留原始意图"""
      resume_node = resume_ctx.get("resume_node")
      if resume_node == "execution":
          original_query = resume_ctx.get("original_user_query")
          # 合并原始问题和新补充信息
          return f"{original_query}；补充信息：{response_text}"
      return response_text

  ---
  4. Terminology 节点 (terminology.py)

  术语统一核心代码：
  async def node_terminology(state: OntoConsumeState, config: RunnableConfig) -> Dict[str, Any]:
      # 1. 处理术语澄清恢复
      if ask_resume_context.get("resume_node") == "terminology":
          # 使用 LLM 分析用户确认意图
          intent_result = await _detect_user_intent_with_llm(user_reply, clarification_issues)

          if intent_result.overall_intent == "new_query":
              # 用户切换新问题，继续正常流程
              pass
          else:
              # 处理用户确认结果
              for issue in clarification_issues:
                  action = decision_map.get(issue["original_term"])
                  if action == "confirm":
                      unified_query = unified_query.replace(original_term, suggested_term)
              return {"unified_query": unified_query, ...}

      # 2. 正常术语统一
      result = await skill_executor.execute_skill(
          skill_name="terminology_unify",
          params={"input_text": user_query, "threshold": 0.9},
          on_progress=on_progress
      )

      # 3. 检查是否需要澄清
      if _is_terminology_ask_needed(data):
          # 构建 ask_user payload
          ask_payload = build_ask_user(
              reason_type="ambiguous_term",
              context_patch={
                  "resume_node": "terminology",
                  "clarification_issues": clarification_issues,
                  "standard_terms": standard_terms,
              }
          )
          return {"awaiting_user_input": True, "pending_user_action": ask_payload}

      return {"unified_query": unified_query, "standard_terms": standard_terms}

  ---
  5. Intent 节点 (intent.py)

  意图识别核心代码：
  async def node_intent_extraction(state: OntoConsumeState, config: RunnableConfig) -> Dict[str, Any]:
      # 1. 动态绑定已启用的业务技能到 LLM
      llm_with_skills = _get_llm_with_skills()  # 绑定已启用技能

      # 2. 构建完整 prompt（包含多轮对话上下文）
      full_prompt = [sys_msg]
      if global_blackboard:
          bb_preview = json.dumps(global_blackboard)[:3000]
          full_prompt.append(SystemMessage(content=f"【前序轮次执行背景】\n{bb_preview}"))
      full_prompt.extend(messages)
      full_prompt.append(HumanMessage(content=unified_query))

      # 3. 调用 LLM
      response = await llm_with_skills.ainvoke(full_prompt)

      # 4. 处理 Tool Calls
      if hasattr(response, "tool_calls") and response.tool_calls:
          # 合并可能被拆分的 tool_calls
          for tc in response.tool_calls:
              merged_name = tc.get("name")
              merged_args.update(tc.get("args", {}))

          if merged_name:
              # Fast Track 路径：直接命中技能
              return {
                  "fast_track_skill": merged_name,
                  "execution_params": merged_args,
                  "intent": "skill_execution_intent"
              }

      # 5. 自然语言意图：实体匹配
      matcher_service = GraphMatchEntitiesService(llm=llm)
      matched_entities = await matcher_service.execute(unified_query)

      # 6. 图查询
      query_service = GraphQueryEntitiesService(skill_loader=skill_loader)
      query_result = await query_service.query(matched_entities)

      return {"entities": matched_entities, "graph_path_result": query_result.to_dict()}

  ---
  6. Router 节点 (router.py)

  路由决策核心代码：
  def router_logic(state: OntoConsumeState) -> Literal["skill_execution", "nl2sql", "planner",
  "aggregator"]:
      # 1. ask_user 检测
      if state.get("awaiting_user_input"):
          return "aggregator"

      # 2. Fast Track 检测
      if state.get("fast_track_skill"):
          return "skill_execution"

      # 3. 实体技能分析
      for entity in entities:
          inputs = entity.get("__skills__", {}).get("inputs", [])
          if inputs:
              return "skill_execution"

      # 4. 图路径分析
      graph_path_result = state.get("graph_path_result")
      path_exists = graph_path_result.get("data", {}).get("path", {}).get("path_exists")

      if not path_exists:
          # 检查是否有匹配实体
          matched_entities = [e for e in entities if e.get("type") == "entity"]
          if matched_entities:
              return "nl2sql"
          return "aggregator"  # 无路径

      path_nodes = graph_path_result.get("path_nodes", [])

      # 5. 检查路径节点上的技能
      path_skills_to_execute = []
      for node in path_nodes:
          n_inputs = node.get("__skills__", {}).get("inputs", [])
          for inp in n_inputs:
              path_skills_to_execute.append(inp.get("skill_name"))

      if path_skills_to_execute:
          return "skill_execution"

      # 6. 路径长度判断
      if path_exists:
          # 1跳 → nl2sql
          # 多跳 → planner
          return "nl2sql" if len(path_nodes) == 2 else "planner"

  ---
  7. Execution 节点 (execution.py)

  技能执行核心代码：
  async def node_skill_execution(state: OntoConsumeState, config: RunnableConfig) -> Dict[str, Any]:
      target_skill = state.get("fast_track_skill")
      current_params = state.get("execution_params", {})

      # 1. 检查技能启用状态
      manager = get_skill_manager()
      if not manager.is_enabled(target_skill):
          return {"execution_result": [{"status": "disabled"}]}

      # 2. 构建图谱追踪
      full_graph_trace = build_skill_trace(target_skill, state, current_params)
      seed_runtime(global_blackboard, full_graph_trace)

      # 3. 执行技能
      res = await skill_loader.execute_skill(
          target_skill,
          current_params,
          unified_query=state.get("unified_query"),
          on_progress=on_progress,
          session_id=run_id,
          global_blackboard=global_blackboard,
      )

      # 4. 处理 ask_user 中断
      if res.get("status") == "awaiting_input":
          pending_action = res.get("pending_user_action")
          pending_action["context_patch"]["resume_node"] = "execution"
          return {
              "awaiting_user_input": True,
              "pending_user_action": pending_action,
          }

      return {"execution_result": [{"skill": target_skill, "status": "success", "data": res}]}

  ---
  8. NL2SQL 节点 (nl2sql.py)

  问题增强核心代码：
  async def node_nl2sql(state: OntoConsumeState, config: RunnableConfig) -> Dict[str, Any]:
      user_query = state.get("unified_query")
      graph_path_result = state.get("graph_path_result")

      # 1. 增强问题：注入实体信息
      enhanced_question, entity_context = _enhance_question_with_entity_context(
          graph_path_result, user_query
      )

      # 场景1：单实体查询
      def _build_single_entity_enhanced_question(entity_name_cn, entity_name_en, available_props,
  user_query):
          return (
              f"【系统已识别实体信息】\n"
              f"实体中文名: {entity_name_cn}\n"
              f"实体英文名: {entity_name_en}\n"
              f"【可用属性列表】\n" + "\n".join([f"  - {p['name_en']}" for p in available_props]) +
              f"\n【用户查询】\n{user_query}"
          )

      # 场景2：双实体关联查询
      def _build_path_entity_enhanced_question(from_entity_en, to_entity_en, ...):
          return (
              f"【系统已识别双实体关联信息】\n"
              f"起始实体: {from_entity_cn}（英文名: {from_entity_en}）\n"
              f"目标实体: {to_entity_cn}（英文名: {to_entity_en}）\n"
              f"【用户查询】\n{user_query}"
          )

      # 2. 调用 instance_data_search 技能
      res = await skill_loader.execute_skill(
          "instance_data_search",
          {"question": enhanced_question},
          on_progress=on_progress
      )

      return {"execution_result": [{"skill": "NL2SQL_Direct_Query", "data": res}]}

  ---
  9. Planner 节点 (planner.py)

  问题拆解核心代码：
  async def node_planner(state: OntoConsumeState) -> Dict[str, Any]:
      graph_path_result = state.get("graph_path_result")
      path_nodes = graph_path_result.get("path_nodes", [])
      path_edges = graph_path_result.get("path_edges", [])

      # 1. 获取图 Schema
      graph_schema = await get_graph_schema()

      # 2. 构建 LLM 拆解链
      parser = PydanticOutputParser(pydantic_object=PlannerPlan)
      chain = PLANNER_SYSTEM_PROMPT | llm | parser

      # 3. 调用 LLM 拆解
      plan = await chain.ainvoke({
          "graph_schema": json.dumps(graph_schema),
          "path_data": f"Nodes: {path_nodes}\nEdges: {path_edges}",
          "user_query": state.get("unified_query"),
          "format_instructions": parser.get_format_instructions(),
      })

      # 4. 返回子任务队列
      return {
          "pending_sub_queries": plan.sub_tasks,
          "original_main_query": state.get("user_query"),
          "is_processing_sub_query": True,
      }

  ---
  10. Dispatch 节点 (execution.py)

  子查询分发核心代码：
  async def node_dispatch_sub_query(state: OntoConsumeState) -> Dict[str, Any]:
      pending = state.get("pending_sub_queries", [])
      completed = list(state.get("completed_sub_results", []))

      # 1. 收集上一轮结果
      if state.get("is_processing_sub_query"):
          completed.extend(state.get("execution_result", []))

      # 2. 检查是否完成
      if not pending:
          return {
              "is_processing_sub_query": False,
              "user_query": state.get("original_main_query"),  # 恢复原始问题
              "execution_result": completed,  # 合并结果
          }

      # 3. 出队下一个子任务
      next_task = pending[0]
      next_q = next_task["query"]
      remaining = pending[1:]

      # 4. 重置状态，准备递归执行
      return {
          "user_query": next_q,
          "unified_query": "",
          "entities": [],
          "execution_result": [],  # 清空
          "pending_sub_queries": remaining,
          "completed_sub_results": completed,
          "is_processing_sub_query": True
      }

  ---
  11. Aggregator 节点 (aggregator.py)

  结果聚合核心代码：
  async def node_answer_aggregation(state: OntoConsumeState) -> Dict[str, Any]:
      # 1. 错误处理
      if state.get("errors"):
          return {"final_answer": f"抱歉，系统未能找到相关路径: {errors}"}

      # 2. ask_user 处理
      if state.get("awaiting_user_input"):
          pending_action = state.get("pending_user_action")
          question = resolve_ask_user_question(pending_action)
          return {
              "final_answer": question,
              "awaiting_user_input": True,
              "pending_user_action": pending_action,
          }

      # 3. 闲聊直接返回
      if state.get("is_chit_chat"):
          return {"final_answer": state.get("final_answer")}

      # 4. LLM 聚合生成最终答案
      chain = ANSWER_AGGREGATION_SYSTEM_PROMPT | llm
      response = await chain.ainvoke({
          "user_query": state.get("user_query"),
          "execution_result": json.dumps(state.get("execution_result"))
      })

      return {"final_answer": response.content}

  ---
  三、技能执行引擎 (executor.py)

  核心执行循环：
  async def execute(self, skill_name: str, params: Dict[str, Any], ...) -> Any:
      # 1. 注册中断令牌
      CancelRegistry.register(session_id)

      # 2. 初始化上下文
      self.context = ExecutionContext(params, session_id, ...)

      # 3. 创建节点
      planner_node = PlannerNode(llm, read_context_file, self.context, ...)
      worker_node = WorkerNode(llm, execute_physical_script, self.context, ...)
      finalizer_node = FinalizerNode(llm, self.context, ...)

      # 4. 执行循环
      while not self.context.is_skill_completed:
          # 中断检测
          if CancelRegistry.is_cancelled(session_id):
              break

          # Planner 规划
          plan_dag = await planner_node.run(skill_doc, ...)

          # ask_user 处理
          if plan_dag.awaiting_user_input:
              _save_runtime_state()  # 保存 runtime 快照
              return {"status": "awaiting_input", ...}

          # 上报 DAG 计划
          self._report_progress({"type": "dag_plan", "tasks": plan_dag.tasks})

          # Worker 执行
          new_tasks = [t for t in plan_dag.tasks if t.task_id not in self.context.completed_tasks]
          await worker_node.run(new_tasks, session_id, ...)

          self.context.increment_iteration()

      # 5. Finalizer 汇总
      result = await finalizer_node.run(skill_name, skill_doc, params, ...)
      return result

  ---
  四、关键设计模式

  1. 状态管理模式

  class OntoConsumeState(TypedDict):
      messages: Annotated[List[BaseMessage], add_messages]  # 自动合并
      entities: Annotated[List[Dict], operator.add]         # 累加
      execution_result: Annotated[List[Dict], operator.add] # 追加
      awaiting_user_input: bool                             # 标志位

  2. 进度上报模式

  def on_progress(payload: dict):
      progress_queue.put_nowait(payload)  # 线程安全

  # 在技能执行中
  self._report_progress({"type": "dag_plan", "tasks": [...]})
  self._report_progress({"type": "tool.started", "tool_name": "..."})

  3. 中断恢复模式

  # 保存快照
  runtime_pool[skill_name] = self.context.snapshot_runtime_state()

  # 恢复快照
  resume_runtime_state = runtime_pool.get(skill_name)
  self.context = ExecutionContext(..., runtime_state=resume_runtime_state)

  ---
  五、数据流转总结

  用户请求
      ↓
  FastAPI → 注入 workspace/request_id
      ↓
  LangGraph → Guardrail → Terminology → Intent → Router
      ↓                                    ↓
      ├─→ Fast Track ─────────────→ Execution
      ├─→ 图路径 1跳 ─────────────→ NL2SQL
      ├─→ 图路径 多跳 ────────────→ Planner → Dispatch → 循环
      └─→ 无路径/ask_user ────────→ Aggregator
      ↓
  SSE 流式输出




---
---
好的！我用一个具体场景，带你一步步看代码。

  场景：用户发送 "分析一下 ECO-001 的成本影响"

  ---
  第 1 步：请求到达

  前端发送：
  fetch('/api/agentic/runs/stream', {
    method: 'POST',
    body: JSON.stringify({
      request: "分析一下 ECO-001 的成本影响",
      session_id: "sess_abc123",
      context: {}
    })
  })

  后端接收：

  # 第 198 行：函数入口
  @router.post("/agentic/runs/stream")
  async def consume_onto_agent_format(request: Request, data: CompatStreamRequest):

  做了什么：
  - FastAPI 把 HTTP 请求解析为 CompatStreamRequest 对象
  - 提取 3 个关键参数：
    - request = "分析一下 ECO-001 的成本影响"
    - session_id = "sess_abc123"
    - context = {}（空字典）

  ---
  第 2 步：解析参数

  # 第 199-212 行
  workspace_id = _resolve_workspace_or_404(request)  # → "default"
  user_query = req_data.get("request", "")              # → "分析一下 ECO-001 的成本影响"
  session_id = req_data.get("session_id")             # → "sess_abc123"
  context = req_data.get("context", {})              # → {}
  hitl_tool_response = context.get("hitl_tool_response")  # → None（首次提问）

  # 生成 run_id
  run_id = f"run_{uuid.uuid4().hex[:8]}"  # → "run_1a2b3c4d"

  做了什么：
  - 从请求头提取 workspace_id（默认 "default"）
  - 从请求体提取用户问题、会话ID
  - 生成唯一的 run_id 标识这次运行

  ---
  第 3 步：创建进度队列

  # 第 223 行
  progress_queue = asyncio.Queue()

  # 第 224 行：获取当前事件循环
  _req_loop = asyncio.get_event_loop()

  # 第 226-232 行：定义进度回调
  def on_progress(payload: dict):
      event_type = payload.get("type")
      # 日志记录
      logger.info(f"[*] SSE Sync[default]: Received {event_type}")
      # 推入队列
      _req_loop.call_soon_threadsafe(progress_queue.put_nowait(payload))

  做了什么：
  - 创建一个队列 progress_queue，用于接收技能执行的进度
  - 定义 on_progress 函数，稍后会传给 LangGraph

  为什么要用队列？
  - 技能执行在后台，会有进度更新（如 "正在查询数据库..."）
  - 通过队列把进度传递给前端

  ---
  第 4 步：构建 LangGraph 配置

  # 第 234-237 行
  config = {
      "configurable": {"thread_id": session_id},  # "sess_abc123"
      "metadata": {
          "on_progress_callback": on_progress,    # ← 进度回调
          "run_id": run_id                        # "run_1a2b3c4d"
      },
  }

  # 第 239-243 行
  invoke_state = {
      "messages": [HumanMessage(content=user_query)],  # 用户的原始问题
      "user_query": user_query,                       # "分析一下 ECO-001 的成本影响"
      "planner_thinking_enabled": False,              # 不启用深度思考
  }

  做了什么：
  - 准备 LangGraph 执行所需的配置
  - thread_id 用于会话持久化（跨轮对话）
  - on_progress_callback 用于接收进度

  ---
  第 5 步：启动 SSE 生成器

  # 第 247 行
  async def event_generator():
      # 这是生成器，会 yield SSE 事件给前端

  FastAPI 这里做了什么：
  # 第 497 行
  return StreamingResponse(event_generator(), media_type="text/event-stream")

  - FastAPI 检测到返回值是异步生成器
  - 自动设置为 SSE 响应
  - 前端会持续接收事件流

  ---
  第 6 步：初始化（进入 event_generator）

  # 第 249-250 行
  await init_db(workspace_id)  # 确保 default workspace 的数据库存在
  agent = build_onto_agent(checkpointer=...)  # 构建 LangGraph 状态机

  做了什么：
  - 初始化数据库（创建表）
  - 构建 Agent 状态机（包含所有节点：guardrail → terminology → intent → router → ...）

  ---
  第 7 步：创建 3 个队列

  # 第 254-255 行
  transport_queue = asyncio.Queue()   # 传输队列（输出到 SSE）
  db_write_queue = asyncio.Queue()    # 数据库写入队列

  _seq_counter = 0  # 事件序号计数器

  3 个队列的作用：

  ┌─────────────────┬──────────────────────────┬────────────────────────────┐
  │      队列       │           用途           │            举例            │
  ├─────────────────┼──────────────────────────┼────────────────────────────┤
  │ progress_queue  │ 接收技能进度             │ "正在查询 ECO-001 数据..." │
  ├─────────────────┼──────────────────────────┼────────────────────────────┤
  │ transport_queue │ 合并所有事件，输出到 SSE │ 最终推送给前端的数据       │
  ├─────────────────┼──────────────────────────┼────────────────────────────┤
  │ db_write_queue  │ 持久化到数据库           │ 存储会话历史               │
  └─────────────────┴──────────────────────────┴────────────────────────────┘

  ---
  第 8 步：启动数据库写入任务（后台）

  # 第 295 行
  db_writer_task = asyncio.create_task(_async_event_writer())

  后台任务做什么：

  # 第 257-293 行
  async def _async_event_writer():
      async with aiosqlite.connect(...) as db:
          # 1. 存储用户消息
          await db.execute(
              "INSERT INTO agent_session_events ...",
              (session_id, run_id, 0, "chat_message", user_query)
          )

          # 2. 循环存储所有后续事件
          while True:
              evt_data = await db_write_queue.get()
              await db.execute("INSERT INTO agent_session_events ...", evt_data)
              await db.commit()

  为什么要后台写入？
  - 不阻塞主流程
  - 实时持久化，防止数据丢失

  ---
  第 9 步：定义事件包装器

  # 第 297-313 行
  def wrap_v2(event_type: str, payload: dict, node="planner", component="workflow"):
      nonlocal _seq_counter
      _seq_counter += 1  # 序号递增：0 → 1 → 2 → 3 ...

      packet = {
          "protocol": "agent-stream.v2",
          "event_id": f"run_1a2b3c4d:1",  # 第 1 个事件
          "seq": 1,
          "ts": 1726789234567,
          "session_id": "sess_abc123",
          "run_id": "run_1a2b3c4d",
          "type": event_type,  # 如 "turn.started"
          "payload": payload,
      }

      db_write_queue.put_nowait(packet)  # 同时推入数据库队列
      return packet

  做了什么：
  - 包装事件为统一格式
  - 同时推入数据库队列（一鱼两吃）

  ---
  第 10 步：启动 LangGraph 执行

  # 第 468 行：创建任务
  graph_task = asyncio.create_task(_run_graph())

  # 第 325-441 行：_run_graph 函数
  async def _run_graph():
      # 1. 发送开始事件
      await transport_queue.put(
          wrap_v2("turn.started", {
              "user_preview": "分析一下 ECO-001 的成本影响"
          })
      )

  此时发生了什么：
  - 事件被推入 transport_queue
  - 同时被推入 db_write_queue（存数据库）

  数据库记录：
  event_type = "turn.started"
  payload_json = {"type": "turn.started", "seq": 1, ...}

  ---
  第 11 步：流式执行 LangGraph

  # 第 342 行：开始流式执行
  async for event in agent.astream_events(invoke_state, config=config, version="v2"):
      event_name = event.get("name", "")      # 如 "guardrail"
      event_type = event.get("event", "")     # 如 "on_chain_end"

  astream_events 做什么：
  - 逐节点执行 LangGraph 状态机
  - 每个节点完成后产生事件
  - 实时返回，不等待全部完成

  假设执行路径：
  guardrail → terminology → intent → router → skill_execution → aggregator

  ---
  第 12 步：处理 guardrail 节点完成

  # 第 346-349 行
  if event_type == "on_chain_end":
      data = event.get("data", {})
      output = data.get("output", {})
      await _push_graph_trace_from_output(output, "guardrail")

  guardrail 输出可能是什么：
  output = {
      "is_chit_chat": False,  # 不是闲聊
      "awaiting_user_input": False,
      "user_query": "分析一下 ECO-001 的成本影响"
  }

  此时 SSE 发送给前端：
  event: agent.v2
  data: {"type":"graph.trace","seq":2,"payload":{...}}

  ---
  第 13 步：处理 aggregator 节点完成

  # 第 351-365 行
  if event_type == "on_chain_end" and event_name == "aggregator":
      data = event.get("data", {})
      output = data.get("output", {})

      # 检查是否需要 ask_user
      if output.get("awaiting_user_input"):
          awaiting_input = True
          pending_action = output.get("pending_user_action")

      # 检查有最终答案
      if output.get("final_answer") and not awaiting_input:
          await transport_queue.put(
              wrap_v2("token.delta", {
                  "channel": "content",
                  "text": output["final_answer"]  # "根据分析，ECO-001 的成本影响为..."
              })
          )

  aggregator 输出可能是什么：
  output = {
      "final_answer": "根据分析，ECO-001 的成本影响为 50 万元...",
      "execution_result": [
          {"skill": "eco_impact_analysis", "status": "success", "data": {...}}
      ]
  }

  ---
  第 14 步：发送结束事件

  # 第 427-429 行
  await transport_queue.put(
      wrap_v2("turn.finished", {"status": "success"})
  )

  # 第 430 行
  await transport_queue.put({"status": "completed"})  # 终止信号

  此时事件流：
  event: agent.v2
  data: {"type":"turn.finished","seq":10,"payload":{"status":"success"}}

  ---
  第 15 步：SSE 输出到前端

  # 第 472-482 行：主循环
  while True:
      msg = await transport_queue.get()  # 从队列获取事件

      if isinstance(msg, dict) and "protocol" in msg:
          # 推送给前端
          yield f"event: agent.v2\ndata: {json.dumps(msg)}\n\n"

      elif msg.get("status") == "completed":
          break  # 结束 SSE 连接

  前端接收到的完整流：
  event: agent.v2
  data: {"type":"turn.started","seq":1,...}

  event: agent.v2
  data: {"type":"graph.trace","seq":2,...}

  event: agent.v2
  data: {"type":"reasoning.delta","seq":3,...}

  event: agent.v2
  data: {"type":"token.delta","seq":9,"payload":{"text":"根据分析..."}}

  event: agent.v2
  data: {"type":"turn.finished","seq":10,...}

  ---
  第 16 步：清理资源

  # 第 483-495 行：finally 块
  finally:
      CancelRegistry.cleanup(run_id)      # 清理取消注册
      graph_task.cancel()                  # 取消图任务
      progress_task.cancel()               # 取消进度任务

      # 等待数据库写入完成（最多3秒）
      await asyncio.wait_for(db_write_queue.join(), timeout=3.0)
      db_writer_task.cancel()

  做了什么：
  - 清理所有后台任务
  - 确保数据库写入完成
  - 释放资源

  ---
  总结：代码执行流程

  用户请求 → 参数解析 → 创建队列 → 构建配置 → 启动生成器
                                              ↓
                                      初始化 + 创建任务
                                              ↓
                                      ┌─────────────────────┐
                                      │   并发执行           │
                                      │                     │
                                      │  _run_graph         │
                                      │  _run_progress      │
                                      │  _async_event_writer│
                                      │                     │
                                      └─────────────────────┘
                                              ↓
                                      transport_queue 合并
                                              ↓
                                      SSE yield 推送
                                              ↓
                                      清理资源

  这样是不是更清晰了？每一步只做了一个小动作，逐步推进！
