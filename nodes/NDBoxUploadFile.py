"""
NDBox_UploadFiles Node - upload local files from web UI to
ComfyUI input/output directories, then output saved path.
"""

import os
import uuid
from typing import Tuple

import folder_paths
from aiohttp import web
from server import PromptServer


FILE_TYPE_ACCEPTS = {
    "any":  "*/*",
    "txt":  ".txt",
    "csv":  ".csv",
    "json": ".json",
    "npz":  ".npz",
    "npy":  ".npy",
    "bvh":  ".bvh",
    "fbx":  ".fbx",
    "obj":  ".obj",
    "glb":  ".glb",
    "png":  ".png",
    "jpg":  ".jpg",
    "jpeg": ".jpeg",
    "webp": ".webp",
    "gif":  ".gif",
    "bmp":  ".bmp",
}
UPLOAD_TARGETS = ("input", "output")

ROOT_SUBDIR_MARKER = "(input根目录)"
MAX_LIST_FILES = 200


def _safe_upload_name(filename: str) -> str:
    name = os.path.basename(filename or "").strip()
    if not name:
        name = f"upload_{uuid.uuid4().hex}"
    return name.replace("\\", "_").replace("/", "_")


def _is_extension_allowed(filename: str, file_type: str) -> bool:
    t = (file_type or "any").strip().lower()
    if t == "any":
        return True
    return (filename or "").lower().endswith(f".{t}")


def _filter_history_files_by_type(names, file_type: str):
    t = (file_type or "any").strip().lower()
    if t == "any":
        return names
    return [n for n in names if (n or "").lower().endswith(f".{t}")]


def _get_history_files(file_type: str):
    """扫描 input / output 下最多 3 层的所有文件，供 file_name 下拉框使用。"""
    files = []
    for base_dir in (folder_paths.get_input_directory(),
                     folder_paths.get_output_directory()):
        if not os.path.isdir(base_dir):
            continue
        try:
            for root, dirs, filenames in os.walk(base_dir):
                rel = os.path.relpath(root, base_dir)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
                if depth > 3:
                    dirs[:] = []
                    continue
                for name in filenames:
                    if depth == 0:
                        files.append(name)
                    else:
                        rel_name = os.path.relpath(
                            os.path.join(root, name), base_dir
                        ).replace("\\", "/")
                        files.append(rel_name)
        except Exception:
            pass

    files = _filter_history_files_by_type(files, file_type)
    uniq = []
    for name in (["none"] + files):
        if name not in uniq:
            uniq.append(name)
    return uniq


def _normalize_input_subdir(path_value: str) -> str:
    """规范化子目录；返回空字符串表示根目录。"""
    raw = (path_value or "").strip().replace("\\", "/").strip("/")
    if not raw or raw == ROOT_SUBDIR_MARKER:
        return ""
    normalized = os.path.normpath(raw).replace("\\", "/").strip("/")
    if normalized in ("", "."):
        return ""
    if normalized.startswith("..") or "/../" in f"/{normalized}/":
        return ""
    return normalized


def _list_subdirs(target: str = "input"):
    """动态扫描指定根目录下所有子目录；首项固定为根目录标记。"""
    if target == "output":
        base = folder_paths.get_output_directory()
    else:
        base = folder_paths.get_input_directory()
    base = os.path.abspath(base)

    candidates = set()
    try:
        for root, dirs, _files in os.walk(base):
            rel = os.path.relpath(root, base).replace("\\", "/")
            if rel == ".":
                continue
            depth = rel.count("/") + 1
            if depth <= 3:
                candidates.add(rel)
            else:
                dirs[:] = []
    except Exception:
        pass
    return [ROOT_SUBDIR_MARKER] + sorted(candidates)


def _list_input_subdirs():
    return _list_subdirs("input")


def _safe_resolve_dir(upload_target: str, upload_subdir: str):
    """返回 (base_abs, target_abs)；target 不合法时返回 (None, None)。"""
    target = (upload_target or "input").strip().lower()
    if target not in UPLOAD_TARGETS:
        return None, None
    base_dir = (folder_paths.get_output_directory() if target == "output"
                else folder_paths.get_input_directory())
    base_abs = os.path.abspath(base_dir).replace("\\", "/")

    subdir = _normalize_input_subdir(upload_subdir)
    target_abs = os.path.join(base_abs, subdir) if subdir else base_abs
    target_abs = os.path.abspath(target_abs).replace("\\", "/")

    if not (target_abs == base_abs or target_abs.startswith(base_abs + "/")):
        return None, None
    return base_abs, target_abs


