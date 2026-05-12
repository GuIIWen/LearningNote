## NPU vs GPU 框架生态对比（训练 · 推理维度）

> NPU 指华为昇腾（Ascend 910B/910C）；GPU 生态以 NVIDIA CUDA 为基准。✗ 表示当前不支持或需替代方案。

---

### 一、推理框架

| 维度 | 对比项 | GPU 生态 | NPU 生态（昇腾） |
|------|--------|----------|-----------------|
| 核心框架 | 推理引擎 | vLLM、SGLang、TGI、TensorRT-LLM | vLLM-Ascend、MindIE、MindSpore Lite |
| 计算加速库 | 底层算子 | CUDA、cuDNN、FlashAttention-2/3 | CANN、FlashAttention for Ascend |
| KV Cache | 内存管理 | PagedAttention（vLLM 原生），成熟稳定 | vLLM-Ascend 移植 PagedAttention；MindIE 自研 KV 管理 |
| 编译加速 | 图优化 | CUDA Graph、torch.compile、TensorRT | aclGraph（ACL 图模式）、MindCompiler 图融合 |
| 精度格式 | 量化支持 | FP8、INT8、GPTQ、AWQ、bitsandbytes | INT8、FP16、FP8 支持中；bitsandbytes ✗ |
| Speculative Decode | 推测解码 | vLLM/SGLang 原生支持，完善 | vLLM-Ascend 路线图中；MindIE 部分支持 |
| 覆盖范围 | 模型支持 | 200+ 架构，HuggingFace 全系列 | Qwen、Llama、DeepSeek 主流验证，持续扩展 |
| 生产可用性 | 成熟度 | 高，广泛生产部署 | 中，Q2 2025 起主推 Production 就绪 |

---

### 二、训练框架

| 维度 | 对比项 | GPU 生态 | NPU 生态（昇腾） |
|------|--------|----------|-----------------|
| 深度学习框架 | 前端框架 | PyTorch、JAX、TensorFlow | PyTorch + torch_npu 插件、MindSpore |
| 分布式加速库 | 大规模训练 | Megatron-LM、DeepSpeed、FSDP | MindSpeed（Megatron/DeepSpeed 昇腾适配）、deepspeed_npu |
| 分布式并行 | 并行策略 | DP/TP/PP/SP/MoE EP，成熟完备 | DP/TP/PP 支持；MoE EP 优化中；Zero3+Offload 有限制 |
| 集合通信库 | 互联通信 | NCCL | HCCL（灵衢高速互联） |
| 自定义算子 | 算子生态 | Triton / CUDA C++ 生态丰富 | CANN 算子，部分 Triton 适配中，存在算子 gap 风险 |
| 训练精度 | 混合精度 | BF16、FP16、FP8 | BF16、FP16；FP8 支持中 |

---

### 三、微调 / RLHF 框架

| 维度 | 对比项 | GPU 生态 | NPU 生态（昇腾） |
|------|--------|----------|-----------------|
| SFT 微调 | 框架支持 | LLaMAFactory、unsloth、HF TRL | AscendFactory |
| 强化学习框架 | RLHF | verl、OpenRLHF、TRL | MindSpeed-RL；verl/OpenRLHF NPU 已适配 |
| 参数高效微调 | PEFT | LoRA/QLoRA，HF PEFT 原生支持 | LoRA 可用；QLoRA（bitsandbytes）受限 |
| Transformers 系列 | HF 生态 | Transformers/Accelerate/PEFT/TRL 完全原生 | Transformers/Accelerate/PEFT/TRL 已原生支持 Ascend NPU |

---

### 四、部署与服务

