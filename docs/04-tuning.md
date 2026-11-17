# 调参与性能（8 GB 显存）

目标：**8 GB 显存下，一张图 25 秒左右，不爆显存**。

---

## 1. 显存里都装了什么

SDXL 类模型出图时的显存占用（fp16）：

| 项 | 约占 |
|-|-|
| UNet / 整个 checkpoint 权重 | 6.4~6.8 GB |
| 文本编码器（SDXL 两个 CLIP） | 已含在上面 |
| 潜变量（832×1216 = 0.8 MP） | 0.3 GB |
| 采样中间张量 | 0.4~0.8 GB |
| VAE 解码 | 0.3 GB |

加起来 **7.2~7.8 GB** —— 8 GB 卡上只剩几百 MB 余量。所以：

- 分辨率是**第一敏感项**（面积翻倍 = 潜变量和注意力翻倍）；
- 别同时开吃显存的程序（游戏、3D 网页、别的 AI 工具）；
- 二次放大（hires fix）等于在解码后再跑一遍采样，必爆。

---

## 2. 推荐参数（本仓库默认值）

| 参数 | 值 | 为什么 |
|-|-|-|
| 分辨率（1:1） | `1024×1024` | SDXL 的甜点，~0.99 MP |
| 分辨率（3:4） | `832×1216` | 长边不超过 1216，竖图最稳 |
| 分辨率（9:16） | `768×1344` | 更瘦，像素控制在 1.03 MP |
| 分辨率（16:9） | `1344×768` | 横图 |
| 步数 `steps` | `28` | 24 步开始就该成形，28 稳一点 |
| CFG | `5.0` | 原作者建议 4~7；5 听话又不糊 |
| 采样器 | `euler_ancestral` | 快、稳；`dpmpp_2m` 更细腻但慢 15% |
| 调度器 | `normal` | |
| denoise（图生图） | `0.6` | 0.5 太像原图，0.75 会跑偏 |
| 负面词 | `low quality, worst quality, bad anatomy, extra digits, missing digits, extra limbs, missing limbs, watermark, text, signature` | 写死在工作流里，用户看不到也不污染提示词 |
| 质量词 | `masterpiece, best quality, newest, absurdres, highres, very aesthetic` | 同上，进节点 8 后与用户词合并 |

> 像素预算：**0.8~1.05 MP** 是 8 GB 卡的安全区。超 1.2 MP 就要赌运气了。

**速度参考**（单步约 0.85 s）：

| 分辨率 | 步数 | 采样耗时 | 端到端（含轮询/编码） |
|-|-|-|-|
| 832×1216 | 28 | ~24 s | 25~27 s |
| 1024×1024 | 28 | ~25 s | 26~32 s |
| 768×1344 | 28 | ~24 s | 26 s |
| 1344×768 | 28 | ~24 s | 27 s |
| 832×1216 | 20 | ~17 s | 18~19 s |
| 832×1216 | 40 | ~34 s | 35~37 s |

---

## 3. 想更快

| 手段 | 省多少 | 代价 |
|-|-|-|
| 步数 28 → 24 | ~3 s | 细节略糙 |
| 步数 28 → 20 | ~7 s | 明显变糙 |
| 分辨率降到 0.6 MP（720×1080 之类） | ~5 s | 更小，接口/手容易崩 |
| 采样器换 `euler`（非 ancestral） | ~1 s | 变化少一点 |
| 加 `--fast`（fp8 计算 / 新 torch 的 fast 路径） | 5~15 s | 可能掉质量，看模型 |
| 常驻 ComfyUI，不关 | 首张省 10~15 s | 一直占着 7 GB 显存 |
| 换 `--lowvram` | 不省时间，反而慢 | 但能跑更高分辨率 |

`--fast` 是 ComfyUI 自带参数，8 GB 卡上实测对 SDXL 有效但要试过再定。

---

## 4. 只让用户填提示词

两种做法，按你的需要挑：

### A. 用本仓库的 Pipe（推荐）

用户看到的只有：

- 模型下拉里 4 个比例；
- 输入框（填提示词，中文也行，模型不太吃中文，见下）；
- `+` → 上传参考图（图生图）。

步数、CFG、采样器、负面词、质量词全在 Pipe 的**阀门**里（管理员面板 → 函数 → 齿轮 → 配置项），用户碰不到，改完立刻生效。

### B. 用工作流本身

如果你想用内置引擎那条路：把要固定的值**直接写死在 JSON** 里（不要留给前端映射），前端节点映射只保留 `text`（节点 2）和 `ckpt_name`（节点 1）。宽/高/步数/seed 的映射删掉，工作流里就是死值。

注意：内置引擎的设置页每次保存都只保留那 6 行固定映射，硬塞负面词映射会被丢掉——所以负面词必须写进节点 3 的 `text`。

---

## 5. 提示词怎么写

NoobAI-XL 是动漫向 SDXL 微调，习惯 **Danbooru 标签**，逗号分隔：

```
1girl, solo, silver blue gradient hair, blue eyes, whale fin ears,
navy maid dress, white apron, standing, night, city lights,
looking at viewer, upper body, detailed background
```

几条经验：

- **权重**：`(tag:1.2)` 加强，`[tag]` 或 `(tag:0.8)` 减弱；
- **构图**：`upper body` / `full body` / `from side` / `wide shot` 比形容词好用；
- **中文**：能吃一点（很多中文提示词能出对应物），但精确度远不如英文标签；
- **反向**：想"不要"什么别写进正向提示词，去改阀门 `NEGATIVE`；
- **多张对比**：同一个 seed 换 prompt 才能看出差异，seed 在消息下方有显示，复制回来即可复现。

---

## 6. 加 LoRA

在 `workflows/text2img.json` 里插一个 `LoraLoader`，串在 checkpoint 和下游之间：

```json
"30": {"class_type": "LoraLoader",
       "inputs": {"model": ["1", 0], "clip": ["1", 1],
                  "lora_name": "your_lora.safetensors",
                  "strength_model": 0.8, "strength_clip": 0.8}}
```

然后把所有引用 `["1", 0]`（MODEL）和 `["1", 1]`（CLIP）的地方改成 `["30", 0]` / `["30", 1]`：
`CLIPTextEncode(2/3/8)` 的 `clip`、`KSampler(5)` 的 `model`。

**千万别动** 节点 1、2、4、5 的编号——Pipe（和内置引擎）靠这些号来覆盖参数。新节点用 30 以上。

LoRA 文件放 `ComfyUI\models\loras\`，`lora_name` 写相对文件名。

---

## 7. 换成别的模型

1. 模型（`.safetensors` 或 `.ckpt`）丢进 `ComfyUI\models\checkpoints\`；
2. ComfyUI 里按 **R** 刷新（或重启）；
3. 回 Open WebUI 刷新页面 —— 模型下拉会自动多出 4 条（Pipe 每 60 秒问一次 ComfyUI）；
4. 显存考量：SD1.5 类（约 2 GB）可以跑更大分辨率；SDXL 类（约 6.6 GB）按本文预算；Flux 之类（12 GB+）8 GB 卡基本只能 fp8/量化 + 低分辨率。

如果某个模型是 **SD1.5**，把分辨率改小（`512×768` 之类）再试，否则会出怪图（同一个人长两条腿那种）。