def _resolve_selected_file_path(upload_target: str,
                                upload_subdir: str,
                                file_name: str) -> str:
    target = (upload_target or "input").strip().lower()
    subdir = _normalize_input_subdir(upload_subdir)
    name = (file_name or "").strip().replace("\\", "/")
    if not name or name == "none":
        return ""

    if "/" in name:
        return name

    base_abs, target_abs = _safe_resolve_dir(target, subdir)
    if base_abs is None:
        return name

    candidate = os.path.join(target_abs, name)
    if os.path.exists(candidate):
        return (f"{target}/{subdir}/{name}" if subdir else f"{target}/{name}").replace("\\", "/")

    for base, kind in ((folder_paths.get_input_directory(), "input"),
                       (folder_paths.get_output_directory(), "output")):
        for root, _dirs, filenames in os.walk(base):
            if name in filenames:
                full = os.path.join(root, name)
                base_a = os.path.abspath(base).replace("\\", "/")
                full_a = os.path.abspath(full).replace("\\", "/")
                if full_a.startswith(base_a + "/"):
                    rel = full_a[len(base_a) + 1:]
                    return f"{kind}/{rel}"
    return name


# ---------------------------------------------------------------------------
# POST /NDBox/upload_files
# ---------------------------------------------------------------------------
@PromptServer.instance.routes.post("/NDBox/upload_files")
async def NDBox_upload_npz(request):
    try:
        reader = await request.multipart()
        file_type = "any"
        upload_target = "input"
        upload_subdir_raw = ROOT_SUBDIR_MARKER
        original_name = ""
        file_bytes = bytearray()

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file" and part.filename:
                original_name = part.filename
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    file_bytes.extend(chunk)
            elif part.name == "file_type":
                file_type = (await part.text()).strip().lower() or "any"
            elif part.name == "upload_target":
                upload_target = (await part.text()).strip().lower() or "input"
            elif part.name == "upload_subdir":
                upload_subdir_raw = (await part.text()).strip()

        if not original_name:
            return web.json_response({"ok": False, "error": "No file provided."}, status=400)
        if len(file_bytes) == 0:
            return web.json_response({"ok": False, "error": "Uploaded file is empty."}, status=400)
        if file_type not in FILE_TYPE_ACCEPTS:
            return web.json_response({"ok": False, "error": f"Unsupported file_type: {file_type}"}, status=400)
        if upload_target not in UPLOAD_TARGETS:
            return web.json_response({"ok": False, "error": f"Unsupported upload_target: {upload_target}"}, status=400)
        if not _is_extension_allowed(original_name, file_type):
            return web.json_response(
                {"ok": False, "error": f"File extension does not match selected type: {file_type}"},
                status=400,
            )

        base_abs, target_abs = _safe_resolve_dir(upload_target, upload_subdir_raw)
        if base_abs is None:
            return web.json_response({"ok": False, "error": "Invalid subdir."}, status=400)
        os.makedirs(target_abs, exist_ok=True)

        safe_name = _safe_upload_name(original_name)
        final_path = os.path.join(target_abs, safe_name)
        if os.path.exists(final_path):
            stem, ext = os.path.splitext(safe_name)
            safe_name = f"{stem}_{uuid.uuid4().hex[:8]}{ext}"
            final_path = os.path.join(target_abs, safe_name)

        with open(final_path, "wb") as f:
            f.write(file_bytes)

        subdir = _normalize_input_subdir(upload_subdir_raw)
        rel_path = (f"{upload_target}/{subdir}/{safe_name}"
                    if subdir else f"{upload_target}/{safe_name}").replace("\\", "/")

        return web.json_response({"ok": True, "file_path": rel_path, "size": len(file_bytes)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# GET /NDBox/list_uploaded_files   （file_name 下拉框数据源）
# ---------------------------------------------------------------------------
@PromptServer.instance.routes.get("/NDBox/list_uploaded_files")
async def NDBox_list_uploaded_files(request):
    try:
        file_type = (request.query.get("file_type", "any") or "any").strip().lower()
        if file_type not in FILE_TYPE_ACCEPTS:
            return web.json_response({"ok": False, "error": f"Unsupported file_type: {file_type}"}, status=400)
        return web.json_response({"ok": True, "files": _get_history_files(file_type)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# GET /NDBox/list_input_subdirs
# ---------------------------------------------------------------------------
@PromptServer.instance.routes.get("/NDBox/list_input_subdirs")
async def NDBox_list_input_subdirs(request):
    try:
        target = (request.query.get("upload_target", "input") or "input").strip().lower()
        if target not in UPLOAD_TARGETS:
            target = "input"
        return web.json_response({"ok": True, "subdirs": _list_subdirs(target)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# GET /NDBox/list_files_in_subdir
# ---------------------------------------------------------------------------
@PromptServer.instance.routes.get("/NDBox/list_files_in_subdir")
async def NDBox_list_files_in_subdir(request):
    try:
        upload_target = (request.query.get("upload_target", "input") or "input").strip().lower()
        upload_subdir = request.query.get("upload_subdir", "") or ""
        file_type = (request.query.get("file_type", "any") or "any").strip().lower()

        if upload_target not in UPLOAD_TARGETS:
            return web.json_response({"ok": False, "error": "Unsupported upload_target"}, status=400)

        base_abs, target_abs = _safe_resolve_dir(upload_target, upload_subdir)
        if base_abs is None:
            return web.json_response({"ok": False, "error": "Invalid subdir"}, status=400)

        subdir = _normalize_input_subdir(upload_subdir)
        result = {
            "ok": True,
            "target": upload_target,
            "subdir": subdir,
            "count": 0,
            "truncated": False,
            "total": 0,
            "files": [],
        }

        if not os.path.isdir(target_abs):
            return web.json_response(result)

        items = []
        for name in os.listdir(target_abs):
            full = os.path.join(target_abs, name)
            if not os.path.isfile(full):
                continue
            if file_type != "any" and not name.lower().endswith(f".{file_type}"):
                continue
            try:
                st = os.stat(full)
            except Exception:
                continue
            items.append({
                "name": name,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "rel_path": (f"{upload_target}/{subdir}/{name}" if subdir
                             else f"{upload_target}/{name}").replace("\\", "/"),
            })

        items.sort(key=lambda x: x["mtime"], reverse=True)
        total = len(items)
        truncated = total > MAX_LIST_FILES
        items = items[:MAX_LIST_FILES]

        result.update({
            "count": len(items),
            "total": total,
            "truncated": truncated,
            "files": items,
        })
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# POST /NDBox/delete_files
# ---------------------------------------------------------------------------
@PromptServer.instance.routes.post("/NDBox/delete_files")
async def NDBox_delete_files(request):
    try:
        data = await request.json()
        upload_target = (data.get("upload_target") or "input").strip().lower()
        upload_subdir = data.get("upload_subdir") or ""
        names = data.get("names") or []

        if upload_target not in UPLOAD_TARGETS:
            return web.json_response({"ok": False, "error": "Unsupported upload_target"}, status=400)
        if not isinstance(names, list):
            return web.json_response({"ok": False, "error": "names must be a list"}, status=400)

        base_abs, target_abs = _safe_resolve_dir(upload_target, upload_subdir)
        if base_abs is None:
            return web.json_response({"ok": False, "error": "Invalid subdir"}, status=400)

        deleted, failed = [], []
        for raw in names:
            name = str(raw or "").strip()
            safe = os.path.basename(name)
            if not safe or safe != name or "/" in name or "\\" in name or ".." in name:
                failed.append({"name": name, "reason": "invalid name"})
                continue
            full = os.path.join(target_abs, safe)
            full_abs = os.path.abspath(full).replace("\\", "/")
            if not (full_abs.startswith(target_abs + "/")):
                failed.append({"name": name, "reason": "out of bounds"})
                continue
            if not os.path.isfile(full_abs):
                failed.append({"name": name, "reason": "not found"})
                continue
            try:
                os.remove(full_abs)
                deleted.append(safe)
            except Exception as e:
                failed.append({"name": name, "reason": str(e)})

        return web.json_response({"ok": True, "deleted": deleted, "failed": failed})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# POST /NDBox/clear_subdir
# ---------------------------------------------------------------------------
@PromptServer.instance.routes.post("/NDBox/clear_subdir")
async def NDBox_clear_subdir(request):
    try:
        data = await request.json()
        upload_target = (data.get("upload_target") or "input").strip().lower()
        upload_subdir = data.get("upload_subdir") or ""

        if upload_target not in UPLOAD_TARGETS:
            return web.json_response({"ok": False, "error": "Unsupported upload_target"}, status=400)

        base_abs, target_abs = _safe_resolve_dir(upload_target, upload_subdir)
        if base_abs is None:
            return web.json_response({"ok": False, "error": "Invalid subdir"}, status=400)

        deleted_count = 0
        if os.path.isdir(target_abs):
            for name in os.listdir(target_abs):
                full = os.path.join(target_abs, name)
                if not os.path.isfile(full):
                    continue
                try:
                    os.remove(full)
                    deleted_count += 1
                except Exception:
                    pass

        return web.json_response({"ok": True, "deleted_count": deleted_count})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# GET /NDBox/resolve_file_path
# ---------------------------------------------------------------------------
@PromptServer.instance.routes.get("/NDBox/resolve_file_path")
async def NDBox_resolve_file_path(request):
    try:
        upload_target = (request.query.get("upload_target", "input") or "input").strip().lower()
        upload_subdir = request.query.get("upload_subdir", ROOT_SUBDIR_MARKER) or ROOT_SUBDIR_MARKER
        file_name = request.query.get("file_name", "none") or "none"
        path = _resolve_selected_file_path(upload_target, upload_subdir, file_name)
        return web.json_response({"ok": True, "file_path": path})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# 节点定义
# ---------------------------------------------------------------------------
class NDBox_UploadFiles:
    @classmethod
    def INPUT_TYPES(cls):
        default_type = "any"
        file_files = _get_history_files(default_type)
        return {
            "required": {
                "upload_target": (
                    list(UPLOAD_TARGETS),
                    {"default": "input", "tooltip": "上传目标根目录：input 或 output。"},
                ),
                "upload_subdir": (
                    _list_subdirs("input"),
                    {"default": ROOT_SUBDIR_MARKER,
                     "tooltip": "仅 upload_target=input 时生效。选择 (input根目录) 表示上传到 input 根目录。"},
                ),
                "file_type": (
                    list(FILE_TYPE_ACCEPTS.keys()),
                    {"default": default_type, "tooltip": "上传文件类型过滤；any 代表允许任意后缀。"},
                ),
                "file_name": (
                    file_files,
                    {"tooltip": "历史文件下拉列表（input/output 下最多 3 层）。"},
                ),
                "file_path": (
                    "STRING",
                    {"default": "", "multiline": False,
                     "tooltip": "上传后自动写入。若为空，则使用 file_name 解析。"},
                ),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("file_path",)
    FUNCTION = "get_file_path"
    CATEGORY = "Sundaybox/Tools"
    OUTPUT_NODE = True

    def get_file_path(self, *args, **kwargs) -> Tuple[str]:
        upload_target = "input"
        upload_subdir = ROOT_SUBDIR_MARKER
        file_type = "any"
        file_name = "none"
        file_path = ""

        if len(args) == 2:
            file_name, file_path = args
        elif len(args) >= 3:
            if len(args) >= 5:
                upload_target, upload_subdir, file_type, file_name, file_path = args[:5]
            else:
                file_type, file_name, file_path = args[:3]

        upload_target = kwargs.get("upload_target", upload_target)
        upload_subdir = kwargs.get("upload_subdir", upload_subdir)
        file_type = kwargs.get("file_type", file_type)
        file_name = kwargs.get("file_name", file_name)
        file_path = kwargs.get("file_path", file_path)

        _ = (file_type,)

        path = (file_path or "").strip().replace("\\", "/")

        if not path and file_name and file_name != "none":
            path = _resolve_selected_file_path(upload_target, upload_subdir, file_name)

        return {
            "ui": {"file_path": [path], "text": [path]},
            "result": (path,),
        }


WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS = {
    "NDBox_UploadFiles": NDBox_UploadFiles,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "NDBox_UploadFiles": "NDBox Upload Files",
}
