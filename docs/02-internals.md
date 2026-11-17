# 内部机制：从"打字"到"图片"

这篇是给想改代码 / 换模型 / 接别的前端的人看的。里面提到的数字和行号都来自 Open WebUI **0.13.3** 和 ComfyUI **0.45.0** 的本地安装。

---

## 0. 一张图看懂数据流

```
[浏览器]
   │  POST /api/chat/completions {model:"xiaoyu_img.noobai_xl_v1_1__3x4", messages:[...]}
   ▼
[Open WebUI 后端]
   │  1. 解析 model → pipe_id = "xiaoyu_img"
   │  2. 找到已加载的函数模块，取 Pipe 实例
   │  3. 收集 __user__ / __files__ / __event_emitter__ / __task__
   │  4. 只把"签名里写了"的参数传进去
   ▼
[Pipe.pipe()]
   │  5. body["model"] → (checkpoint, 比例)
   │  6. 拼工作流 dict（节点号 → class_type + inputs）
   │  7. POST http://127.0.0.1:8188/prompt
   │  8. 每 0.4 s GET /history/<prompt_id>
   │  9. GET /view?filename=... 拿字节（顺便算耗时）
   │ 10. yield 一行状态 + markdown 图片链接（流式）
   ▼
[Open WebUI] 把 yield 出来的字符串包成 SSE chunk 推给浏览器
   ▼
[浏览器] 渲染 markdown → <img src="http://<内网IP>:8188/view?..."> → 直连 ComfyUI 取图
```

---

## 1. Open WebUI 的模型清单从哪来

`GET /api/models` 汇总多个来源（Ollama、OpenAI 兼容、Functions）。Functions 那部分的核心：

```python
# open_webui/functions.py
async def get_function_models(request=None, user=None) -> list[dict]:
    functions = Functions.get_functions_by_type("pipe", active_only=True)
    for function in functions:
        module = request.app.state.FUNCTION_MODULES[...]
        if not hasattr(module, "pipes"):
            # 单管道：一个函数 = 一个模型，id 就是函数 id
            ...
        else:
            # manifold：一个函数 = 多个模型
            for p in await module.pipes():
                models.append({
                    "id": f"{function.id}.{p['id']}",
                    "name": p["name"],
                    ...
                })
```

所以：

- `pipes` 可以是 **list**，也可以是 **同步函数 / 异步函数**（三种都支持）；
- 子模型 id 会自动拼成 `函数id.子id`；
- 子 id 里**不能有点**（`get_pipe_id()` 按第一个 `.` 切分）。

我们的 `pipes()`：

```python
async def pipes(self):
    out = []
    for ck in await self._checkpoints():          # 问 ComfyUI 要模型列表
        for rk, (w, h) in RATIOS.items():         # 1x1 / 3x4 / 9x16 / 16x9
            out.append({
                "id": f"{_slug(ck)}__{rk}",       # noobai_xl_v1_1__3x4
                "name": f"🎨 {clean(ck)} · {RATIO_LABEL[rk]}",
            })
    return out
```

`_checkpoints()` 读的是 ComfyUI 的 `/object_info/CheckpointLoaderSimple`：

```
GET http://127.0.0.1:8188/object_info/CheckpointLoaderSimple
→ {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"]]}}}}
```

结果缓存 60 秒；ComfyUI 没开的时候回退到阀门 `FALLBACK_CHECKPOINTS`，这样下拉框至少不会空。

---

## 2. 为什么参数要"具名"

`generate_function_chat_completion()` 里：

```python
params = {"body": form_data} | {
    k: v for k, v in extra_params.items() if k in sig.parameters
}
```

`extra_params` 里其实有 `__event_emitter__`、`__event_call__`、`__request__`、`__user__`、`__files__`、`__metadata__`、`__chat_id__`、`__task__` 等等，但**只有写进签名的才会被传**。

这就是为什么第一版用 `**kwargs` 抓 `__task__` 抓不到，导致一条消息出 3 张图（Open WebUI 会拿同一个模型跑"生成标题""生成标签"两个后台任务）。

正确写法：

```python
async def pipe(self, body: dict,
               __user__: Optional[dict] = None,
               __files__: Optional[list] = None,
               __event_emitter__=None,
               __task__: Optional[str] = None,
               **kwargs) -> AsyncGenerator[str, None]:
    if __task__:                 # 后台任务：只回一句话，不画图
        yield "标题草稿"
        return
```

---

## 3. 返回值与流式

函数可以返回 `str`、`Iterator`、`AsyncGenerator`。我们用 `AsyncGenerator`：每 `yield` 一段，Open WebUI 立刻包成一个 SSE chunk 发给浏览器，所以**状态行先到、图后到**，不会干等 25 秒白屏。

```python
yield f"> 🎨 {mode} · `{ckpt}` · {ratio} · {steps} 步 · seed `{seed}`\n\n"
# ... 出图 ...
yield f"\n![image]({link})\n\n"
yield f"*{dt:.1f} 秒 · {w}×{h} · seed `{seed}`*"
```

异常时 `yield f"❌ 出图失败：{e}"`，前端会把这段当正文显示——比抛异常直接 500 友好得多。

---

## 4. 工作流 JSON 的结构

`/prompt` 收的是 **API 格式**：

```json
{
  "1": { "class_type": "CheckpointLoaderSimple",
         "inputs": { "ckpt_name": "NoobAI-XL-v1.1.safetensors" } },
  "2": { "class_type": "CLIPTextEncode",
         "inputs": { "text": "1girl, ...", "clip": ["1", 1] } },
  "5": { "class_type": "KSampler",
         "inputs": { "model": ["1", 0], "positive": ["9", 0],
                     "negative": ["3", 0], "latent_image": ["4", 0],
                     "seed": 123, "steps": 28, "cfg": 5.0,
                     "sampler_name": "euler_ancestral",
                     "scheduler": "normal", "denoise": 1.0 } }
}
```

