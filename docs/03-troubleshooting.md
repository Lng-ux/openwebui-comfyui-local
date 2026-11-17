# 排错

按"报错长什么样 → 到底哪儿坏了 → 怎么办"排。带 ⭐ 的是实测踩过的。

---

## 1. 跨域 / CORS

**长这样**

```
Access to fetch at 'http://127.0.0.1:8188/prompt' from origin 'http://127.0.0.1:8080'
has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present.
Failed to fetch
```

（Code 里是 `TypeError: Failed to fetch`，Network 面板里那条请求标红、Response 空。）

**根因**：浏览器发跨域请求，ComfyUI 没回 `Access-Control-Allow-Origin`。

**怎么办**

1. 确认 ComfyUI 启动参数带 `--enable-cors-header *`（`curl http://127.0.0.1:8188/system_stats` 看 `system.argv`）；
2. 改完**必须重启 ComfyUI**（参数只在启动时读）；
3. 如果用 `*` 还是不行，换成明确来源：`--enable-cors-header http://192.168.1.5:8080`；
4. 走本仓库 Pipe 的路径**不需要**这个头（服务端到服务端），所以先验证是"浏览器直连"还是"Pipe 直连"：Pipe 报错说明不是 CORS，去看第 2 节。

---

## 2. Cannot connect to ComfyUI ⭐

**长这样**

```
❌ 出图失败：<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>
❌ 出图失败：HTTP Error 404: Not Found
ConnectionRefusedError(10061)
```

**根因**：8188 上没有服务，或地址/端口写错。

**怎么办**

```bat
netstat -ano | findstr ":8188"
curl http://127.0.0.1:8188/system_stats
```

- 没监听 → ComfyUI 没起，或者 bat 被从别的目录调用导致相对路径失效（本仓库的 `run_nvidia_gpu.bat` 第一件事就是 `cd /d "%~dp0"`）；
- 监听了但只 `127.0.0.1` → 手机用不了，加 `--listen 0.0.0.0`；
- 端口不是 8188 → 改阀门 `COMFYUI_URL`；
- 起了又秒退 → 用 cmd 窗口手跑 bat 看报错，最常见是显存被别的程序占满。

---

## 3. checkpoint not found ⭐

**长这样**

```
value not in list: ckpt_name: 'NoobAI-XL-v1.1.safetensors' not in [...]
```

或 ComfyUI /history 里 `status.status_str = "error"`、`messages` 里有 `prompt_outputs_failed_validation`。

**根因**：工作流里写的模型名和 ComfyUI 实际扫到的不一致（含大小写、`.safetensors` 后缀、子目录前缀）。

**怎么办**

```bat
curl http://127.0.0.1:8188/object_info/CheckpointLoaderSimple
```

返回里 `input.required.ckpt_name[0]` 就是合法取值。三条要求：

1. 文件名**逐字**一致；
2. 模型放在 `ComfyUI\models\checkpoints\`，放子目录的话名字会带 `子目录/` 前缀；
3. 放好之后要在 ComfyUI 界面里点一下 **R**（刷新）或重启，服务才会重新扫描。

用本仓库的 Pipe 会自动对齐（`pipes()` 里的清单和出图时的 `_slug` 匹配），所以这条主要出现在"手写 JSON + 内置引擎"的路径上。

---

## 4. out of memory ⭐

**长这样**

```
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB
(GPU 0; 8.00 GiB total capacity; 6.8 GiB already allocated)
Allocation on device
```

或浏览器端 `HTTP Error 500`，ComfyUI 控制台红字。

**根因**：8 GB 装不下「模型 + 当前分辨率 + 步数」的组合。

**怎么办**

| 动作 | 效果 |
|-|-|
| 分辨率降到 ≤ 0.8 MP（832×1216 / 768×1344） | 最有效，显存按面积走 |
| 别同时开游戏/浏览器硬件加速的 3D 页 | 抢显存 |
| ComfyUI 加 `--lowvram` 或 `--normalvram` | 变慢但能跑 |
| 别开二次放大（Hires.fix / Upscale） | 又加一份潜变量 |
| 用 `.fp16` 或 fp8 量化的模型 | 模型体积减半 |

同机实测：832×1216、28 步、NoobAI-XL（6.6 GB）峰值约 7.2 GB，能跑；换 1536 宽就往 OOM 边上走。

---

## 5. 出图是黑图 / 白图 / 绿图 ⭐

**长这样**：消息里图出来了，但整张纯黑、纯白、或者惨绿。

**根因**（按概率）

1. **VAE 不对**：工作流里 `VAEDecode.vae` 没接、或者接了别的 VAE；
2. **CLIP 不对**：SDXL 模型配了 SD1.5 的 clip（或者反过来），出来就是噪点绿图；
3. **cfg / 步数太低**：cfg 1.0 + 4 步那种"过曝白"；
4. **负面词里混了正向内容**；
5. **图片链接指向错的节点**（Pipe 取的是 `SaveImage` 的图，如果工作流里有多个 SaveImage，可能取到中间态）。

**怎么办**

- 先在 ComfyUI 界面里**手动跑一次**这个工作流。界面能出图而 Pipe 出黑图 → 问题在 Pipe 取图；界面也黑 → 问题在工作流；
- 检查 `CheckpointLoaderSimple` 的 MODEL/CLIP/VAE 三根线是否都接上了（尤其是 VAE 那根，最容易忘）；
- 换个已知好的提示词试（`1girl, solo, simple background`），排除提示词问题。

---

## 6. 上传工作流 JSON 后解析失败

**长这样**

前端弹 `Invalid workflow file` / `Failed to parse workflow`；或者没报错但 `POST /prompt` 回

```
{"error": {"type": "prompt_outputs_failed_validation",
           "message": "Prompt outputs failed validation",
           "details": "Node 'X': ..."}}
