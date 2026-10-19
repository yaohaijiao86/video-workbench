const $ = id => document.getElementById(id);
const UI_STATE_KEY = "video-workbench-ui-state-v1";
let current = null;
let currentBatch = "";
let batches = [];
let projects = [];
let taskControl = {running:false,paused:false,pause_pending:false};
let selectedOutputMode = "data";
let renderedSuggestionsKey = "";
let lastStatus = "";
let currentSegments = [];
let currentTranscriptLayout = null;
let currentSuggestions = [];
let currentMediaDuration = 0;
let currentAnalysisDone = false;
let interactionBusy = false;
let suppressTranscriptClickUntil = 0;
const autoFormatRetryAt = new Map();
const scannedModelsByProfile = new Map();
let activeModelRole = "editing";
let folderCurrent = "";
let folderParent = "";
let folderTargetId = "";
let restoringUIState = false;
let requiredInitialSetup = false;
let bilibiliQrLoginId = "";
let bilibiliQrExpiresAt = 0;
let bilibiliQrTimer = 0;
if ("scrollRestoration" in history) history.scrollRestoration = "manual";

function readUIState() {
  try {
    const value = JSON.parse(localStorage.getItem(UI_STATE_KEY) || "null");
    return value && typeof value === "object" ? value : null;
  } catch { return null; }
}
function saveUIState() {
  if (restoringUIState) return;
  try {
    localStorage.setItem(UI_STATE_KEY, JSON.stringify({
      page: $("settings_page").hidden ? "workspace" : "settings",
      projectId: current || "",
      batchId: currentBatch || $("batch_select").value || "",
      recentOpen: document.querySelector("details.recent")?.open === true,
      importOpen: $("import_panel").getAttribute("aria-expanded") === "true",
      batchOpen: $("batch_content").hidden === false,
      scrollY: window.scrollY
    }));
  } catch { /* Browser storage may be disabled; the workbench still functions. */ }
}
function setImportPanelOpen(open) {
  const panel = $("import_panel");
  const expanded = !!open;
  panel.setAttribute("aria-expanded", String(expanded));
  $("import_content").hidden = !expanded;
  saveUIState();
}
function setBatchPanelOpen(open) {
  const expanded = !!open;
  $("batch_toggle").setAttribute("aria-expanded", String(expanded));
  $("batch_content").hidden = !expanded;
  saveUIState();
}
let saveScrollTimer = 0;
window.addEventListener("scroll", () => {
  clearTimeout(saveScrollTimer);
  saveScrollTimer = setTimeout(saveUIState, 150);
}, {passive:true});
window.addEventListener("pagehide", saveUIState);