| 维度 | 对比项 | GPU 生态 | NPU 生态（昇腾） |
|------|--------|----------|-----------------|
| 在线推理服务 | 服务框架 | vLLM Serve、Triton IS、Ray Serve | vLLM-Ascend、MindIE Server、GPUStack (NPU) |
| 云原生支持 | 容器化 | Docker + K8s + NVIDIA Device Plugin，完善 | Atlas 设备插件 + K8s，华为云 ModelArts |
| 权重兼容性 | 模型格式 | SafeTensors/GGUF/GPTQ，HuggingFace Hub | HuggingFace 格式直接加载；部分需转为 MindIR |
| 监控 & 日志 | 可观测性 | Prometheus + Grafana，vLLM metrics 完备 | vLLM-Ascend 复用 vLLM metrics；MindIE 自有监控体系 |



## NPU 模型适配全景矩阵：训练 / 推理 × LLM / AIGC

> NPU = 华为昇腾 910B/910C；✗ = 当前不支持；★ 成熟度基于 2025 Q2 现状

---

### 一、训练阶段

| 维度 | LLM（大语言模型） | AIGC（多模态生成） |
|------|-----------------|-----------------|
| **底层框架** | PyTorch + torch_npu、MindSpore | PyTorch + torch_npu、MindSpore |
| **训练加速库** | MindSpeed（对标 Megatron）、deepspeed_npu | ascend_diffusers（图像）、AscendX-MM（视频） |
| **并行策略** | DP/TP/PP 完备；Zero-3+Offload 部分限制；MoE EP 优化中 | DeepSpeed ZeRO 分布式扩散模型训练；HCCL 通信 |
| **微调框架** | LLaMA-Factory（原生）、HF PEFT/Accelerate/TRL | ascend_diffusers LoRA；AscendCloud-AIGC LoRA（Wan2.2 T2V/I2V） |
| **RLHF** | MindSpeed-RL；verl/OpenRLHF NPU 支持进行中 | 不适用 |
| **已验证模型（图像）** | Qwen2.5/3、Llama 3.x、DeepSeek V3/R1、Qwen-VL、InternVL | SD 1.5 / SDXL / SD3.5、HunyuanDiT；FLUX.1 适配中 |
| **已验证模型（视频）** | — | Wan2.1 / Wan2.2、HunyuanVideo、CogVideoX-5B |
| **精度支持** | BF16 / FP16 ✅；FP8 进行中；INT4（bitsandbytes）✗ | BF16 / FP16 ✅；FP8 支持中 |
| **受限项** | bitsandbytes ✗、QLoRA 受限、xFormers 需替代 | xFormers ✗、Triton 算子未全覆盖、ControlNet 训练弱 |
| **数据编排** | HF Datasets / MindRecord，流程与 GPU 无差异 | Ascend 专用 CSV 元数据格式（视频路径、文本、时长） |
| **整体成熟度** | ★★★★☆ 主流全流程已完整验证 | ★★★☆☆ 图像基本打通，视频 LoRA 2025 H2 完善 |

---

### 二、推理阶段

| 维度 | LLM（大语言模型） | AIGC（多模态生成） |
|------|-----------------|-----------------|
| **推理引擎** | vLLM-Ascend（主推）、MindIE（自研高性能）、MindSpore Lite（端侧） | ascend_diffusers、AscendX-MM、ComfyUI + comfyui_ascend_node |
| **注意力加速** | FlashAttention for Ascend；PagedAttention（移植）；aclGraph 图模式 | aclGraph 静态图加速 DiT/UNet；高性能模式（enable_high_performance） |
| **KV Cache** | vLLM-Ascend PagedAttention；MindIE 自研 KV 管理；Prefix Cache 支持 | VAE 编解码优化；AscendX-MM 内置显存管理 |
| **推测解码** | vLLM-Ascend 路线图中；MindIE 部分支持 | 不适用 |
| **量化精度** | INT8 / FP16 ✅；FP8 支持中；GPTQ/AWQ 部分受限；bitsandbytes ✗ | FP16 / BF16 ✅；FP8 推理支持中；GGUF ✗ |
| **已验证模型（图像）** | Qwen2.5/3、Llama 3.x、DeepSeek V3/R1、Qwen-VL | SD 1.5 / SDXL / SD3.5、HunyuanDiT；FLUX.1 适配中 |
| **已验证模型（视频）** | — | Wan2.1/2.2（T2V+I2V）、HunyuanVideo、CogVideoX-5B |
| **服务化** | vLLM-Ascend Serve（OpenAI 兼容）、MindIE Server、GPUStack NPU 版 | ComfyUI WebUI（图形化）、ModelArts Lite Server HTTP API |
| **云原生** | K8s + Atlas 设备插件；Prometheus metrics 复用 vLLM | ModelArts Lite Server（Snt9B/Snt9B23）；多卡单机支持 |
| **生态对齐度** | 接口与 GPU vLLM 高度对齐，量化生态是主要缺口 | ComfyUI 需额外 Ascend 节点，社区生态弱于 GPU |
| **整体成熟度** | ★★★★☆ 推理链路完整，主流模型可生产部署 | ★★★☆☆ 图像可用，视频 2025 H1 主流模型已支持 |