```

**根因**

1. 传成了**界面格式**（`{"nodes":[...], "links":[...]}`）——该用 API 格式；
2. 工作流里有**本机没装**的自定义节点（某个 `class_type` 在 `/object_info` 里查不到）；
3. JSON 里节点号不是字符串（`1` vs `"1"`）；
4. 复制粘贴时带了 BOM 或中文引号。

**怎么办**

```bash
# 用 ComfyUI 自己校验，报错会精确到节点
curl -X POST http://127.0.0.1:8188/prompt \
  -H "Content-Type: application/json" \
  -d @workflow_api.json
```

- 顶层应该以 `"1": {"class_type": ...}` 开头 → API 格式；
- 拿 `class_type` 去 `curl http://127.0.0.1:8188/object_info | findstr "类名"` 查，查不到就是缺节点；缺的自定义节点要么在 ComfyUI Manager 里装，要么从工作流里删掉；
- 最稳的做法：**只用核心节点**（本仓库的 `workflows/*.json` 全是核心节点，`/prompt` 一次过）。

---

## 7. 一条消息出了好几张图 ⭐

**根因**：Open WebUI 在你要图的同时，还会拿**同一个模型**去跑"生成对话标题""生成标签"两个后台任务。Pipe 没判断任务类型，就也跟着画了。

**怎么办**：Pipe 签名里显式写 `__task__`，一进来就判断：

```python
if __task__:
    yield prompt[:40]
    return
```

注意：用 `**kwargs` 抓不到（Open WebUI 只传签名里写了的参数）。

---

## 8. 手机上打不开 ⭐

**长这样**：电脑好好的，手机一直转圈 / `ERR_CONNECTION_TIMED_OUT` / 能开页面但图片裂了。

见 [`01-deploy.md` 第 7 节](01-deploy.md#7-手机访问) 的排查表。最常见的两个：

- WiFi 被识别成"公用网络"，防火墙规则只勾了"专用"；
- 页面能开但**图裂**：8188 没放行（图是浏览器直连 8188 取的）。

---

## 9. Open WebUI 启动特别慢（100 秒+） ⭐

**日志长这样**

```
httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]
...
429 Too Many Requests  (hf-mirror.com)
Error loading SentenceTransformer
```

**根因**：启动时 `retrieval/utils.py` 想加载 `sentence-transformers/all-MiniLM-L6-v2` 做向量检索，本地缓存不完整 → 去 HuggingFace 下载 → SSL 失败 / 镜像限流 → 重试到超时。

**怎么办**：加环境变量 `OFFLINE_MODE=true` 再启动（会让 HF 直接离线，本地没有就立刻失败，不再重试）。RAG 用不上向量模型，忽略那条 `Error loading SentenceTransformer` 就行。

---

## 10. bat 双击一闪而过 / 报一堆乱码命令 ⭐

**长这样**

```
'i_check' 不是内部或外部命令
'andalone-build'' 不是内部或外部命令
```

**根因**：bat 存成了 **UTF-8 无 BOM + LF**。cmd 要求 GBK（中文系统）+ CRLF，遇到 LF 会按字节切行，把单词从中间劈开。

**怎么办**

- 用 VSCode：右下角编码点成 `GB2312/GBK`，换行符点成 `CRLF`，再保存；
- 或者 python：
  ```python
  open("x.bat", "w", encoding="gbk", newline="").write(src.replace("\r\n", "\n").replace("\n", "\r\n"))
  ```
- 生产小技巧：bat 里只用 ASCII 字符写逻辑，中文提示放 echo 里也要跟着 GBK 一起存。

---

## 11. 图片显示成破图（图裂）

**根因**：消息里存的是 `http://<IP>:8188/view?filename=...`，而这台机器换了 IP、或者 8188 关了。

**怎么办**：切阀门 `IMAGE_URL_MODE=base64`（图直接塞进消息，永不失效，但慢、数据库涨得快）；或者把阀门 `LAN_IP` 固定成一个不怎么会变的地址。

---

## 12. 重置 / 彻底重来

```bat
:: 只清数据，保留程序
rmdir /s /q E:\owui-data

:: 只想重置图像配置：管理员面板 → 设置 → 图像 → 重新填一遍
:: 只想重装 Pipe：函数 → 小鱼生图 → 右上 ⋯ → 删除 → 重新创建
```

ComfyUI 的模型和输出在 `ComfyUI\models\`、`ComfyUI\output\`，删数据目录不会动它们。
