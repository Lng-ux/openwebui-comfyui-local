"""
title: 小鱼生图
author: 小鱼
version: 1.4.0
description: 把 ComfyUI 变成模型下拉里的一项：输入提示词回车即出图，支持多比例与图生图（纯本地）。
repository: https://github.com/Lng-ux/openwebui-comfyui-local
required_open_webui_version: 0.13.0
"""

import asyncio
import base64
import inspect
import os
import random
import re
import time
from typing import AsyncGenerator, Dict, List, Optional
from urllib.parse import urlencode, urlparse

import aiohttp
from pydantic import BaseModel, Field

RATIOS: Dict[str, tuple] = {
    "1x1": (1024, 1024),
    "3x4": (832, 1216),
    "9x16": (768, 1344),
    "16x9": (1344, 768),
}
RATIO_LABEL = {"1x1": "1:1", "3x4": "3:4", "9x16": "9:16", "16x9": "16:9"}

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _slug(name: str) -> str:
    base = re.sub(r"\.(safetensors|ckpt|pt|pth|bin)$", "", name, flags=re.I)
    base = re.sub(r"[^0-9a-zA-Z]+", "_", base).strip("_").lower()
    return base or "model"


class Pipe:
    class Valves(BaseModel):
        COMFYUI_URL: str = Field(
            default="http://127.0.0.1:8188", description="ComfyUI 地址"
        )
        STEPS: int = Field(default=28, description="采样步数")
        CFG: float = Field(default=5.0, description="CFG")
        SAMPLER: str = Field(default="euler_ancestral", description="采样器")
        SCHEDULER: str = Field(default="normal", description="调度器")
        DENOISE: float = Field(default=0.6, description="图生图重绘幅度")
        QUALITY: str = Field(
            default="masterpiece, best quality, newest, absurdres, highres, very aesthetic",
            description="自动追加的正向质量词",
        )
        NEGATIVE: str = Field(
            default="lowres, worst quality, low quality, bad anatomy, bad hands, extra digits, fewer digits, watermark, signature, text, jpeg artifacts, blurry",
            description="负面词（写死，界面不暴露）",
        )
        TIMEOUT: int = Field(default=300, description="单张超时秒数")
        IMAGE_URL_MODE: str = Field(
            default="lan",
            description="lan=用局域网地址引用图片（快、省数据库）；base64=把图片直接塞进消息（慢、但换 IP 也不丢）",
        )
        LAN_IP: str = Field(default="", description="留空=自动探测本机局域网 IP")
        FALLBACK_CHECKPOINTS: str = Field(
            default="NoobAI-XL-v1.1.safetensors",
            description="ComfyUI 连不上时下拉里显示的模型（逗号分隔）",
        )

    def __init__(self):
        self.valves = self.Valves()
        self._ckpt_cache: List[str] = []
        self._ckpt_ts = 0.0

    # ---------------- 模型列表 ----------------
    async def _checkpoints(self) -> List[str]:
        now = time.time()
        if self._ckpt_cache and now - self._ckpt_ts < 60:
            return self._ckpt_cache
        url = self.valves.COMFYUI_URL.rstrip("/")
        got: List[str] = []
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f"{url}/object_info/CheckpointLoaderSimple") as r:
                    data = await r.json()
                ck = data["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
                got = [str(x) for x in ck]
        except Exception:
            got = []
        if not got:
            got = [
                x.strip()
                for x in self.valves.FALLBACK_CHECKPOINTS.split(",")
                if x.strip()
            ]
        self._ckpt_cache, self._ckpt_ts = got, now
        return got

    async def pipes(self) -> List[dict]:
        out = []
        for ck in await self._checkpoints():
            for rk, (w, h) in RATIOS.items():
                out.append(
                    {
                        "id": f"{_slug(ck)}__{rk}",
                        "name": f"🎨 {re.sub(r'[.](safetensors|ckpt)$','',ck,flags=re.I)} · {RATIO_LABEL[rk]}",
                    }
                )
        if not out:
            out = [{"id": "noobai_xl_v1_1__3x4", "name": "🎨 生图 · 3:4"}]
        return out

    # ---------------- 工具函数 ----------------
    def _resolve(self, model_id: str) -> tuple:
        sub = model_id.split(".", 1)[1] if "." in model_id else model_id
        slug, _, ratio = sub.rpartition("__")
        if ratio not in RATIOS:
            slug, ratio = sub, "3x4"
        return slug, ratio

    @staticmethod
    def _text(body: dict) -> str:
        msgs = body.get("messages") or []
        for m in reversed(msgs):
            if m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, list):
                parts = [
                    p.get("text", "")
                    for p in c
                    if isinstance(p, dict) and p.get("type") in (None, "text")
                ]
                return "".join(parts).strip()
        return ""

    async def _file_path(self, f: dict) -> Optional[str]:
        p = f.get("path")
        if p and os.path.exists(p):
            return p
        fid = f.get("id")
        if fid:
            try:
                from open_webui.models.files import Files

                rec = Files.get_file_by_id(fid)
                if inspect.isawaitable(rec):
                    rec = await rec
                if rec and rec.path and os.path.exists(rec.path):
                    return rec.path
            except Exception:
                pass
        return None

    def _wf_txt2img(self, prompt, ckpt, w, h, seed):
        v = self.valves
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": v.NEGATIVE, "clip": ["1", 1]}},
            "8": {"class_type": "CLIPTextEncode", "inputs": {"text": v.QUALITY, "clip": ["1", 1]}},
            "9": {
                "class_type": "ConditioningCombine",
                "inputs": {"conditioning_1": ["2", 0], "conditioning_2": ["8", 0]},
            },
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": int(w), "height": int(h), "batch_size": 1},
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["9", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                    "seed": int(seed),
                    "steps": int(v.STEPS),
                    "cfg": float(v.CFG),
                    "sampler_name": v.SAMPLER,
                    "scheduler": v.SCHEDULER,
                    "denoise": 1.0,
                },
            },
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "owui"}},
        }

    def _wf_img2img(self, prompt, ckpt, w, h, seed, image_name):
        v = self.valves
        wf = self._wf_txt2img(prompt, ckpt, w, h, seed)
        wf["20"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        wf["21"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["20", 0],
                "upscale_method": "lanczos",
                "width": int(w),
                "height": int(h),
                "crop": "center",
            },
        }
        wf["22"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["1", 2]}}
        wf["5"]["inputs"]["latent_image"] = ["22", 0]
        wf["5"]["inputs"]["denoise"] = float(v.DENOISE)
        return wf

    async def _run(self, wf: dict) -> tuple:
        url = self.valves.COMFYUI_URL.rstrip("/")
        timeout = aiohttp.ClientTimeout(total=self.valves.TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(
                f"{url}/prompt", json={"prompt": wf, "client_id": f"owui-{random.randint(0, 1 << 30)}"}
            ) as r:
                if r.status != 200:
                    raise RuntimeError(f"ComfyUI /prompt 返回 {r.status}: {await r.text()}")
                pid = (await r.json())["prompt_id"]
            deadline = time.time() + self.valves.TIMEOUT
            while time.time() < deadline:
                await asyncio.sleep(0.4)
                async with s.get(f"{url}/history/{pid}") as r:
                    hist = await r.json()
                if pid in hist:
                    entry = hist[pid]
                    status = entry.get("status", {})
                    if status.get("status_str") == "error":
                        msgs = status.get("messages", [])
                        raise RuntimeError(f"ComfyUI 执行出错: {str(msgs)[:500]}")
                    outs = entry.get("outputs", {})
                    for node in outs.values():
                        imgs = node.get("images") or []
                        if imgs:
                            im = imgs[0]
                            q = {
                                "filename": im["filename"],
                                "subfolder": im.get("subfolder", ""),
                                "type": im.get("type", "output"),
                            }
                            async with s.get(f"{url}/view", params=q) as r2:
                                return await r2.read(), q
            raise RuntimeError("ComfyUI 出图超时")
        raise RuntimeError("unreachable")

    def _lan_ip(self) -> str:
        if self.valves.LAN_IP.strip():
            return self.valves.LAN_IP.strip()
        ip = ""
        try:
            import socket

            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sk.connect(("223.5.5.5", 80))
                ip = sk.getsockname()[0]
            finally:
                sk.close()
        except Exception:
            ip = ""
        return ip or "127.0.0.1"


    # ---------------- 主入口 ----------------
    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __files__: Optional[list] = None,
        __event_emitter__=None,
        __task__: Optional[str] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        async def status(desc, done=False):
            if __event_emitter__:
                try:
                    await __event_emitter__(
                        {"type": "status", "data": {"description": desc, "done": done}}
                    )
                except Exception:
                    pass

        model_id = body.get("model", "")
        slug, ratio = self._resolve(model_id)
        w, h = RATIOS[ratio]

        prompt = self._text(body)

        # 前台的后台任务（生成标题/标签等）不要真的去跑图
        task = str(__task__ or "")
        if task:
            head = re.sub(r"\s+", " ", prompt).strip()[:40] or "图片"
            yield head
            return

        if not prompt:
            yield "在下面输入框里写提示词，回车就出图。"
            return

        known = await self._checkpoints()
        ckpt = next((c for c in known if _slug(c) == slug), known[0])

        ref = None
        for f in __files__ or []:
            fn = str(f.get("filename") or f.get("name") or "")
            if fn.lower().endswith(_IMG_EXT):
                ref = await self._file_path(f)
                if ref:
                    break

        seed = random.randint(1, 2**31 - 1)
        t0 = time.time()
        mode = "图生图" if ref else "文生图"
        await status(f"{mode}中 · {ckpt} · {RATIO_LABEL[ratio]} · {self.valves.STEPS}步")
        yield f"> 🎨 {mode} · `{ckpt}` · {RATIO_LABEL[ratio]} · {self.valves.STEPS} 步 · seed `{seed}`\n\n"

        url = self.valves.COMFYUI_URL.rstrip("/")
        if ref:
            try:
                timeout = aiohttp.ClientTimeout(total=120)
                async with aiohttp.ClientSession(timeout=timeout) as s:
                    form = aiohttp.FormData()
                    with open(ref, "rb") as fh:
                        form.add_field(
                            "image",
                            fh.read(),
                            filename=os.path.basename(ref),
                            content_type="application/octet-stream",
                        )
                    form.add_field("type", "input")
                    async with s.post(f"{url}/api/upload/image", data=form) as r:
                        if r.status != 200:
                            raise RuntimeError(f"参考图上传失败 {r.status}")
                        up = await r.json()
                        image_name = up.get("name") or up.get("filename")
                wf = self._wf_img2img(prompt, ckpt, w, h, seed, image_name)
            except Exception as e:
                yield f"❌ 参考图处理失败：`{e}`"
                await status("失败", True)
                return
        else:
            wf = self._wf_txt2img(prompt, ckpt, w, h, seed)

        try:
            png, q = await self._run(wf)
        except Exception as e:
            yield f"❌ 出图失败：`{e}`"
            await status("失败", True)
            return

        dt = time.time() - t0
        await status(f"完成 · {dt:.1f}s", True)
        if str(self.valves.IMAGE_URL_MODE).lower() == "base64":
            b64 = base64.b64encode(png).decode()
            yield f"\n![image](data:image/png;base64,{b64})\n\n"
        else:
            parsed = urlparse(self.valves.COMFYUI_URL)
            host = self._lan_ip()
            port = parsed.port or (443 if parsed.scheme == "https" else 8188)
            link = f"http://{host}:{port}/view?{urlencode(q)}"
            yield f"\n![image]({link})\n\n"
        yield f"*{dt:.1f} 秒 · {w}×{h} · seed `{seed}`*"