## NPU 适配优化全景矩阵（含代码修改 / 算子 / 图编译 / 静态图）

> NPU = 华为昇腾 910B/910C | CANN = 昇腾异构计算架构 | GE = Graph Engine | ATC = Ascend Tensor Compiler | ✗ = 当前不支持

---

### 一、框架层代码修改

| 维度 | LLM 训练 | LLM 推理 | AIGC 训练 | AIGC 推理 |
|------|---------|---------|---------|---------|
| **设备映射** | `import torch_npu` + `transfer_to_npu` 一键自动映射 CUDA API | vLLM-Ascend 插件 patch `ModelRunner` / `Worker`，`--device npu` 启动 | `ascend_diffusers` + `AscendX-MM` 替换 pipeline 内部实现 | `pipeline.to("npu")`；ComfyUI 安装 `comfyui_ascend_node` |
| **手工替换点** | `.cuda()→.npu()`，`nccl→hccl`，`cuda.amp→torch_npu amp` | 权重加载路径指向 npu 设备；`torch.device("npu:0")` | `init_process_group` device 参数；视频模型需改 deepspeed torch.py 156 行 | `enable_high_performance=True`；`MODEL_PATH` 环境变量注入 |
| **迁移工具** | `pytorch_gpu2npu.sh` 脚本工具，生成不支持算子列表 & 迁移报告 | — | Ascend 数据预处理脚本（CSV 元数据格式） | ComfyUI workflow JSON 可复用，node 替换为 Ascend 版 |
| **分布式** | DP 强制改 DDP；`torch.distributed` 接管 | HCCL 通信 buffer 预留（`--gpu-memory-utilization 0.85`） | DeepSpeed ZeRO 分布式训练，device 映射适配 | 单机多 NPU 支持；多机分布式视频推理验证中 |

---

### 二、算子适配优化

| 维度 | LLM 训练 | LLM 推理 | AIGC 训练 | AIGC 推理 |
|------|---------|---------|---------|---------|
| **架构差异** | 达芬奇：Cube（矩阵）+ Vector（向量）+ Scalar，OP-based 调用粒度 | 同左 | VAE 卷积 → CANN NN 库；DiT Self-Attention → FlashAttention for Ascend | 同左 |
| **亲和算子** | CANN 内置 NN 库 / BLAS 库优先；Triton-Ascend 部分算子适配中 | PagedAttention（ACLNN 实现）、RoPE（Vector Unit 向量化）、W8A8 Matmul（Cube Unit）| FlashAttention for Ascend；VAE CANN 内核调用 | Cube Unit 原生 INT8 Matmul；DDIM scheduler 全 tensor 化 |
| **融合算子** | FlashAttention（QKV 融合）、RMSNorm 融合、Rotary Embedding 融合 | `fuse_norm_quant`、`fuse_qknorm_rope`、`fuse_allreduce_rms`；FLASHCOMM2 通信计算重叠 | Wan VAE 时空分离编码、3D attention 封装 | aclGraph 调度算子融合；scheduler step 全在 NPU tensor 化 |
| **缺失算子** | bitsandbytes ✗、xFormers ✗ → Ascend C 自定义算子替代 | GPTQ/AWQ 部分受限；FP8 支持中 | xFormers ✗ → ascend_diffusers 内置 attention；Triton 算子未全覆盖 | GGUF ✗；DiT 算子偶发精度 gap，需 `allow_fp16_reduced_precision_reduction` |