规则：

- 顶层 key 是**节点号的字符串**；
- 连线写成 `["源节点号", 输出槽位序号]`；
- 每个节点的 `class_type` 必须和 ComfyUI 里注册的名字**完全一致**（大小写敏感）；
- 不认识的 `class_type` 会被 `/prompt` 直接拒（`prompt_outputs_failed_validation`）。

本仓库 `workflows/text2img.json` 的节点分工：

| 节点 | 类 | 作用 |
|-|-|-|
| 1 | `CheckpointLoaderSimple` | 装模型，输出 MODEL / CLIP / VAE |
| 2 | `CLIPTextEncode` | **用户输入的那句话**（前端每次覆盖） |
| 3 | `CLIPTextEncode` | 负面词（写死） |
| 8 | `CLIPTextEncode` | 质量词（写死） |
| 9 | `ConditioningCombine` | 2 + 8 合并 → KSampler 正向 |
| 4 | `EmptyLatentImage` | 空白画布（宽高写在这） |
| 5 | `KSampler` | 采样 |
| 6 | `VAEDecode` | 潜变量 → 像素 |
| 7 | `SaveImage` | 存到 `output/`，文件名前缀 `owui` |

**把用户输入单独放节点 2、质量词单独放节点 8 再合并**，是这个工作流能同时"保留用户原话"和"稳定出好图"的关键。负面词同理，永远不经过用户。

---

## 5. 图片的两种回传方式

| | base64 内嵌 | URL 引用（默认） |
|-|-|-|
| 消息大小 | ~1.6 MB | ~80 字节 |
| 首屏速度 | 整块到齐才渲染，出图后再等 30 s+ | 随出随显 |
| 数据库 | 每条消息存一份图，涨得飞快 | 只存链接 |
| 手机访问要求 | 只要 8080 通 | **8080 和 8188 都要通** |
| 换 IP / 换网络 | 不影响（图已在消息里） | 旧图会失效 |

阀门 `IMAGE_URL_MODE` 可以切换。URL 里的 IP 用 UDP `connect()` 探测（不真的发包）：

```python
sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sk.connect(("223.5.5.5", 80))
ip = sk.getsockname()[0]      # 本机出口网卡的地址
```

也可以用阀门 `LAN_IP` 写死。

---

## 6. 图生图的工作流差异

在文生图基础上加三个节点：

```
LoadImage(20) → ImageScale(21, lanczos, 缩到目标尺寸)
                      ↓
                 VAEEncode(22) → KSampler.latent_image
```

并且 `KSampler.denoise = 0.6`（阀门 `DENOISE`）。denoise 表示"推倒重来多少"：0 是原图不动，1 是完全重画。

参考图要先上传：

```http
POST http://127.0.0.1:8188/api/upload/image
Content-Type: multipart/form-data
  image = <文件>
  type  = input
→ {"name": "owui_upload_00001_.png", ...}
```

然后把这个 `name` 填进 `LoadImage.image`。

> **节点号别撞车**。我们第一版把 `LoadImage` 编号成 `8`，正好和文生图的质量词节点 `8` 冲突：合并出来的工作流里 `ConditioningCombine.conditioning_2` 指向了一个 IMAGE，ComfyUI 报
> `conditioning_2, received_type(IMAGE) mismatch input_type(CONDITIONING)`。
> 现在 img2img 专用节点固定用 20/21/22。

---

## 7. 文件（附件）怎么拿到本地路径

`__files__` 里每个元素大致是：

```json
{"type": "image", "id": "<file-id>", "filename": "cat.png", "path": "/abs/path/cat.png"}
```

优先用 `path`（Open WebUI 一般会给）；没有就退回用 `id` 查数据库：

```python
from open_webui.models.files import Files
rec = Files.get_file_by_id(fid)          # 有的版本是同步，有的是协程
if inspect.isawaitable(rec):
    rec = await rec
path = rec.path
```

---

## 8. 耗时构成（832×1216 / 28 步，RTX 5060 Laptop 8GB）

| 阶段 | 耗时 | 备注 |
|-|-|-|
| 模型加载（仅首张） | +10~15 s | 6.6 GB 从磁盘进显存 |
| 采样 | ~24 s | 28 步 × 约 0.85 s/步 |
| VAE 解码 + 存盘 | ~0.6 s | |
| 轮询延迟 | 0~0.4 s | 间隔 0.4 s |
| **合计（热）** | **25~27 s** | |

采样时间几乎线性于 `步数 × 总像素`。想在 8GB 上更快：降步数到 24（省 ~3 s）、降像素到 0.8 MP（省 ~4 s）。质量换速度，自己权衡。

---

## 9. 想换别的前端 / 别的后端

- **换前端**：任何能发 JSON 的东西都行，核心两步是 `POST /prompt` 拿 `prompt_id`、`GET /history/<id>` 取结果（`outputs.<节点>.images[0]`）。本仓库的 `_run()` 就是 40 行的参考实现。
- **换后端**（Automatic1111、ComfyUI 的 `/api/prompt` 新版接口、别的推理服务）：改 Pipe 里的 `_run()`，其余（模型清单、比例、图生图入口）不用动。
- **接别的模型**：只要 ComfyUI 能加载，套进同一份工作流即可；`pipes()` 会自动多出 4 条。LoRA 的话在工作流里加 `LoraLoader`（`MODEL`/`CLIP` 串联），注意别动 1/2/4/5 这几个被映射的节点号。