async function request(url, method = "GET", data = null, headers = {}) {
  const opt = {method, headers: {...headers}};
  if (data !== null) {
    opt.body = data instanceof Blob ? data : JSON.stringify(data);
    if (!(data instanceof Blob)) opt.headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, opt);
  if (response.status === 401) {
    window.location.replace("/");
    throw Error("登录已失效，请重新登录");
  }
  const body = await response.text();
  let value;
  try { value = JSON.parse(body); } catch { value = body; }
  if (!response.ok) throw Error(value.error || "请求失败");
  return value;
}
function notice(message) {
  $("message").textContent = message;
  setTimeout(() => { if ($("message").textContent === message) $("message").textContent = ""; }, 7000);
}
function safe(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function currentModelProfiles() {
  return [...document.querySelectorAll("#model_profiles .model-profile-row")].map(row => ({
    id: row.dataset.profileId,
    name: row.querySelector("[data-profile-name]").value.trim(),
    model: row.querySelector("[data-profile-model]").value.trim(),
    provider: row.querySelector("[data-profile-provider]").value,
    base_url: row.querySelector("[data-profile-base]").value.trim(),
    api_key: row.querySelector("[data-profile-key]").value,
    api_key_set: row.dataset.apiKeySet === "true",
    local_model_lifecycle: row.querySelector("[data-profile-lifecycle]").value
  }));
}
function currentModelRoles() {
  return Object.fromEntries(["editing","summary","vision"].map(role => [role,
    [...document.querySelectorAll('[data-role-list="' + role + '"] .model-role-item')].map(row => ({
      profile_id: row.dataset.profileId, model: row.dataset.modelId
    }))]));
}
function renderModelChoices(profiles, roles = {}) {
  const container = $("model_profiles");
  container.innerHTML = profiles.length ? profiles.map((row,index) =>
    '<article class="model-profile-row" data-profile-id="' + safe(row.id) + '" data-api-key-set="' + (row.api_key_set ? "true" : "false") + '">' +
    '<div class="profile-head"><strong>模型服务 ' + (index + 1) + '</strong><div class="profile-actions">' +
    '<button type="button" class="secondary" data-profile-up title="上移"' + (index ? "" : " disabled") + '>↑</button>' +
    '<button type="button" class="secondary" data-profile-down title="下移"' + (index < profiles.length - 1 ? "" : " disabled") + '>↓</button>' +
    '<button type="button" class="secondary" data-profile-remove>删除</button></div></div>' +
    '<div class="profile-fields"><label>配置名称<input data-profile-name aria-label="模型配置名称" placeholder="例如：本机推理服务" value="' + safe(row.name || "") + '"></label>' +
    '<label>模型服务类型<select data-profile-provider><option value="local"' + (row.provider !== "online" ? " selected" : "") + '>本地或局域网模型</option><option value="online"' + (row.provider === "online" ? " selected" : "") + '>在线模型（OpenAI 兼容）</option></select></label>' +
    '<label class="profile-url">模型 API 地址<input data-profile-base autocomplete="url" placeholder="' + (row.provider === "online" ? "https://api.example.com/v1" : "http://127.0.0.1:8787/v1") + '" value="' + safe(row.base_url || "") + '"></label>' +
    '<label>API Key<input data-profile-key type="password" autocomplete="new-password" placeholder="' + (row.api_key_set ? "已设置，留空保持现值" : row.provider === "online" ? "填写服务商 API Key" : "服务需要认证时填写") + '" value="' + safe(row.api_key || "") + '"></label>' +
    '<label class="profile-lifecycle"' + (row.provider === "online" ? " hidden" : "") + '>本地服务任务结束后<select data-profile-lifecycle><option value="manual"' + (row.local_model_lifecycle !== "gateway" ? " selected" : "") + '>手动管理模型</option><option value="gateway"' + (row.local_model_lifecycle === "gateway" ? " selected" : "") + '>自建 8787/9990 网关：停止推理引擎</option></select><small>仅用于该服务地址主机上的本项目网关；停止引擎可能影响同机其他调用。</small></label>' +
    '<label class="profile-model">默认模型 ID（可选）<input data-profile-model list="model_options_' + safe(row.id) + '" aria-label="默认模型 ID" placeholder="扫描后添加多个模型，也可手动填写" value="' + safe(row.model || "") + '"><datalist id="model_options_' + safe(row.id) + '">' + (scannedModelsByProfile.get(row.id) || []).map(model => '<option value="' + safe(model.id) + '" label="' + safe(model.id + (model.active ? "（运行中）" : "")) + '"></option>').join("") + '</datalist></label>' +
    '<div class="profile-scan"><button type="button" class="secondary" data-profile-scan>扫描模型</button><span data-scan-status class="hint">' + (scannedModelsByProfile.has(row.id) ? '已扫描 ' + scannedModelsByProfile.get(row.id).length + ' 个可调用模型' : '扫描此服务返回的可调用模型') + '</span></div></div></article>').join("")
    : '<p class="hint">尚未添加模型服务。点击“添加模型服务”开始。</p>';
  renderAvailableModels(profiles);
  renderModelRoleLists(profiles, roles || {});
}
function normalizeModelRoleItems(role, profiles, selected) {
  const items = Array.isArray(selected[role]) ? selected[role] : [];
  return items.map(item => {
    if (item && typeof item === "object") return {profile_id:String(item.profile_id || item.id || ""), model:String(item.model || "")};
    const profile = profiles.find(row => row.id === String(item));
    return profile ? {profile_id:profile.id, model:String(profile.model || "")} : null;
  }).filter(item => item && item.profile_id && item.model);
}
function renderAvailableModels(profiles = currentModelProfiles()) {
  const available = $("available_models"), entries = [];
  profiles.forEach((profile,index) => {
    const models = new Map();
    for (const model of scannedModelsByProfile.get(profile.id) || []) models.set(String(model.id), model);
    if (profile.model) models.set(profile.model, models.get(profile.model) || {id:profile.model, active:false});
    for (const model of models.values()) entries.push({profile, model, index});
  });
  available.innerHTML = entries.length ? entries.map(({profile,model,index}) => {
    const source = (profile.provider === "online" ? "在线" : "本地") + " · " + (profile.name || "模型配置 " + (index + 1));
    return '<button type="button" class="model-option" data-available-model data-profile-id="' + safe(profile.id) + '" data-model-id="' + safe(model.id) + '" title="添加到当前选中的任务模型">' +
      '<span class="model-option-source">' + safe(source) + '</span><span>' + safe(model.id) + (model.active ? ' · 运行中' : '') + '</span></button>';
  }).join("") : '<p class="hint model-empty">扫描模型后，可调用模型会显示在这里。</p>';
}
function renderModelRoleLists(profiles, selected) {
  const profileById = new Map(profiles.map(profile => [profile.id, profile]));
  const profileIndexes = new Map(profiles.map((profile,index) => [profile.id,index + 1]));
  for (const role of ["editing","summary","vision"]) {
    const list = document.querySelector('[data-role-list="' + role + '"]') || $(role + "_model_list");
    const items = normalizeModelRoleItems(role, profiles, selected);
    list.innerHTML = items.length ? items.map((item,index) => {
      const profile = profileById.get(item.profile_id);
      const source = (profile?.provider === "online" ? "在线" : "本地") + " · " + (profile?.name || "模型配置 " + (profileIndexes.get(item.profile_id) || ""));
      return '<div class="model-role-item" data-profile-id="' + safe(item.profile_id) + '" data-model-id="' + safe(item.model) + '">' +
        '<div class="model-role-name"><small>' + safe(source) + '</small><strong>' + safe(item.model) + '</strong></div><div class="model-role-actions">' +
        '<button type="button" class="secondary" data-role-up title="上移" aria-label="上移 ' + safe(item.model) + '"' + (index ? "" : " disabled") + '>↑</button>' +
        '<button type="button" class="secondary" data-role-down title="下移" aria-label="下移 ' + safe(item.model) + '"' + (index < items.length - 1 ? "" : " disabled") + '>↓</button>' +
        '<button type="button" class="secondary" data-role-remove aria-label="删除 ' + safe(item.model) + '">删除</button></div></div>';
    }).join("") : '<p class="hint model-empty">尚未添加模型。</p>';
  }
  for (const button of document.querySelectorAll("[data-role-select]")) {
    const active = button.dataset.roleSelect === activeModelRole;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}
function setBilibiliLoginStatus(configured) {
  $("bilibili_login_status").textContent = configured
    ? "已登录 B 站；后续链接导入会使用此账号权限。"
    : "尚未设置 B 站登录信息；部分视频或高清画质可能无法下载。";
  $("clear_bilibili_cookies").hidden = !configured;
  if (configured) stopBilibiliQrPolling(true);
}
function applySettings(s, module = "") {
  const applies = (...names) => !module || names.includes(module);
  if (applies("html")) {
    $("html_include_images").checked = s.html_include_images !== "false";
    $("html_include_image_notes").checked = s.html_include_image_notes !== "false";
  }
  if (applies("storage")) {
    for (const k of ["output_dir","video_output_dir","notes_output_dir"]) $(k).value = s[k] || "";
    $("output_mode").value = s.output_mode || "data";
    selectedOutputMode = $("output_mode").value;
    updateOutputHint();
  }
  if (applies("external")) {
    for (const k of ["ffmpeg","ffprobe"]) $(k).value = s[k] || "";
    for (const k of ["ocr_command","scene_command"]) $(k).value = JSON.stringify(s[k] || [], null, 2);
  }
  if (applies("asr")) {
    $("qwen3_asr_model").value = s.qwen3_asr_model || "";
    $("qwen3_aligner_model").value = s.qwen3_aligner_model || "";
    $("asr_command").value = JSON.stringify(s.asr_command || [], null, 2);
  }
  if (applies("video")) {
    $("video_container").value = s.video_container || "source";
    $("video_fps").value = s.video_fps || "source";
    $("video_resolution").value = s.video_resolution || "source";
    $("video_resolution_width").value = s.video_resolution_width || "1920";
    $("video_resolution_height").value = s.video_resolution_height || "1080";
    const savedBitrate = String(s.video_bitrate_mbps || "source");
    const bitrateOptions = [...$("video_bitrate_mbps").options].map(option => option.value);
    $("video_bitrate_mbps").value = bitrateOptions.includes(savedBitrate) ? savedBitrate : "custom";
    $("video_bitrate_custom_mbps").value = s.video_bitrate_custom_mbps || (savedBitrate === "source" ? "5" : savedBitrate);
    $("video_encoder").value = s.video_encoder || "h264_nvenc";
    syncVideoSettingsControls();
  }
  if (applies("model-services")) {
    for (const profile of s.model_profiles || []) scannedModelsByProfile.delete(profile.id);
    renderModelChoices(s.model_profiles || [], s.model_roles || {});
  }
  if (!module) {
    setBilibiliLoginStatus(s.bilibili_login_configured === true);
    if (s.folder_setup_required) {
      requiredInitialSetup = true;
      showSettings(true);
      notice("首次使用：请确认视频和笔记保存文件夹，然后点击该卡片的保存按钮");
    }
  }
}
async function loadSettings() {
  applySettings(await request("/api/settings"));
}
function settingsModulePayload(module) {
  const result = {};
  const stringFields = keys => keys.forEach(key => { result[key] = $(key).value.trim(); });
  const commandFields = keys => keys.forEach(key => { result[key] = JSON.parse($(key).value || "[]"); });
  if (module === "storage") stringFields(["output_mode","output_dir","video_output_dir","notes_output_dir"]);
  else if (module === "html") {
    result.html_include_images = $("html_include_images").checked ? "true" : "false";
    result.html_include_image_notes = $("html_include_image_notes").checked ? "true" : "false";
  } else if (module === "video") stringFields(["video_container","video_fps","video_resolution","video_resolution_width","video_resolution_height","video_encoder","video_bitrate_mbps","video_bitrate_custom_mbps"]);
  else if (module === "external") {
    stringFields(["ffmpeg","ffprobe"]);
    commandFields(["ocr_command","scene_command"]);
  } else if (module === "asr") {
    stringFields(["qwen3_asr_model","qwen3_aligner_model"]);
    commandFields(["asr_command"]);
  } else if (module === "model-services") {
    result.model_profiles = currentModelProfiles();
    result.model_roles = currentModelRoles();
  }
  return result;
}
function closeSettingsModule(module) {
  const button = document.querySelector('[data-module-save="' + module + '"]') || document.querySelector('[data-module-undo="' + module + '"]');
  const details = button?.closest("details");
  if (details) details.open = false;
}
async function saveSettingsModule(module) {
  if (module === "access") {
    const previous = await request("/api/access");
    const enabled = $("password_required").value === "true";
    await request("/api/access", "POST", {password_required:enabled});
    closeSettingsModule(module);
    if (enabled && !previous.password_required) {
      window.location.replace("/");
      return;
    }
    notice(enabled ? "密码验证设置已保存" : "密码验证设置已保存，局域网访问密码已关闭");
    return;
  }
  const payload = settingsModulePayload(module);
  await request("/api/settings", "POST", payload);
  const updated = await request("/api/settings");
  applySettings(updated, module);
  closeSettingsModule(module);
  notice("此卡片设置已保存");
}
async function undoSettingsModule(module) {
  if (module === "access") {
    await loadAccess();
  } else {
    applySettings(await request("/api/settings"), module);
  }
  closeSettingsModule(module);
  notice("未保存的修改已撤回");
}
async function revealProjectFolder(projectId) {
  const result = await request("/api/projects/" + projectId + "/reveal", "POST", {});
  notice("已切到资源管理器：" + result.name);
}
function updateOutputHint() {
  const mode = $("output_mode").value;
  $("output_dir").disabled = mode !== "custom";
  $("pick_output").disabled = false;
  $("output_hint").textContent = mode === "source"
    ? "从本机工作区导入时：原文件夹/原文件夹名_workbench/视频名_编号。浏览器拖入无法取得原始路径。"
    : mode === "custom" ? "单文件保存在指定目录/视频名_编号；批量保存在指定目录/原文件夹名/视频名_编号。"
    : "单文件保存在工作台数据目录/projects/视频名_编号；批量按原文件夹名分组。";
}
async function loadProjects() {
  projects = await request("/api/projects");
  $("projects").innerHTML = projects.length ? projects.slice(0,10).map(p =>
    '<article class="recent-item"><div class="recent-main"><button type="button" class="recent-file" data-project-open="' + safe(p.id) + '" title="打开项目">' + safe(p.name) +
    '</button><div class="recent-meta"><span>' + safe(p.step) + ' · ' + safe(p.updated || p.created) + '</span><code>' + safe(p.output_path || "旧项目：工作台数据目录") + '</code></div></div><button type="button" class="secondary recent-open" data-reveal="' + safe(p.id) + '" title="在资源管理器中打开项目文件夹">打开文件夹</button></article>'
  ).join("") : '<p class="hint">还没有项目</p>';
  document.querySelectorAll("#projects [data-reveal]").forEach(button => button.onclick = async () => {
    try { await revealProjectFolder(button.dataset.reveal); }
    catch (error) { notice(error.message); }
  });
  document.querySelectorAll("#projects [data-project-open]").forEach(button => button.onclick = () => openProjectFromButton(button.dataset.projectOpen,button));
  renderBatchTasks();
}
function batchStage(project, action, title) {
  const row = project.progress?.[action] || {};
  const state = row.state || "pending";
  const percent = state === "done" ? 100 : Math.max(0, Math.min(100, Number(row.percent) || 0));
  const indeterminate = state === "running" && row.percent == null;
  const stateText = {pending:"等待", running:"进行中", done:"完成", error:"失败"}[state] || state;
  const bar = '<progress max="100"' + (indeterminate ? '' : ' value="' + percent + '"') +
    ' class="' + (indeterminate ? 'indeterminate' : state === "error" ? 'error' : '') + '" aria-label="' + title + ' ' + stateText + '"></progress>';
  return '<div class="batch-stage"><span>' + title + '</span>' + bar + '<small>' + safe(row.label || stateText) + '</small></div>';
}
function renderBatchTasks() {
  const batch = batches.find(row => row.id === currentBatch);
  const ids = new Set(batch?.projects || []);
  const rows = projects.filter(project => ids.has(project.id));
  $("batch_tasks").innerHTML = rows.length ? rows.map(project =>
    '<article class="batch-project"><div class="batch-project-head"><button type="button" class="batch-project-name" data-project-open="' + safe(project.id) + '">' + safe(project.name) +
    '</button><span>' + safe(project.status || "等待") + ' · ' + safe(project.step || "待处理") + '</span></div><div class="batch-stage-grid">' +
    batchStage(project,"transcribe","转写") + batchStage(project,"analyze","剪辑建议") +
    batchStage(project,"summary","图文总结") + batchStage(project,"export","精剪导出") + '</div></article>'
  ).join("") : '<p class="hint">当前批次还没有项目。</p>';
  document.querySelectorAll("#batch_tasks [data-project-open]").forEach(button => button.onclick = () => openProjectFromButton(button.dataset.projectOpen,button));
}
async function loadBatches(prefer = null) {
  batches = await request("/api/batches");
  const desired = prefer === null ? (currentBatch || $("batch_select").value) : prefer;
  const runningBatch=batches.find(batch => batch.status === "running");
  $("batch_select").innerHTML = (batches.length ? '<option value="">选择批次</option>' : '<option value="">暂无批次</option>') +
    batches.map(b => '<option value="' + safe(b.id) + '">' + safe(b.name) + ' · ' + b.projects.length + ' 个视频</option>').join("");
  currentBatch = runningBatch?.id || (batches.some(b => b.id === desired) ? desired : "");
  $("batch_select").value = currentBatch;
  $("batch_select").disabled = !!runningBatch;
  renderBatchTasks();
  if (current) {
    const project = projects.find(row => row.id === current);
    if (project) updateOperationButtons(project);
  }
}
function renderTaskControl(state = taskControl) {
  taskControl=state;
  const button=$("pause_task");
  const settingsOpen=!$("settings_page").hidden;
  button.hidden=!state.running || settingsOpen;
  button.disabled=!state.running;
  button.textContent=state.paused ? "继续任务" : "暂停任务";
  button.setAttribute("aria-pressed",String(!!state.paused));
  button.classList.toggle("task-paused",!!state.paused);
  button.classList.toggle("task-pause-pending",!!state.pause_pending);
  button.title=state.pause_pending ? "暂停请求已发送；当前模型请求或处理步骤完成后暂停" :
    state.paused ? "继续当前任务" : state.running ? "暂停当前任务" : "没有运行中的任务";
}
async function loadTaskControl() {
  renderTaskControl(await request("/api/task-control"));
}
async function toggleTaskPause() {
  const pause=!taskControl.paused;
  const button=$("pause_task");
  button.disabled=true;
  try {
    renderTaskControl(await request("/api/task-control","POST",{action:pause ? "pause" : "resume"}));
    notice(pause ? (taskControl.pause_pending ? "已请求暂停；当前模型请求或处理步骤完成后会停住。" : "任务已暂停，点击“继续任务”可恢复。") : "任务已继续。");
  } catch (error) {
    notice(error.message);
    await loadTaskControl().catch(() => {});
  }
}
function renderSuggestions(rows, switched, defaultAll) {
  const key = JSON.stringify({rows,defaultAll});
  if (!switched && key === renderedSuggestionsKey) return;
  renderedSuggestionsKey = key;
  currentSuggestions = rows.map(row => ({...row, selected:defaultAll || !!row.selected}));
  updateClipEstimate();
}
function formatTimestamp(value) {
  const ms = Math.max(0,Math.round(Number(value) * 1000));
  const h = Math.floor(ms / 3600000), m = Math.floor(ms % 3600000 / 60000), s = Math.floor(ms % 60000 / 1000), f = ms % 1000;
  return [h,m,s].map(x => String(x).padStart(2,"0")).join(":") + "." + String(f).padStart(3,"0");
}
function formatDurationClock(value) {
  const seconds = Number(value);
  const totalSeconds = Math.max(0,Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const h = Math.floor(totalSeconds / 3600), m = Math.floor(totalSeconds % 3600 / 60), s = totalSeconds % 60;
  return [h,m,s].map(x => String(x).padStart(2,"0")).join(":");
}
function formatSummaryDuration(value) {
  const seconds=Number(value);
  return formatDurationClock(Math.max(0,Number.isFinite(seconds) ? seconds : 0));
}
function mergeTimeRanges(ranges) {
  const merged=[];
  for (const [start,end] of ranges.sort((a,b) => a[0]-b[0] || a[1]-b[1])) {
    const previous=merged[merged.length-1];
    if (previous && start<=previous[1]+.001) previous[1]=Math.max(previous[1],end);
    else merged.push([start,end]);
  }
  return merged;
}
function cutCategory(value) {
  const category=String(value || "other").toLowerCase();
  if (category.includes("pause") || category.includes("停顿")) return "pause";
  if (["repeat","repetition","duplicate","重复"].some(word => category.includes(word))) return "repeat";
  if (["filler","discourse","语气","口头禅"].some(word => category.includes(word))) return "filler";
  if (["restart","rephrase","false_start","重新起句"].some(word => category.includes(word))) return "restart";
  if (category.includes("manual") || category.includes("手动")) return "manual";
  return "other";
}
function renderCutSummary(duration, selectedRanges, estimatedDuration) {
  const categories=[
    ["pause","停顿裁剪"],["repeat","重复裁剪"],["filler","语气词裁剪"],
    ["restart","重新起句裁剪"],["manual","手动标记裁剪"],["other","其他建议裁剪"]
  ];
  const byCategory=new Map(categories.map(([key]) => [key,[]]));
  for (const row of selectedRanges) byCategory.get(cutCategory(row.category)).push([row.start,row.end]);
  const finalDuration=Math.max(0,Math.min(duration,estimatedDuration));
  const totalRemoved=Math.max(0,duration-finalDuration);
  const metrics=[
    {label:"原视频长度",value:formatSummaryDuration(duration)},
    ...categories.map(([key,label]) => ({
      label,value:formatSummaryDuration(mergeTimeRanges(byCategory.get(key)).reduce((sum,[start,end]) => sum+end-start,0))
    })),
    {label:"共裁剪时长",value:formatSummaryDuration(totalRemoved),className:"cut-summary-total"},
    {label:"预计成片长度",value:formatSummaryDuration(finalDuration),className:"cut-summary-final"}
  ];
  $("cut_summary_metrics").innerHTML=metrics.map(metric =>
    '<div class="cut-summary-stat ' + (metric.className || '') + '"><span>' + metric.label + '</span><strong>' + metric.value + '</strong></div>'
  ).join("");
}
function updateClipEstimate() {
  const label=$("clip_estimate");
  if (!label) return;
  const duration=Number(currentMediaDuration);
  if (!(duration>0)) {
    label.textContent="导入视频后显示原片时长";
    $("cut_summary_metrics").innerHTML='<div class="cut-summary-stat"><span>导入视频后显示时长统计</span><strong>—</strong></div>';
    return;
  }
  const selectedRanges=currentSuggestions.filter(row => row.selected).map(row => ({
    start:Math.max(0,Number(row.start)),end:Math.min(duration,Number(row.end)),category:row.category
  })).filter(row => Number.isFinite(row.start) && Number.isFinite(row.end) && row.end>row.start);
  const merged=mergeTimeRanges(selectedRanges.map(row => [row.start,row.end]));
  let cursor=0,kept=0;
  for (const [start,end] of merged) {
    if (start>cursor+.02) kept+=start-cursor;
    cursor=Math.max(cursor,end);
  }
  if (duration>cursor+.02) kept+=duration-cursor;
  const estimatedDuration=Math.min(duration,Math.max(0,kept));
  renderCutSummary(duration,selectedRanges,estimatedDuration);
  if (!currentAnalysisDone) {
    label.textContent="原片 " + formatDurationClock(duration);
    return;
  }
  label.textContent="原片 " + formatDurationClock(duration) + " 新片 " + formatDurationClock(estimatedDuration);
}
function transcriptCharacters(value) {
  const parts=[];
  if (typeof Intl.Segmenter === "function") {
    for (const item of new Intl.Segmenter("zh",{granularity:"grapheme"}).segment(value))
      parts.push({text:item.segment,start:item.index,end:item.index+item.segment.length});
  } else {
    let offset=0;
    for (const text of Array.from(value)) { parts.push({text,start:offset,end:offset+text.length}); offset+=text.length; }
  }
  return parts;
}
function renderTranscript(segments) {
  updateClipEstimate();
  if (!segments?.length) { $("segments").textContent = "尚无转写文本"; return; }
  const selected = currentSuggestions.filter(row => row.selected && row.category!=="pause");
  const proposed = currentSuggestions.filter(row => !row.selected && row.category!=="pause");
  const formatted=Array.isArray(currentTranscriptLayout?.markers) && currentTranscriptLayout.markers.length>0;
  const punctuation=new Map((formatted ? currentTranscriptLayout.markers : []).map(row => [String(row.segment_id)+":"+Number(row.offset_utf16),row]));
  const isPunctuation=value=>/\p{P}/u.test(value);
  let html = "";
  for (let i = 0; i < segments.length; i++) {
    const s = segments[i];
    if (i) {
      const prev = segments[i - 1], gap = Number(s.start) - Number(prev.end);
      if (gap > 1) {
        const pause = currentSuggestions.find(row => row.category === "pause" &&
          Math.abs(Number(row.start) - Number(prev.end)) < .01 && Math.abs(Number(row.end) - Number(s.start)) < .01);
        const pauseDeleted = pause ? !!pause.selected : false;
        const pauseSuggested = !!pause && !pauseDeleted;
        const prevText=String(prev.text || "");
        const prevEnd=punctuation.get(String(prev.id)+":"+prevText.length);
        if (gap >= 1.8 && !prevEnd?.paragraph) html += '<br><br>';
        html += '<span class="pause-marker' + (pauseDeleted ? ' deleted' : pauseSuggested ? ' suggested' : '') + '" data-pause-start="' + Number(prev.end) + '" data-pause-end="' + Number(s.start) + '" title="\u65f6\u95f4\u8303\u56f4\uff1a' + formatTimestamp(prev.end) + '\u2014' + formatTimestamp(s.start) + '\uff1b\u70b9\u51fb\u5207\u6362\u505c\u987f\u5220\u9664">\u23f8 ' + gap.toFixed(2) + '\u79d2</span>';
      }
    }
    const pieces=transcriptCharacters(String(s.text));
    for (const piece of pieces) {
      const before=punctuation.get(String(s.id)+":"+piece.start);
      const after=punctuation.get(String(s.id)+":"+piece.end);
      if (before?.before) html += '<span class="transcript-punctuation">' + safe(before.before) + '</span>';
      if (formatted && isPunctuation(piece.text)) continue;
      const start=Number(s.start)+(Number(s.end)-Number(s.start))*piece.start/Math.max(1,String(s.text).length);
      const end=Number(s.start)+(Number(s.end)-Number(s.start))*piece.end/Math.max(1,String(s.text).length);
      const deleted=selected.some(row => row.start<end && row.end>start);
      const suggested=!deleted && proposed.some(row => row.start<end && row.end>start);
      const tokenClass=deleted ? ' deleted' : suggested ? ' suggested' : '';
      const title=deleted ? '单击恢复整条删除建议；拖选可局部恢复' : suggested ? '模型建议删除，当前未选中；单击采纳' : '';
      html += '<span class="transcript-token' + tokenClass + '" data-segment-id="' + safe(s.id) + '" data-char-start="' + piece.start + '" data-char-end="' + piece.end + '" data-start="' + start + '" data-end="' + end + '"' + (title ? ' title="' + title + '"' : '') + '>' + safe(piece.text) + '</span>';
      if (after?.after) html += '<span class="transcript-punctuation' + (deleted ? ' deleted' : '') + '">' + safe(after.after) + '</span>';
      if (after?.paragraph) html += '<br><br>';
    }
  }
  if ($("segments").innerHTML !== html) $("segments").innerHTML = html;
}
async function saveTranscriptReview(message = "审核结果已保存") {
  if (!current || interactionBusy) return;
  interactionBusy = true;
  try {
    await request("/api/projects/" + current + "/decisions","POST",currentSuggestions.map(row => ({
      id:row.id, selected:!!row.selected, start:Number(row.start), end:Number(row.end)
    })));
    await openProject(current);
    notice(message);
  } catch (error) {
    notice(error.message);
    renderedSuggestionsKey = "";
    await openProject(current).catch(() => {});
  } finally { interactionBusy = false; }
}
async function addSelectedTextSuggestion() {
  if (!current || interactionBusy) return;
  const selection = window.getSelection();
  const box = $("segments");
  if (!selection || selection.isCollapsed || !box.contains(selection.anchorNode) || !box.contains(selection.focusNode)) return;
  const range = selection.getRangeAt(0);
  const tokens = [...box.querySelectorAll(".transcript-token")].filter(token => {
    try { return range.intersectsNode(token); } catch { return false; }
  });
  if (!tokens.length) return;
  const firstToken=tokens[0], lastToken=tokens[tokens.length-1];
  const first=currentSegments.find(segment => segment.id===firstToken.dataset.segmentId);
  const last=currentSegments.find(segment => segment.id===lastToken.dataset.segmentId);
  if (!first || !last) return;
  const boundaryOffset=(token,node,offset,isStart) => {
    const edge=Number(isStart ? token.dataset.charStart : token.dataset.charEnd);
    if (node!==token && !token.contains(node)) return edge;
    try {
      const prefix=document.createRange();
      prefix.selectNodeContents(token);
      prefix.setEnd(node,offset);
      return Number(token.dataset.charStart)+prefix.toString().length;
    } catch { return edge; }
  };
  const startOffset=Math.max(0,Math.min(first.text.length,boundaryOffset(firstToken,range.startContainer,range.startOffset,true)));
  const endOffset=Math.max(0,Math.min(last.text.length,boundaryOffset(lastToken,range.endContainer,range.endOffset,false)));
  const start=Number(first.start)+(Number(first.end)-Number(first.start))*startOffset/Math.max(1,first.text.length);
  const end=Number(last.start)+(Number(last.end)-Number(last.start))*endOffset/Math.max(1,last.text.length);
  if (!(end>start)) return;
  const restoring=currentSuggestions.some(row => row.selected && row.category!=="pause" && row.start<end && row.end>start);
  suppressTranscriptClickUntil = Date.now() + 700;
  interactionBusy = true;
  try {
    if (restoring)
      await request("/api/projects/" + current + "/manual-restore","POST",{start,end});
    else
      await request("/api/projects/" + current + "/manual-suggestion","POST",{
        start_id:first.id,end_id:last.id,start,end
      });
    selection.removeAllRanges();
    renderedSuggestionsKey = "";
    await openProject(current);
    notice(restoring ? "已恢复选中文字，其余删除范围保持不变" : "已标记所选字幕并保存；点击删除线可恢复整条建议");
  } catch (error) {
    notice(error.message);
    await openProject(current).catch(() => {});
  } finally { interactionBusy = false; }
}
async function togglePause(start, end) {
  if (!current || interactionBusy) return;
  const pause = currentSuggestions.find(row => row.category === "pause" &&
    Math.abs(Number(row.start) - start) < .01 && Math.abs(Number(row.end) - end) < .01);
  if (pause) {
    pause.selected = !pause.selected;
    renderTranscript(currentSegments);
    await saveTranscriptReview(pause.selected ? "已标记删除此停顿" : "已取消删除此停顿");
    return;
  }
  interactionBusy = true;
  try {
    await request("/api/projects/" + current + "/manual-pause-suggestion","POST",{start,end});
    renderedSuggestionsKey = "";
    await openProject(current);
    notice("已标记删除此停顿");
  } catch (error) { notice(error.message); }
  finally { interactionBusy = false; }
}
function renderProgress(progress) {
  const labels = {transcribe:"等待开始",analyze:"等待开始",export:"等待审核",summary:"等待开始",punctuate:"转写完成后自动整理标点与段落"};
  for (const action of Object.keys(labels)) {
    const row = progress?.[action] || {};
    const bar = $("progress_" + action);
    const label = $("label_" + action);
    const running = row.state === "running";
    const hasEstimate = running && Number(row.estimated_seconds) > 0 && Number(row.started_at) > 0;
    const elapsed = hasEstimate ? Math.max(Number(row.elapsed_seconds) || 0, Date.now()/1000 - Number(row.started_at)) : 0;
    const actual = typeof row.percent === "number" ? row.percent : (row.state === "done" ? 100 : 0);
    const timed = hasEstimate ? Math.min(98, elapsed/Number(row.estimated_seconds)*100) : 0;
    const shown = hasEstimate ? Math.max(actual,timed) : actual;
    bar.classList.toggle("indeterminate",running && !hasEstimate && (row.percent == null || row.percent <= 0));
    bar.classList.toggle("error",row.state === "error");
    if (running && !hasEstimate && (row.percent == null || row.percent <= 0)) bar.removeAttribute("value");
    else bar.value = shown;
    const fraction = typeof row.percent === "number" && running ? " · " + row.percent + "%" : "";
    const remaining = hasEstimate && elapsed >= Number(row.estimated_seconds) ? "预计时间已过，仍在处理" : "约剩 " + formatEstimate(Math.max(0,Number(row.estimated_seconds)-elapsed));
    const timing = hasEstimate ? " · 已用 " + formatEstimate(elapsed) + " · " + remaining :
      (running ? " · 首次测速中" : "");
    const missingSummaryModel=action==="summary" && row.state==="done" &&
      String(row.label || "").startsWith("未配置总结模型");
    label.classList.toggle("status-complete",row.state==="done" && !missingSummaryModel);
    label.classList.toggle("status-error",row.state==="error");
    if (row.state==="done" && !missingSummaryModel) label.textContent="已完成";
    else if (row.state==="error") {
      label.textContent="失败";
      label.title=row.label || "失败";
    } else {
      label.title="";
      label.textContent=(row.label || labels[action]) + fraction + timing;
    }
  }
}
function formatEstimate(seconds) {
  const value = Math.max(0,Math.ceil(Number(seconds) || 0));
  if (value < 60) return value + "秒";
  if (value < 3600) return Math.ceil(value/60) + "分";
  const hours = Math.floor(value/3600), minutes = Math.ceil(value%3600/60);
  return hours + "小时" + (minutes ? minutes + "分" : "");
}
async function stopCurrent() {
  const batch=batches.find(row => row.id === currentBatch);
  if (batch?.status === "running") {
    const accepted = window.confirm("确定停止当前批量任务吗？已经完成的项目会保留；尚未完成的导出不会保存为成品。");
    if (!accepted) return;
    await request("/api/batches/" + batch.id + "/cancel","POST",{});
    notice("已发送停止请求，正在结束当前批量任务…");
    await loadBatches(batch.id);
    return;
  }
  if (!current) return;
  const accepted = window.confirm("确定停止当前操作吗？已经完成的步骤会保留；尚未完成的导出不会保存为成品。");
  if (!accepted) return;
  await request("/api/projects/" + current + "/cancel","POST",{});
  notice("已发送停止请求，正在结束当前操作…");
  await openProject(current);
}
function setOperationButton(button,active,disabled,stopping=false) {
  if (!button.dataset.defaultLabel) button.dataset.defaultLabel=button.textContent;
  button.dataset.stopActive=active ? "true" : "false";
  button.textContent=active ? (stopping ? "正在停止…" : "停止") : button.dataset.defaultLabel;
  button.classList.toggle("stop-action",active);
  button.disabled=disabled;
}
function updateOperationButtons(project) {
  const batch=batches.find(row => row.id === currentBatch);
  const batchHasSegments=batch?.projects?.some(id => projects.find(row => row.id === id)?.has_segments);
  const batchRunning=batch?.status === "running";
  const running=project.status==="running" || batchRunning;
  const stopping=!!project.stop_requested || !!batch?.stop_requested;
  const operation=batchRunning ? (batch.mode || "") : (project.active_operation || "");
  const entries=[["transcribe","transcribe"],["analyze","analyze"],["export","export"],
    ["punctuate","punctuate"],["generate_document","document"],
    ["generate_timestamp_document","timestamp_document"]];
  for (const [id,action] of entries) {
    const button=$(id), active=running && operation===action;
    let disabled=running && !active;
    if (!running && action==="document") disabled=!(currentSegments.length || batchHasSegments);
    if (!running && action==="timestamp_document") disabled=!(currentSegments.length || batchHasSegments);
    setOperationButton(button,active,disabled || (active && stopping),stopping);
  }
  const summaryActive=running && operation==="summary";
  $("summary_modes").hidden=summaryActive;
  $("summary_stop").hidden=!summaryActive;
  $("summary_stop").disabled=stopping || !summaryActive;
  for (const id of ["summary_detailed","summary_medium","summary_concise"]) $(id).disabled=running;
  const oneClick=$("finish_all"), oneClickActive=running && operation==="finish_all";
  setOperationButton(oneClick,oneClickActive,running && !oneClickActive || (oneClickActive && stopping),stopping);
  $("open_finished_folder").disabled=running || !(batch?.projects?.length || project.output_path);
}
function maybeAutoFormatTranscript(id,project,detail) {
  const progress=project.progress?.punctuate || {};
  const progressLabel=String(progress.label || "");
  const pendingTranscription=progressLabel.includes("等待整理标点");
  if (project.status!=="ready" || !detail.segments?.length || detail.transcript_layout?.markers?.length ||
      progress.state!=="pending" || !pendingTranscription || !currentModelRoles().editing.length) return;
  const retryAt=autoFormatRetryAt.get(id) || 0;
  if (retryAt===Infinity || Date.now()<retryAt) return;
  autoFormatRetryAt.set(id,Date.now()+8000);
  request("/api/projects/"+id+"/punctuate","POST",{}).then(async () => {
    autoFormatRetryAt.set(id,Infinity);
    notice("转写已完成，正在自动补充标点与段落…");
    await openProject(id);
  }).catch(error => {
    if (!error.message.includes("另一任务正在运行")) notice("自动整理未能启动："+error.message);
  });
}
async function openProject(id) {
  const switched = current !== id;
  current = id;
  const d = await request("/api/projects/" + id);
  const p = d.project;
  currentSegments = d.segments || [];
  currentTranscriptLayout = d.transcript_layout || null;
  currentMediaDuration = Number(d.media?.duration) || 0;
  currentAnalysisDone = p.progress?.analyze?.state === "done" || !!p.reviewed_at;
  $("detail").hidden = false;
  $("project_name").textContent = p.name;
  const reviewText = !p.reviewed_at ? "待审核" : p.review_mode === "automatic" ? "自动剪辑 " + (p.auto_selected_count ?? 0) + " 处" : "已审核";
  $("project_status").textContent = p.status + " · " + p.step + " · " + reviewText + (p.model_runtime_status ? " · " + p.model_runtime_status : "");
  $("project_error").textContent = p.error || "";
  renderProgress(p.progress);
  updateOperationButtons(p);
  if (switched) $("preview").src = "/api/projects/" + id + "/source";
  const base = "/api/projects/" + id + "/files/";
  const files = p.artifacts || {video:"edited.mp4",subtitle:"edited.srt",html:"summary.html",full_html:"transcript_full.html",markdown:"summary.md",full_markdown:"transcript_full.md"};
  $("downloads").innerHTML =
    '<a href="' + base + encodeURIComponent(files.video) + '">精剪视频</a>' +
    '<a href="' + base + encodeURIComponent(files.subtitle) + '">字幕</a>' +
    '<a href="' + base + encodeURIComponent(files.html) + '">总结 HTML</a>' +
    '<a href="' + base + encodeURIComponent(files.full_html || "transcript_full.html") + '">全文 HTML</a>' +
    '<a href="' + base + encodeURIComponent(files.markdown || "summary.md") + '">总结笔记 Markdown</a>' +
    '<a href="' + base + encodeURIComponent(files.full_markdown || "transcript_full.md") + '">完整 Markdown</a>' +
    '<a href="' + base + encodeURIComponent(files.timestamp_markdown || "transcript_timestamps.md") + '">时间戳 Markdown</a>';
  const segments = d.segments || [];
  $("transcript_download").href = base + "transcript.txt";
  renderSuggestions(d.suggestions || [], switched, !p.reviewed_at);
  renderTranscript(segments);
  $("chapters").innerHTML = (d.summary?.chapters || []).map(c =>
    '<div class="item"><b>' + safe(c.title) + '</b><small>' +
    formatTimestamp(c.start) + '—' + formatTimestamp(c.end) + ' · ' + safe(c.summary).slice(0,200) + '</small></div>'
  ).join("") || (d.summary?.mode === "transcript_only"
    ? '<p class="hint">' + (d.summary?.fallback_reason ? '模型服务不可用，已自动回退生成字幕版' + (d.summary?.html_generated === false ? '总结 Markdown。' : 'Markdown 与 HTML。') + '错误：' + safe(d.summary.fallback_reason) : '未配置文本模型，已生成按语音段落排版的 Markdown' + (d.summary?.html_generated === false ? '；未生成图文 HTML。' : ' 与 HTML；未生成主题摘要。')) + '</p>'
    : '<p class="hint">尚未生成总结</p>');
  if (p.status === "running" && lastStatus !== p.step) { lastStatus = p.step; notice(p.step); }
  saveUIState();
  maybeAutoFormatTranscript(id,p,d);
}
async function openProjectFromButton(id,button) {
  if (button) button.disabled = true;
  notice("正在加载项目详情…");
  try {
    await openProject(id);
    if (button) {
      document.querySelector("details.recent")?.removeAttribute("open");
      setImportPanelOpen(false);
    }
    $("detail").scrollIntoView({behavior:"smooth",block:"start"});
    saveUIState();
    notice("项目已打开");
  } catch (error) {
    notice("打开项目失败：" + error.message);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}
async function start(action,options={}) {
  if (!current) return;
  const summaryMode=action === "summary" ? (options.summary_mode || "detailed") : "";
  const payload=action === "summary" ? {summary_mode:summaryMode} : {};
  await request("/api/projects/" + current + "/" + action,"POST",payload);
  const modeLabel={detailed:"详细",concise:"简约",medium:"中档"}[summaryMode];
  notice(action === "document" ? "正在生成无时间戳 Markdown 文档（不调用大模型）" : action === "timestamp_document" ? "正在生成时间戳 Markdown 文档" : action === "summary" ? "正在生成图文文档（" + modeLabel + "模式）" : "任务已启动：" + action);
  await openProject(current);
}
async function upload(files, options = {}) {
  const items = [...files].filter(file => /\.(mp4|mov|mkv|webm)$/i.test(file.name));
  if (!items.length) return notice(options.fromFolder
    ? "拖入的文件夹里没有 MP4、MOV、MKV 或 WEBM 视频"
    : "请选择 MP4、MOV、MKV 或 WEBM 文件");
  if (selectedOutputMode === "source") return notice("原目录保存模式请使用“选择工作区文件夹”导入");
  setImportPanelOpen(true);
  try {
    let batchId = "";
    if (items.length > 1 || options.forceBatch) {
      const firstVideoName = items[0].name.replace(/\.[^.]+$/,"");
      const batchName = options.batchName || firstVideoName || "上传批次";
      const batch = await request("/api/batches","POST",{name:batchName,group_output:true});
      batchId = batch.id;
    }
    let firstId = "", lastId = "";
    for (let i = 0; i < items.length; i++) {
      notice("正在导入 " + (i + 1) + "/" + items.length + "：" + items[i].name);
      const headers = {"X-Filename":encodeURIComponent(items[i].name)};
      if (batchId) headers["X-Batch-ID"] = batchId;
      const value = await request("/api/projects","POST",items[i],headers);
      if (!firstId) firstId = value.id;
      lastId = value.id;
    }
    await loadProjects();
    await loadBatches(batchId);
    const projectId=firstId || lastId;
    if (projectId) await openProject(projectId);
    notice(batchId
      ? "已导入 " + items.length + " 个视频并创建批次；点击右侧按钮开始批量任务"
      : "已导入 1 个视频；点击右侧按钮选择任务");
  } catch (error) { notice(error.message); }
}
function readDirectoryEntries(entry) {
  return new Promise((resolve,reject) => {
    const reader = entry.createReader();
    const entries = [];
    const readNext = () => reader.readEntries(rows => {
      if (!rows.length) return resolve(entries);
      entries.push(...rows);
      readNext();
    },reject);
    readNext();
  });
}
function readDroppedFile(entry) {
  return new Promise((resolve,reject) => entry.file(resolve,reject));
}
async function collectDroppedEntry(entry, files) {
  if (entry.isFile) {
    files.push(await readDroppedFile(entry));
    return;
  }
  if (!entry.isDirectory) return;
  const children = await readDirectoryEntries(entry);
  for (const child of children) await collectDroppedEntry(child,files);
}
async function collectDroppedItems(dataTransfer) {
  const entries = [...(dataTransfer.items || [])]
    .map(item => (item.getAsEntry?.() || item.webkitGetAsEntry?.()))
    .filter(Boolean);
  if (!entries.length) return {files:[...dataTransfer.files],folderNames:[]};
  const files = [];
  const folderNames = entries.filter(entry => entry.isDirectory).map(entry => entry.name);
  for (const entry of entries) await collectDroppedEntry(entry,files);
  return {files,folderNames};
}
async function uploadDroppedItems(dataTransfer) {
  try {
    const {files,folderNames} = await collectDroppedItems(dataTransfer);
    const fromFolder = folderNames.length > 0;
    const batchName = fromFolder ? folderNames[0] : "";
    await upload(files,{fromFolder,forceBatch:fromFolder,batchName});
  } catch (error) {
    notice("读取拖入文件夹失败：" + error.message);
  }
}
async function loadFolderPath(path) {
  $("folder_error").textContent = "";
  $("folder_rows").innerHTML = '<p class="hint">正在读取文件夹…</p>';
  const result = await request("/api/directories?path=" + encodeURIComponent(path || ""));
  folderCurrent = result.current || "";
  folderParent = result.parent || "";
  $("folder_path").value = folderCurrent;
  $("folder_up").disabled = !folderParent;
  $("folder_select").disabled = !folderCurrent;
  $("folder_create").disabled = !folderCurrent;
  $("folder_error").textContent = "";
  const rows = result.directories || [];
  $("folder_rows").innerHTML = rows.length
    ? rows.map(row => '<div class="folder-entry-row"><button type="button" class="folder-entry" data-folder-path="' + safe(row.path) + '"><span>📁</span>' + safe(row.name) + '</button><button type="button" class="secondary folder-rename" data-folder-rename="' + safe(row.name) + '">重命名</button></div>').join("")
    : '<p class="hint">此文件夹没有子文件夹。</p>';
}
async function createFolder() {
  const name = window.prompt("新建文件夹名称");
  if (name === null) return;
  try {
    await request("/api/directories/create","POST",{parent:folderCurrent,name});
    await loadFolderPath(folderCurrent);
    notice("文件夹已创建");
  } catch (error) { $("folder_error").textContent = error.message; }
}
async function renameFolder(oldName) {
  const name = window.prompt("请输入新的文件夹名称",oldName);
  if (name === null || name.trim() === oldName) return;
  try {
    await request("/api/directories/rename","POST",{parent:folderCurrent,old_name:oldName,new_name:name});
    await loadFolderPath(folderCurrent);
    notice("文件夹已重命名");
  } catch (error) { $("folder_error").textContent = error.message; }
}
async function chooseFolder(inputId) {
  folderTargetId = inputId;
  const dialog = $("folder_picker");
  $("folder_rows").innerHTML = '<p class="hint">正在读取文件夹…</p>';
  $("folder_error").textContent = "";
  dialog.showModal();
  const initialdir = $(inputId).value.trim();
  try {
    await loadFolderPath(initialdir);
  } catch (error) {
    try {
      await loadFolderPath("");
      $("folder_error").textContent = error.message + "；请从磁盘列表中选择文件夹。";
    } catch (fallbackError) {
      $("folder_error").textContent = fallbackError.message;
      $("folder_rows").innerHTML = '<p class="hint">无法读取目录列表，请手动输入完整路径。</p>';
    }
  }
}
async function importFolder() {
  const path = $("workspace_folder").value.trim();
  if (!path) throw Error("请选择工作区文件夹或填写 B 站视频链接");
  const button = $("import_folder");
  if (button.disabled) return;
  const label = button.textContent;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "正在导入…";
  notice("正在导入；B 站链接需要先下载视频，可能需要等待几分钟");
  try {
    const result = await request("/api/batches/import","POST",{path});
    await loadProjects();
    await loadBatches(result.batch_id);
    if (result.project_id) {
      await openProject(result.project_id);
      const subtitleMessage=result.subtitle
        ? "字幕已下载并导入；点击右侧按钮开始处理，一键完成会跳过本机语音转写。"
        : result.subtitle_available
          ? "已下载字幕但无法解析；点击右侧按钮开始处理，一键完成会使用本机语音转写。"
          : "未找到可下载字幕；点击右侧按钮开始处理，一键完成会使用本机语音转写。";
      notice("B 站视频已导入并创建项目；" + subtitleMessage);
    }
    else if (result.count) {
      const batch = batches.find(row => row.id === result.batch_id);
      if (batch?.projects?.[0]) await openProject(batch.projects[0]);
      notice("已导入 " + result.count + " 个视频并创建批次；点击右侧按钮开始批量任务" + (result.skipped.length ? "，跳过 " + result.skipped.length + " 个" : ""));
    } else notice("导入 0 个视频" + (result.skipped.length ? "，跳过 " + result.skipped.length + " 个" : ""));
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = label;
  }
}
async function runSelectedTask(action) {
  const batch=batches.find(row => row.id === currentBatch);
  if (!batch) {
    if (!current) return notice("请先导入或打开一个项目");
    if (action === "finish_all") {
      await request("/api/projects/" + current + "/finish","POST",{});
      notice("一键处理已启动");
      await openProject(current);
    } else {
      await start(action);
    }
    return;
  }
  await request("/api/batches/" + batch.id + "/run","POST",{mode:action});
  setBatchPanelOpen(true);
  await loadBatches(batch.id);
  const firstId=batch.projects?.[0];
  if (firstId) await openProject(firstId);
  const labels={document:"生成文档",timestamp_document:"生成时间戳文档",finish_all:"一键完成"};
  notice("已启动批量" + (labels[action] || action));
}
function showSettings(open, options = {}) {
  $("workspace_page").hidden = open;
  $("settings_page").hidden = !open;
  $("open_settings").hidden = open;
  renderTaskControl();
  if (options.scroll !== false) window.scrollTo({top:0,behavior:"smooth"});
  if (options.persist !== false) saveUIState();
}
async function loadSession() {
  const session = await request("/api/session");
  $("logout").hidden = !session.auth_enabled;
}
async function loadAccess() {
  const access = await request("/api/access");
  $("access_settings").hidden = !access.supported;
  if (access.supported) $("password_required").value = String(access.password_required);
}
async function restoreUIState(saved) {
  restoringUIState = true;
  const recent = document.querySelector("details.recent");
  if (recent) recent.open = saved?.recentOpen === true;
  setBatchPanelOpen(saved?.batchOpen === true);
  setImportPanelOpen(true);

  const savedProject = projects.some(row => row.id === saved?.projectId) ? saved.projectId : "";
  const runningProject = !saved ? projects.find(row => row.status === "running")?.id || "" : "";
  const selectedBatchProject = batches.find(row => row.id === currentBatch)?.projects?.[0] || "";
  const projectId = savedProject || selectedBatchProject || runningProject;
  if (projectId) {
    try {
      await openProject(projectId);
    } catch {
      current = null;
      $("detail").hidden = true;
    }
  } else {
    current = null;
    $("detail").hidden = true;
  }

  showSettings(requiredInitialSetup, {scroll:false,persist:false});
  if (!requiredInitialSetup) {
    document.querySelector(".import-card")?.scrollIntoView({block:"start"});
  }
  restoringUIState = false;
  saveUIState();
}
function syncVideoSettingsControls() {
  $("video_resolution_custom").hidden = $("video_resolution").value !== "custom";
  $("video_bitrate_custom_wrap").hidden = $("video_bitrate_mbps").value !== "custom";
}
$("settings_page").addEventListener("click", event => {
  const saveButton = event.target.closest("[data-module-save]");
  const undoButton = event.target.closest("[data-module-undo]");
  const button = saveButton || undoButton;
  if (!button) return;
  event.preventDefault();
  event.stopPropagation();
  const module = button.dataset.moduleSave || button.dataset.moduleUndo;
  button.disabled = true;
  const action = saveButton ? saveSettingsModule(module) : undoSettingsModule(module);
  action.catch(error => notice(error.message)).finally(() => { button.disabled = false; });
});
function stopBilibiliQrPolling(hidePanel = false) {
  clearTimeout(bilibiliQrTimer);
  bilibiliQrTimer = 0;
  bilibiliQrLoginId = "";
  bilibiliQrExpiresAt = 0;
  if (hidePanel) {
    $("bilibili_qr_panel").hidden = true;
    $("bilibili_qr_image").removeAttribute("src");
  }
}
async function startBilibiliQrLogin() {
  const button = $("start_bilibili_qr");
  if (button.disabled) return;
  stopBilibiliQrPolling();
  button.disabled = true;
  button.textContent = "正在生成…";
  $("bilibili_qr_panel").hidden = false;
  $("bilibili_qr_message").textContent = "正在申请登录二维码…";
  try {
    const result = await request("/api/bilibili/qr", "POST", {});
    bilibiliQrLoginId = result.login_id;
    bilibiliQrExpiresAt = Date.now() + Number(result.expires_in || 180) * 1000;
    $("bilibili_qr_image").src = result.image;
    $("bilibili_qr_message").textContent = "请使用哔哩哔哩 App 扫码";
    $("bilibili_login_status").textContent = "等待手机确认登录…";
    void pollBilibiliQrLogin(bilibiliQrLoginId);
  } catch (error) {
    $("bilibili_qr_panel").hidden = true;
    $("bilibili_login_status").textContent = "二维码生成失败：" + error.message;
    notice(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "生成登录二维码";
  }
}
async function pollBilibiliQrLogin(loginId) {
  if (!loginId || bilibiliQrLoginId !== loginId) return;
  if (Date.now() >= bilibiliQrExpiresAt) {
    bilibiliQrLoginId = "";
    $("bilibili_login_status").textContent = "二维码已过期，请刷新后重新扫码。";
    $("bilibili_qr_message").textContent = "二维码已过期";
    return;
  }
  try {
    const result = await request("/api/bilibili/qr/poll", "POST", {login_id:loginId});
    if (bilibiliQrLoginId !== loginId) return;
    if (result.status === "success") {
      stopBilibiliQrPolling(true);
      setBilibiliLoginStatus(true);
      notice("B 站扫码登录成功");
      return;
    }
    if (result.status === "expired") {
      bilibiliQrLoginId = "";
      $("bilibili_login_status").textContent = "二维码已失效，请刷新后重新扫码。";
      $("bilibili_qr_message").textContent = "二维码已失效";
      return;
    }
    const scanned = result.status === "scanned";
    $("bilibili_login_status").textContent = scanned ? "已扫码，请在手机上确认登录…" : "等待使用哔哩哔哩 App 扫码…";
    $("bilibili_qr_message").textContent = scanned ? "已扫码，请在手机上确认" : "请使用哔哩哔哩 App 扫码";
  } catch (error) {
    if (bilibiliQrLoginId !== loginId) return;
    $("bilibili_login_status").textContent = "查询登录状态失败，将自动重试：" + error.message;
  }
  if (bilibiliQrLoginId === loginId && Date.now() < bilibiliQrExpiresAt)
    bilibiliQrTimer = setTimeout(() => void pollBilibiliQrLogin(loginId), 1600);
}
$("start_bilibili_qr").onclick = () => startBilibiliQrLogin();
$("refresh_bilibili_qr").onclick = () => startBilibiliQrLogin();
$("clear_bilibili_cookies").onclick = async () => {
  const button = $("clear_bilibili_cookies");
  button.disabled = true;
  try {
    await request("/api/bilibili/cookies/clear", "POST");
    stopBilibiliQrPolling(true);
    setBilibiliLoginStatus(false);
    notice("B 站登录信息已清除");
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
};
$("open_settings").onclick = () => showSettings(true);
$("pause_task").onclick = () => toggleTaskPause();
$("logout").onclick = async () => {
  try { await request("/auth/logout", "POST", {}); }
  finally { window.location.replace("/"); }
};
$("close_settings").onclick = () => showSettings(false);
$("output_mode").onchange = updateOutputHint;
$("video_resolution").onchange = syncVideoSettingsControls;
$("video_bitrate_mbps").onchange = syncVideoSettingsControls;
$("pick_workspace").onclick = () => chooseFolder("workspace_folder").catch(e => notice("文件夹选择不可用，可手动填入绝对路径：" + e.message));
$("pick_output").onclick = () => chooseFolder("output_dir").catch(e => notice("文件夹选择不可用，可手动填入绝对路径：" + e.message));
$("pick_video_output").onclick = () => chooseFolder("video_output_dir").catch(e => notice("文件夹选择不可用，可手动填入绝对路径：" + e.message));
$("pick_notes_output").onclick = () => chooseFolder("notes_output_dir").catch(e => notice("文件夹选择不可用，可手动填入绝对路径：" + e.message));
$("pick_qwen3_asr_model").onclick = () => chooseFolder("qwen3_asr_model").catch(e => notice("模型文件夹选择不可用，可手动填入绝对路径：" + e.message));
$("pick_qwen3_aligner_model").onclick = () => chooseFolder("qwen3_aligner_model").catch(e => notice("模型文件夹选择不可用，可手动填入绝对路径：" + e.message));
$("folder_rows").onclick = event => {
  const rename = event.target.closest("[data-folder-rename]");
  if (rename) return renameFolder(rename.dataset.folderRename);
  const button = event.target.closest("[data-folder-path]");
  if (button) loadFolderPath(button.dataset.folderPath).catch(error => { $("folder_error").textContent = error.message; });
};
$("folder_up").onclick = () => loadFolderPath(folderParent).catch(error => { $("folder_error").textContent = error.message; });
$("folder_go").onclick = () => loadFolderPath($("folder_path").value.trim()).catch(error => { $("folder_error").textContent = error.message; });
$("folder_path").addEventListener("keydown",event => {
  if (event.key === "Enter") { event.preventDefault(); $("folder_go").click(); }
});
$("folder_cancel").onclick = () => $("folder_picker").close();
$("folder_create").onclick = () => createFolder();
$("folder_select").onclick = () => {
  if (!folderCurrent || !folderTargetId) return;
  $(folderTargetId).value = folderCurrent;
  if (folderTargetId === "output_dir") { $("output_mode").value = "custom"; updateOutputHint(); }
  $("folder_picker").close();
  notice("已选择文件夹：" + folderCurrent + "。点击所在卡片的保存按钮后生效");
};
$("import_folder").onclick = () => importFolder().catch(e => notice(e.message));
$("batch_select").onchange = async e => {
  currentBatch = e.target.value;
  setBatchPanelOpen(!!currentBatch);
  try {
    await loadBatches(currentBatch);
    const batch=batches.find(row => row.id === currentBatch);
    if (batch?.projects?.[0]) await openProject(batch.projects[0]);
  } catch (err) { notice(err.message); }
};
$("model_profiles").onclick = async event => {
  const scan = event.target.closest("[data-profile-scan]");
  if (scan) {
    const row = scan.closest(".model-profile-row");
    const base = row.querySelector("[data-profile-base]").value.trim();
    if (!base) { notice("请先填写模型 API 地址"); row.querySelector("[data-profile-base]").focus(); return; }
    const status = row.querySelector("[data-scan-status]");
    scan.disabled = true; status.textContent = "正在连接并扫描模型…";
    try {
      const profile = {
        profile_id: row.dataset.profileId,
        provider: row.querySelector("[data-profile-provider]").value,
        base_url: base,
        api_key: row.querySelector("[data-profile-key]").value,
      };
      const models = await request("/api/models/scan", "POST", profile);
      scannedModelsByProfile.set(profile.profile_id, models);
      const datalist = row.querySelector("datalist");
      datalist.replaceChildren(...models.map(model => { const option = document.createElement("option"); option.value = model.id; option.label = model.id + (model.active ? "（运行中）" : ""); return option; }));
      status.textContent = models.length ? `已扫描 ${models.length} 个可调用模型` : "API 已连接，但没有返回可调用模型";
      renderAvailableModels(currentModelProfiles());
    } catch (error) {
      status.textContent = "扫描失败：" + error.message;
      notice(error.message);
    } finally { scan.disabled = false; }
    return;
  }
  const button = event.target.closest("button[data-profile-up],button[data-profile-down],button[data-profile-remove]");
  if (!button) return;
  const profiles = currentModelProfiles(), roles = currentModelRoles();
  const index = profiles.findIndex(row => row.id === button.closest(".model-profile-row").dataset.profileId);
  if (index < 0) return;
  if (button.hasAttribute("data-profile-remove")) {
    const removed = profiles.splice(index,1)[0];
    scannedModelsByProfile.delete(removed.id);
    for (const role of Object.keys(roles)) roles[role] = roles[role].filter(item =>
      (item && typeof item === "object" ? item.profile_id : item) !== removed.id);
  } else {
    const next = index + (button.hasAttribute("data-profile-up") ? -1 : 1);
    if (next >= 0 && next < profiles.length) [profiles[index],profiles[next]] = [profiles[next],profiles[index]];
  }
  renderModelChoices(profiles,roles);
};
$("available_models").onclick = event => {
  const button = event.target.closest("[data-available-model]");
  if (!button) return;
  const roles = currentModelRoles(), selected = roles[activeModelRole];
  const duplicate = selected.some(item => item.profile_id === button.dataset.profileId && item.model === button.dataset.modelId);
  if (duplicate) { notice("该模型已在当前任务列表中"); return; }
  if (selected.length >= 40) { notice("每项任务最多选择 40 个模型"); return; }
  selected.push({profile_id:button.dataset.profileId, model:button.dataset.modelId});
  renderModelRoleLists(currentModelProfiles(),roles);
};
document.querySelectorAll("[data-role-select]").forEach(button => {
  button.onclick = () => {
    activeModelRole = button.dataset.roleSelect;
    renderModelRoleLists(currentModelProfiles(),currentModelRoles());
  };
});
for (const role of ["editing","summary","vision"]) {
  const list = $(role + "_model_list");
  list.onclick = event => {
    const button = event.target.closest("[data-role-up],[data-role-down],[data-role-remove]");
    if (!button) return;
    const item = button.closest(".model-role-item");
    const index = [...list.children].indexOf(item);
    const roles = currentModelRoles(), items = roles[role];
    if (index < 0 || index >= items.length) return;
    if (button.hasAttribute("data-role-remove")) items.splice(index,1);
    else {
      const next = index + (button.hasAttribute("data-role-up") ? -1 : 1);
      if (next < 0 || next >= items.length) return;
      [items[index],items[next]] = [items[next],items[index]];
    }
    renderModelRoleLists(currentModelProfiles(),roles);
  };
}
$("add_model_profile").onclick = () => {
  const profiles = currentModelProfiles();
  const id = "m" + Date.now().toString(36) + Math.random().toString(36).slice(2,8);
  profiles.push({id,name:"",model:"",provider:"local",base_url:"http://127.0.0.1:8787/v1",local_model_lifecycle:"gateway",api_key:"",api_key_set:false});
  renderModelChoices(profiles,currentModelRoles());
  const rows = document.querySelectorAll("#model_profiles .model-profile-row");
  rows[rows.length-1]?.querySelector("[data-profile-name]").focus();
};
$("model_profiles").onchange = event => {
  const row = event.target.closest(".model-profile-row");
  if (!row) return;
  if (event.target.matches("[data-profile-provider]")) {
    const online = event.target.value === "online";
    row.querySelector(".profile-lifecycle").hidden = online;
    row.querySelector("[data-profile-base]").placeholder = online ? "https://api.example.com/v1" : "http://127.0.0.1:8787/v1";
    row.querySelector("[data-profile-key]").placeholder = online ? "填写服务商 API Key" : "服务需要认证时填写";
    scannedModelsByProfile.delete(row.dataset.profileId);
    row.querySelector("datalist").replaceChildren();
    row.querySelector("[data-scan-status]").textContent = "服务类型已更改，请重新扫描";
  } else if (event.target.matches("[data-profile-base]")) {
    scannedModelsByProfile.delete(row.dataset.profileId);
    row.querySelector("datalist").replaceChildren();
    row.querySelector("[data-scan-status]").textContent = "API 地址已更改，请重新扫描";
  }
  renderAvailableModels(currentModelProfiles());
};
$("model_profiles").oninput = event => {
  if (!event.target.matches("[data-profile-name],[data-profile-model]")) return;
  const profiles = currentModelProfiles();
  renderAvailableModels(profiles);
  if (event.target.matches("[data-profile-name]")) renderModelRoleLists(profiles,currentModelRoles());
};
$("drop").onclick = () => $("video").click();
$("drop").onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("video").click(); } };
for (const event of ["dragover","dragleave","drop"]) {
  $("drop").addEventListener(event,e => {
    e.preventDefault();
    $("drop").classList.toggle("drag",event === "dragover");
    if (event === "drop") uploadDroppedItems(e.dataTransfer);
  });
}
$("video").onchange = e => { if (e.target.files.length) upload(e.target.files); e.target.value = ""; };
$("subtitle").onchange = async e => {
  if (!current || !e.target.files[0]) return;
  try {
    const value = await request("/api/projects/" + current + "/subtitles","POST",e.target.files[0]);
    notice("导入 " + value.count + " 段字幕");
    await openProject(current);
  } catch (error) { notice(error.message); }
};
for (const action of ["transcribe","analyze","export","punctuate"])
  $(action).onclick = () => ($(action).dataset.stopActive === "true" ? stopCurrent() : start(action)).catch(e => notice(e.message));
for (const button of document.querySelectorAll("[data-summary-mode]"))
  button.onclick = () => start("summary",{summary_mode:button.dataset.summaryMode}).catch(e => notice(e.message));
$("summary_stop").onclick=() => stopCurrent().catch(e => notice(e.message));
$("generate_document").onclick = () => {
  if ($("generate_document").dataset.stopActive === "true") return stopCurrent().catch(e => notice(e.message));
  runSelectedTask("document").catch(e => notice(e.message));
};
$("generate_timestamp_document").onclick = () => {
  if ($("generate_timestamp_document").dataset.stopActive === "true") return stopCurrent().catch(e => notice(e.message));
  runSelectedTask("timestamp_document").catch(e => notice(e.message));
};
$("finish_all").onclick = async () => {
  if ($("finish_all").dataset.stopActive === "true") return stopCurrent().catch(e => notice(e.message));
  runSelectedTask("finish_all").catch(error => notice(error.message));
};
$("open_finished_folder").onclick = async () => {
  const batch=batches.find(row => row.id === currentBatch);
  const projectId=batch?.projects?.[0] || current;
  if (!projectId) return;
  try { await revealProjectFolder(projectId); }
  catch (error) { notice(error.message); }
};
document.addEventListener("mouseup", addSelectedTextSuggestion);
$("import_panel").onclick = () => setImportPanelOpen($("import_content").hidden);
$("batch_toggle").onclick = () => setBatchPanelOpen($("batch_content").hidden);
document.querySelectorAll("details").forEach(element => element.addEventListener("toggle", saveUIState));
$("segments").addEventListener("click", event => {
  if (Date.now() < suppressTranscriptClickUntil) return;
  const pause = event.target.closest(".pause-marker");
  if (pause) {
    togglePause(Number(pause.dataset.pauseStart),Number(pause.dataset.pauseEnd));
    return;
  }
  const token = event.target.closest(".transcript-token.deleted, .transcript-token.suggested");
  if (!token || interactionBusy) return;
  const overlaps = currentSuggestions.filter(row => row.category!=="pause" &&
    row.start < Number(token.dataset.end) && row.end > Number(token.dataset.start));
  const matches = overlaps.filter(row => !!row.selected);
  if (!matches.length) {
    const candidates=overlaps.filter(row => !row.selected);
    if (!candidates.length) return;
    const groups=new Set(candidates.map(row => row.group_id || row.id));
    currentSuggestions.filter(row => !row.selected && groups.has(row.group_id || row.id)).forEach(row => { row.selected = true; });
    renderTranscript(currentSegments);
    saveTranscriptReview("已采纳剪辑建议");
    return;
  }
  const groups=new Set(matches.map(row => row.group_id || row.id));
  currentSuggestions.filter(row => row.selected && groups.has(row.group_id || row.id)).forEach(row => { row.selected = false; });
  renderTranscript(currentSegments);
  saveTranscriptReview("已恢复整条删除建议");
});
const initialUIState = readUIState();
Promise.all([loadSession(),loadAccess(),loadSettings(),loadProjects(),loadBatches(initialUIState?.batchId || ""),loadTaskControl()])
  .then(() => restoreUIState(initialUIState))
  .catch(e => notice(e.message));
setInterval(() => {
  if (current && !interactionBusy) openProject(current).catch(() => {});
  loadProjects().catch(() => {});
  loadBatches().catch(() => {});
  loadTaskControl().catch(() => {});
},4000);
