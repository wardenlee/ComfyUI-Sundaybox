import { app } from "../../scripts/app.js";

const NODE_NAME = "NDBox_UploadFiles";
const HIDE_WIDGETS = ["upload_target", "upload_subdir", "file_type"];
// 基于当前 JS 文件位置解析 HTML 路径，避免硬编码出错
const IFRAME_SRC = new URL("./NDBoxUploadFile.html", import.meta.url).href;
const NODE_MIN_WIDTH = 480;
const NODE_MIN_HEIGHT = 640;
const IFRAME_HEIGHT = 480;

// ---------------- 工具 ----------------
function getWidget(node, name) {
    return (node.widgets || []).find(w => w.name === name);
}

function hideWidget(widget) {
    if (!widget) return;
    widget.computeSize = () => [0, -4];
    widget.type = "hidden";
    if (widget.element) widget.element.style.display = "none";
    if (widget.inputEl) widget.inputEl.style.display = "none";
    const orig = widget.serializeValue;
    widget.serializeValue = function () {
        return orig ? orig.apply(this, arguments) : this.value;
    };
}

function setWidgetValue(node, name, value) {
    const w = getWidget(node, name);
    if (!w) return;
    w.value = value;
    if (w.callback) {
        try { w.callback(value); } catch (e) { /* ignore */ }
    }
    app.graph.setDirtyCanvas(true, true);
}

// ---------------- file_name 下拉框同步 ----------------
async function refreshFileNameWidget(node) {
    const w = getWidget(node, "file_name");
    if (!w) return;
    try {
        const resp = await fetch("/NDBox/list_uploaded_files?file_type=any");
        const data = await resp.json();
        if (!data || !data.ok || !Array.isArray(data.files)) return;

        if (w.options && Array.isArray(w.options.values)) {
            w.options.values.length = 0;
            for (const v of data.files) w.options.values.push(v);
        } else {
            w.options = w.options || {};
            w.options.values = data.files.slice();
        }
        if (!data.files.includes(w.value)) {
            w.value = "none";
            if (w.callback) w.callback("none");
        }
        app.graph.setDirtyCanvas(true, true);
    } catch (e) {
        console.warn("[NDBox] refresh file_name failed:", e);
    }
}

// ---------------- 创建 iframe ----------------
function buildIframe(node) {
    const iframe = document.createElement("iframe");
    iframe.src = IFRAME_SRC;
    iframe.style.width = "100%";
    iframe.style.height = "100%";
    iframe.style.border = "0";
    iframe.style.borderRadius = "4px";
    iframe.style.background = "#1a1a1a";
    node._ndboxIframe = iframe;

    let added = false;

    // 新版本 ComfyUI 支持 addDOMWidget
    if (typeof node.addDOMWidget === "function") {
        try {
            const w = node.addDOMWidget("ndbox_upload_ui", "div", iframe, {
                serialize: false,
                hideOnZoom: false,
                getValue: () => "",
                setValue: () => {},
            });
            if (w) {
                w.computeSize = (width) => [width, IFRAME_HEIGHT];
                added = true;
            }
        } catch (e) { /* fallthrough */ }
    }

    // 老版本 fallback：往 widget 的 element 里塞 iframe
    if (!added) {
        const w = node.addWidget("text", "ndbox_upload_ui", "", () => {}, {});
        if (w && w.element) {
            w.element.innerHTML = "";
            w.element.style.padding = "0";
            w.element.style.height = IFRAME_HEIGHT + "px";
            w.element.appendChild(iframe);
            w.computeSize = (width) => [width, IFRAME_HEIGHT];
        }
    }

    iframe.addEventListener("load", () => {
        pushStateToIframe(node);
    });
}

// ---------------- 向 iframe 推送状态 ----------------
function pushStateToIframe(node) {
    const iframe = node._ndboxIframe;
    if (!iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage({
        type: "ndbox_files_state",
        uploadTarget: getWidget(node, "upload_target")?.value || "input",
        uploadSubdir: getWidget(node, "upload_subdir")?.value || "(input根目录)",
        fileType: getWidget(node, "file_type")?.value || "any",
        filePath: getWidget(node, "file_path")?.value || "",
    }, "*");
}

// ---------------- 按 event.source 精确定位节点 ----------------
function findNodeByEventSource(event) {
    const nodes = (app.graph && app.graph._nodes) || [];
    for (const node of nodes) {
        if (node.comfyClass !== NODE_NAME) continue;
        const iframe = node._ndboxIframe;
        if (iframe && iframe.contentWindow === event.source) return node;
    }
    return null;
}

// ---------------- 注册扩展 ----------------
app.registerExtension({
    name: "NDBox.UploadFile.Enhancer",
    async nodeCreated(node) {
        if (node.comfyClass !== NODE_NAME) return;

        for (const name of HIDE_WIDGETS) {
            hideWidget(getWidget(node, name));
        }

        buildIframe(node);

        try {
            const w = Math.max(node.size?.[0] || NODE_MIN_WIDTH, NODE_MIN_WIDTH);
            node.setSize([w, NODE_MIN_HEIGHT]);
        } catch (e) { /* ignore */ }
    },
});

// ---------------- 接收 iframe 消息 ----------------
window.addEventListener("message", (event) => {
    const data = event.data || {};
    if (!data || typeof data !== "object" || typeof data.type !== "string") return;
    if (!data.type.startsWith("ndbox_")) return;

    const node = findNodeByEventSource(event);
    if (!node) return;

    switch (data.type) {
        case "ndbox_files_uploaded":
            if (data.filePath) setWidgetValue(node, "file_path", data.filePath);
            refreshFileNameWidget(node);
            break;

        case "ndbox_file_path_cleared":
            setWidgetValue(node, "file_path", "");
            break;

        case "ndbox_files_changed":
            refreshFileNameWidget(node);
            break;

        case "ndbox_files_file_type_changed":
            setWidgetValue(node, "file_type", data.fileType);
            break;

        case "ndbox_files_upload_target_changed":
            setWidgetValue(node, "upload_target", data.uploadTarget);
            break;

        case "ndbox_files_upload_subdir_changed":
            setWidgetValue(node, "upload_subdir", data.uploadSubdir);
            break;

        case "ndbox_files_iframe_ready":
            pushStateToIframe(node);
            break;

        default:
            break;
    }
});