---

### 三、图编译（动态 → 图模式）

| 维度 | LLM 训练 | LLM 推理 | AIGC 训练 | AIGC 推理 |
|------|---------|---------|---------|---------|
| **核心路径** | **TorchAir**：`torch.compile(model, backend="torchair")`，Dynamo 捕获 FX Graph → Ascend IR → CANN GE 编译 | **aclGraph**：`torch_npu.npu.graph` 捕获静态图，固化 stream 回放，消除逐 op 调度 | MindSpore 天然图模式（MindCompiler）；PyTorch 路径可对 UNet/DiT forward 编译 | ascend_diffusers `enable_high_performance` 内置 aclGraph；ComfyUI 节点自动选择图/Eager |
| **收益** | GE 自动算子融合、权重重排、HBM 访存优化；MoE MFU >30% | DeepSeek 671B Decode 吞吐翻倍；单层优化 9.5 ms；MoE rollout 总耗时减少 28 s+ | 训练 forward 图固化后，后续 step 无编译开销；算子调度合并 | 固定分辨率场景端到端延迟显著下降 |
| **自定义优化** | TorchAir 自定义 FX Pass，可消除冗余 Transpose/Cumsum 算子 | `enable_view_optimize` 开关；兼容 chunk_prefill、prefix_cache、PD 分离全特性 | — | ATC 离线编译（适合固定 shape 生产部署） |
| **限制** | FP8 需 CANN 8.0+；recompute 节点需在 GE 图中显式声明 | 动态 shape 需分 bucket 编译；首次有预热开销 | LoRA 训练图动态路径较多，完全静态化收益有限 | ComfyUI 工作流动态，暂不适合全链路 ATC 转换 |

---

### 四、静态图转换 & Shape 管理

| 维度 | LLM 训练 | LLM 推理 | AIGC 训练 | AIGC 推理 |
|------|---------|---------|---------|---------|
| **Shape 策略** | Padding + Bucketing 对齐可变 seq_len；固定 seq_len 场景完全静态图 | Prefill（动态）→ Eager / bucket 图；Decode（固定 batch×1）→ aclGraph 静态图 | 图像固定 H×W；视频按 (T,H,W) bucket 管理；VAE 分块固化每段 shape | 文生图全流程 aclGraph；文生视频按分辨率+帧数两维度 bucket 编译 |
| **KV/显存管理** | FP8 静态量化 cast 节点需在图中显式声明 | `block-size=128`（NPU 内存对齐）；预留 15% 给 HCCL buffer & 碎片 | VAE chunk 分块避免 OOM；LoRA 合并到 base 权重后再编译 | 多步去噪各步 shape 相同，静态图命中率高；scheduler CPU offload 减少 sync |
| **ATC 离线编译** | — | 固定 shape 在线服务可 ATC 转 .om，彻底消除运行时编译开销 | — | 固定分辨率推理服务推荐 ATC；ComfyUI 动态场景暂不适用 |

---

### 五、性能调优 & 调试工具

| 工具 | 用途 | 适用场景 |
|------|------|---------|
| `torch_npu.profiler` | 算子级耗时分析，对标 CUDA Profiler | LLM / AIGC 训练推理均适用 |
| `npu-smi` | 设备状态监控，对标 nvidia-smi | 全场景 |
| Ascend C RTC | 即时编译调试自定义算子 | 算子开发 |
| DeepXTrace | 推理集群快慢卡在线检测（MoE 篇） | 大规模 LLM 推理 |
| MindInsight | 训练过程可视化 | LLM / AIGC 训练 |
| AscendX-MM profiling | 视频推理分段耗时分析 | AIGC 视频推理 |

> **版本管理重要提示**：CANN / driver / torch_npu / torch 四者版本需严格匹配，版本不对齐是最常见故障根因。