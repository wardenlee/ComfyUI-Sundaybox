import { app } from "../../scripts/app.js";

const NODE_NAME = "NDBox_UploadFiles";
const HIDE_WIDGETS = ["upload_target", "upload_subdir", "file_type"];

// 隐藏原生 widget，但仍保留序列化值
function hideWidget(widget) {
    if (!widget) return;
    widget.computeSize = () => [0, -4];
    widget.type = "hidden";
    if (widget.element) widget.element.style.display = "none";
    if (widget.inputEl) widget.inputEl.style.display = "none";
    const origSerialize = widget.serializeValue;
    widget.serializeValue = function () {
        return origSerialize ? origSerialize.apply(this, arguments) : this.value;
    };
}

async function refreshFileNameWidget(node) {
    const w = (node.widgets || []).find(x => x.name === "file_name");
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

        const oldValue = w.value;
        if (!data.files.includes(oldValue)) {
            w.value = "none";
            if (w.callback) w.callback("none");
        }
        app.graph.setDirtyCanvas(true, true);
    } catch (e) {
        console.warn("[NDBox] refresh file_name failed:", e);
    }
}

function findNode() {
    const nodes = (app.graph && app.graph._nodes) || [];
    const matches = nodes.filter(n => n.comfyClass === NODE_NAME);
    return matches.length > 0 ? matches[0] : null;
}

function getWidget(node, name) {
    return (node.widgets || []).find(w => w.name === name);
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

app.registerExtension({
    name: "NDBox.UploadFile.Enhancer",
    async nodeCreated(node) {
        if (node.comfyClass !== NODE_NAME) return;
        for (const name of HIDE_WIDGETS) {
            hideWidget(getWidget(node, name));
        }
    },
});

window.addEventListener("message", (event) => {
    const data = event.data || {};
    if (!data || typeof data !== "object") return;

    const node = findNode();
    if (!node) return;

    switch (data.type) {
        case "ndbox_files_uploaded":
            if (data.filePath) {
                setWidgetValue(node, "file_path", data.filePath);
            }
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

        case "ndbox_files_iframe_ready": {
            // iframe 就绪，推送一次初始状态
            const iframe = document.querySelector('iframe[src*="NDBoxUploadFile"]');
            if (!iframe || !iframe.contentWindow) break;
            const state = {
                type: "ndbox_files_state",
                uploadTarget: getWidget(node, "upload_target")?.value || "input",
                uploadSubdir: getWidget(node, "upload_subdir")?.value || "(input根目录)",
                fileType: getWidget(node, "file_type")?.value || "any",
                filePath: getWidget(node, "file_path")?.value || "",
            };
            iframe.contentWindow.postMessage(state, "*");
            break;
        }
        default:
            break;
    }
});
