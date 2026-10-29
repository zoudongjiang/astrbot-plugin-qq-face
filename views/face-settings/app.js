const bridge = window.AstrBotPluginView;

const CHIP_LIMIT = 40;

const el = (id) => document.getElementById(id);

const ui = {
  enabled: el("enabled"),
  enabledLabel: el("enabledLabel"),
  enabledCount: el("enabledCount"),
  modePrompt: el("modePrompt"),
  modeEmotion: el("modeEmotion"),
  schemeSelect: el("schemeSelect"),
  schemeName: el("schemeName"),
  schemeHint: el("schemeHint"),
  schemeAdd: el("schemeAdd"),
  schemeDelete: el("schemeDelete"),
  selectedCount: el("selectedCount"),
  selectAll: el("selectAll"),
  selectNone: el("selectNone"),
  allowOutside: el("allowOutside"),
  packs: el("packs"),
  aliases: el("aliases"),
  catalogHint: el("catalogHint"),
  faceModeAll: el("faceModeAll"),
  faceModeRandom: el("faceModeRandom"),
  faceCount: el("faceCount"),
  prompt: el("prompt"),
  promptReset: el("promptReset"),
  promptPreview: el("promptPreview"),
  probability: el("probability"),
  probabilityValue: el("probabilityValue"),
  max: el("max_per_reply"),
  maxValue: el("maxValue"),
  cooldown: el("cooldown_seconds"),
  cooldownValue: el("cooldownValue"),
  hourly: el("hourly_limit"),
  hourlyValue: el("hourlyValue"),
  stats: el("stats"),
  save: el("save"),
  reset: el("reset"),
  refresh: el("refresh"),
  toast: el("toast"),
};

let state = null;
let schemes = [];
let activeId = "";
let selected = new Set();
let aliases = {};
let faceNames = new Map();
let expanded = new Set();
let chipNodes = new Map();
let toastTimer = 0;
let previewTimer = 0;

function toast(message, isError = false) {
  ui.toast.textContent = message;
  ui.toast.classList.toggle("error", isError);
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    ui.toast.hidden = true;
  }, 2600);
}

function activeScheme() {
  return schemes.find((scheme) => scheme.id === activeId) || schemes[0];
}

function commitScheme() {
  const scheme = activeScheme();
  if (scheme) {
    scheme.faces = [...selected].sort((a, b) => a - b);
  }
}

function syncLabels() {
  ui.probabilityValue.textContent = `${ui.probability.value}%`;
  ui.maxValue.textContent = ui.max.value;
  ui.cooldownValue.textContent = ui.cooldown.value;
  ui.hourlyValue.textContent = ui.hourly.value;
  ui.enabledLabel.textContent = ui.enabled.checked ? "启用" : "停用";
}

function setDisabled(disabled) {
  [
    ui.save,
    ui.reset,
    ui.refresh,
    ui.enabled,
    ui.probability,
    ui.max,
    ui.cooldown,
    ui.hourly,
    ui.prompt,
    ui.promptReset,
    ui.modePrompt,
    ui.schemeAdd,
    ui.schemeDelete,
    ui.schemeSelect,
    ui.schemeName,
    ui.selectAll,
    ui.selectNone,
    ui.allowOutside,
    ui.faceModeAll,
    ui.faceModeRandom,
    ui.faceCount,
  ].forEach((node) => {
    node.disabled = disabled;
  });
}

function defaultPrompt() {
  return state?.settings?.default_prompt || "";
}

function existingFaces() {
  const ids = new Set();
  (state?.packs || []).forEach((pack) => {
    (pack.faces || []).forEach((face) => ids.add(Number(face.id)));
  });
  return ids;
}

function applySettings(settings) {
  ui.enabled.checked = Boolean(settings.enabled);
  ui.modePrompt.checked = settings.mode !== "emotion";
  ui.probability.value = Math.round(Number(settings.probability) * 100);
  ui.max.value = Number(settings.max_per_reply);
  ui.cooldown.value = Number(settings.cooldown_seconds);
  ui.hourly.value = Number(settings.hourly_limit);
  ui.prompt.value = settings.prompt_template || defaultPrompt();

  const randomMode = settings.prompt_faces_mode === "random";
  ui.faceModeRandom.checked = randomMode;
  ui.faceModeAll.checked = !randomMode;
  ui.faceCount.value = Number(settings.prompt_faces_count || 12);
  ui.faceCount.disabled = !randomMode;

  schemes = (settings.schemes || []).map((scheme) => ({
    id: scheme.id,
    name: scheme.name,
    faces: [...(scheme.faces || [])],
  }));
  if (!schemes.length) {
    schemes = [{ id: "default", name: "默认小黄脸", faces: [] }];
  }
  activeId = settings.active_scheme || schemes[0].id;
  if (!schemes.some((scheme) => scheme.id === activeId)) {
    activeId = schemes[0].id;
  }
  aliases = { ...(settings.aliases || {}) };
  ui.allowOutside.checked = settings.allow_outside_scheme !== false;
  selected = new Set(activeScheme().faces || []);
  renderAll();
}

function renderAll() {
  renderSchemeBar();
  renderPacks();
  renderAliases();
  updateCount();
  syncLabels();
}

function renderSchemeBar() {
  ui.schemeSelect.textContent = "";
  schemes.forEach((scheme) => {
    const option = document.createElement("option");
    option.value = scheme.id;
    option.textContent = `${scheme.name}（${scheme.faces.length}）`;
    ui.schemeSelect.appendChild(option);
  });
  ui.schemeSelect.value = activeId;
  ui.schemeName.value = activeScheme()?.name || "";
  ui.schemeHint.textContent = `${schemes.length} 个方案`;
}

function imageUrl(url) {
  if (typeof url !== "string" || !/^https?:\/\//i.test(url)) {
    return "";
  }
  if (/\.zip(\?|$)/i.test(url)) {
    return "";
  }
  return url;
}

function renderPacks() {
  ui.packs.textContent = "";
  chipNodes = new Map();
  const packs = state?.packs || [];
  if (!packs.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "没有读到表情包目录。";
    ui.packs.appendChild(empty);
    return;
  }

  packs.forEach((pack) => {
    const card = document.createElement("div");
    card.className = "pack";

    const head = document.createElement("div");
    head.className = "pack-head";

    const name = document.createElement("span");
    name.className = "pack-name";
    name.textContent = pack.pack_name;

    const meta = document.createElement("span");
    meta.className = "pack-meta";
    meta.textContent = `${(pack.faces || []).length} 个${pack.builtin ? " · 内置" : ""}`;

    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "mini";
    pick.textContent = "全选本包";
    pick.addEventListener("click", () => togglePack(pack, true));

    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "mini";
    drop.textContent = "取消本包";
    drop.addEventListener("click", () => togglePack(pack, false));

    head.append(name, meta, pick, drop);
    card.appendChild(head);

    const chips = document.createElement("div");
    chips.className = "chips";
    renderChips(chips, pack);
    card.appendChild(chips);
    ui.packs.appendChild(card);
  });
}

function renderChips(container, pack) {
  const faces = pack.faces || [];
  const showAll = expanded.has(pack.pack_name);
  const visible = showAll ? faces : faces.slice(0, CHIP_LIMIT);

  visible.forEach((face) => {
    const id = Number(face.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "face";
    button.title = face.name || `#${id}`;
    button.dataset.id = String(id);
    if (selected.has(id)) {
      button.classList.add("on");
    }

    const url = imageUrl(face.url);
    if (url) {
      const img = document.createElement("img");
      img.src = url;
      img.alt = face.name || "";
      img.loading = "lazy";
      img.addEventListener("error", () => {
        img.remove();
        button.textContent = face.name || `#${id}`;
      });
      button.appendChild(img);
      const label = document.createElement("span");
      label.className = "face-label";
      label.textContent = face.name || `#${id}`;
      button.appendChild(label);
    } else {
      button.textContent = face.name || `#${id}`;
    }

    button.addEventListener("click", () => toggleFace(id, button));
    chipNodes.set(id, button);
    container.appendChild(button);
  });

  if (faces.length > CHIP_LIMIT) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "face more";
    more.textContent = showAll ? "收起" : `+${faces.length - CHIP_LIMIT}`;
    more.addEventListener("click", () => {
      if (showAll) {
        expanded.delete(pack.pack_name);
      } else {
        expanded.add(pack.pack_name);
      }
      renderPacks();
    });
    container.appendChild(more);
  }
}

function toggleFace(id, node) {
  if (selected.has(id)) {
    selected.delete(id);
    node.classList.remove("on");
  } else {
    selected.add(id);
    node.classList.add("on");
  }
  updateCount();
  renderAliases();
  schedulePreview();
}

function togglePack(pack, on) {
  (pack.faces || []).forEach((face) => {
    const id = Number(face.id);
    if (on) {
      selected.add(id);
    } else {
      selected.delete(id);
    }
    const node = chipNodes.get(id);
    if (node) {
      node.classList.toggle("on", on);
    }
  });
  updateCount();
  renderAliases();
  schedulePreview();
}

function updateCount() {
  ui.selectedCount.textContent = String(selected.size);
  ui.enabledCount.textContent = `已启用 ${selected.size} 个表情`;
}

function faceLabel(id) {
  return faceNames.get(id) || `#${id}`;
}

function renderAliases() {
  ui.aliases.textContent = "";
  if (!selected.size) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "还没有选表情。先在上面的表情方案里点选。";
    ui.aliases.appendChild(empty);
    return;
  }

  [...selected]
    .sort((a, b) => a - b)
    .forEach((id) => {
      const row = document.createElement("div");
      row.className = "alias-row";

      const label = document.createElement("span");
      label.className = "alias-label";
      label.textContent = faceLabel(id);

      const input = document.createElement("input");
      input.className = "alias-input";
      input.placeholder = "别名（可选）";
      input.value = aliases[String(id)] || "";
      input.addEventListener("input", () => {
        const value = input.value.trim();
        if (value) {
          aliases[String(id)] = value;
        } else {
          delete aliases[String(id)];
        }
        schedulePreview();
      });

      row.append(label, input);
      ui.aliases.appendChild(row);
    });
}

function renderMeta(data) {
  const stats = data.stats || {};
  ui.stats.textContent = `今日 ${stats.day_count ?? 0} 次 · 本小时 ${stats.hour_count ?? 0} 次 · 累计 ${stats.total ?? 0} 次`;

  if (data.using_builtin) {
    ui.catalogHint.textContent = "未连接 QQ，使用内置经典表情";
  } else {
    ui.catalogHint.textContent = "目录来自 SnowLuma";
  }
  if (data.error && data.using_builtin) {
    ui.catalogHint.textContent += `（${data.error}）`;
  }
}

function buildFaceNames() {
  faceNames = new Map();
  (state?.packs || []).forEach((pack) => {
    (pack.faces || []).forEach((face) => {
      const id = Number(face.id);
      if (!faceNames.has(id)) {
        faceNames.set(id, face.name || `#${id}`);
      }
    });
  });
}

function promptForSave() {
  const text = ui.prompt.value.trim();
  const fallback = String(defaultPrompt()).trim();
  return text === fallback ? "" : text;
}

function faceMode() {
  return ui.faceModeRandom.checked ? "random" : "all";
}

function buildPayload() {
  commitScheme();
  return {
    enabled: ui.enabled.checked,
    mode: ui.modePrompt.checked ? "prompt" : "emotion",
    prompt_template: promptForSave(),
    probability: Number(ui.probability.value) / 100,
    max_per_reply: Number(ui.max.value),
    cooldown_seconds: Number(ui.cooldown.value),
    hourly_limit: Number(ui.hourly.value),
    schemes,
    active_scheme: activeId,
    aliases,
    allow_outside_scheme: ui.allowOutside.checked,
    prompt_faces_mode: faceMode(),
    prompt_faces_count: Number(ui.faceCount.value) || 12,
  };
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(renderPreview, 320);
}

async function renderPreview() {
  if (!bridge || !state) {
    return;
  }
  try {
    const result = await bridge.apiPost("preview", buildPayload());
    ui.promptPreview.textContent = result.prompt || "（没有可渲染的提示词）";
  } catch (error) {
    ui.promptPreview.textContent = `预览失败：${error.message}`;
  }
}

async function loadState() {
  setDisabled(true);
  try {
    state = await bridge.apiGet("state");
    buildFaceNames();
    applySettings(state.settings || {});
    renderMeta(state);
    ui.promptPreview.textContent =
      state.prompt_preview || "（没有可渲染的提示词）";
  } catch (error) {
    toast(`读取失败：${error.message}`, true);
  } finally {
    setDisabled(false);
  }
}

async function save() {
  setDisabled(true);
  try {
    const result = await bridge.apiPost("settings", buildPayload());
    if (result?.settings) {
      state.settings = { ...state.settings, ...result.settings };
      applySettings(state.settings);
      ui.promptPreview.textContent = "";
    }
    renderMeta(state);
    schedulePreview();
    toast("已保存");
  } catch (error) {
    toast(`保存失败：${error.message}`, true);
  } finally {
    setDisabled(false);
  }
}

async function refreshCatalog() {
  setDisabled(true);
  ui.refresh.textContent = "刷新中…";
  try {
    const result = await bridge.apiGet("catalog", { refresh: 1 });
    if (state) {
      state.packs = result.packs || [];
      state.using_builtin = result.using_builtin;
      state.error = result.error;
    }
    buildFaceNames();
    renderPacks();
    renderAliases();
    renderMeta(state);
    if (result.prompt_preview) {
      ui.promptPreview.textContent = result.prompt_preview;
    }
    toast(`已读取 ${(result.packs || []).length} 个表情包`);
  } catch (error) {
    toast(`刷新失败：${error.message}`, true);
  } finally {
    ui.refresh.textContent = "刷新目录";
    setDisabled(false);
  }
}

function selectAllFaces() {
  existingFaces().forEach((id) => selected.add(id));
  chipNodes.forEach((node, id) => node.classList.toggle("on", selected.has(id)));
  updateCount();
  renderAliases();
  schedulePreview();
}

function selectNoFaces() {
  selected.clear();
  chipNodes.forEach((node) => node.classList.remove("on"));
  updateCount();
  renderAliases();
  schedulePreview();
}

function switchScheme(id) {
  commitScheme();
  activeId = id;
  selected = new Set(activeScheme().faces || []);
  renderAll();
  schedulePreview();
}

function addScheme() {
  commitScheme();
  const id = `s${Date.now().toString(36)}`;
  schemes.push({ id, name: `方案 ${schemes.length + 1}`, faces: [] });
  activeId = id;
  selected = new Set();
  renderAll();
  schedulePreview();
}

function deleteScheme() {
  if (schemes.length <= 1) {
    toast("至少保留一个方案", true);
    return;
  }
  schemes = schemes.filter((scheme) => scheme.id !== activeId);
  activeId = schemes[0].id;
  selected = new Set(activeScheme().faces || []);
  renderAll();
  schedulePreview();
}

function resetDraft() {
  const fallback = state?.settings?.default_scheme;
  if (fallback) {
    schemes = [
      { id: fallback.id, name: fallback.name, faces: [...fallback.faces] },
    ];
    activeId = fallback.id;
    selected = new Set(fallback.faces);
    aliases = {};
  }
  ui.enabled.checked = true;
  ui.modePrompt.checked = true;
  ui.probability.value = 50;
  ui.max.value = 2;
  ui.cooldown.value = 30;
  ui.hourly.value = 40;
  ui.faceModeAll.checked = true;
  ui.faceCount.value = 12;
  ui.faceCount.disabled = true;
  ui.allowOutside.checked = true;
  ui.prompt.value = defaultPrompt();
  renderAll();
  syncLabels();
  schedulePreview();
  toast("已恢复默认值，点保存生效");
}

function resetPrompt() {
  ui.prompt.value = defaultPrompt();
  schedulePreview();
  toast("已填回默认提示词");
}

async function main() {
  if (!bridge) {
    ui.catalogHint.textContent = "请在 AstrBot 插件页里打开这个视图。";
    return;
  }
  await bridge.ready();
  await loadState();

  ui.enabled.addEventListener("change", () => {
    syncLabels();
    schedulePreview();
  });
  ui.probability.addEventListener("input", () => {
    syncLabels();
    schedulePreview();
  });
  ui.max.addEventListener("input", () => {
    syncLabels();
    schedulePreview();
  });
  ui.cooldown.addEventListener("input", syncLabels);
  ui.hourly.addEventListener("input", syncLabels);
  ui.prompt.addEventListener("input", schedulePreview);

  ui.faceModeAll.addEventListener("change", () => {
    ui.faceCount.disabled = true;
    schedulePreview();
  });
  ui.faceModeRandom.addEventListener("change", () => {
    ui.faceCount.disabled = false;
    schedulePreview();
  });
  ui.faceCount.addEventListener("input", () => {
    ui.faceCount.disabled = false;
    schedulePreview();
  });

  ui.schemeSelect.addEventListener("change", () => switchScheme(ui.schemeSelect.value));
  ui.schemeName.addEventListener("input", () => {
    const scheme = activeScheme();
    if (scheme) {
      scheme.name = ui.schemeName.value.slice(0, 24);
      const option = ui.schemeSelect.selectedOptions[0];
      if (option) {
        option.textContent = `${scheme.name}（${scheme.faces.length}）`;
      }
    }
  });
  ui.schemeAdd.addEventListener("click", addScheme);
  ui.schemeDelete.addEventListener("click", deleteScheme);
  ui.selectAll.addEventListener("click", selectAllFaces);
  ui.selectNone.addEventListener("click", selectNoFaces);
  ui.allowOutside.addEventListener("change", schedulePreview);

  ui.save.addEventListener("click", save);
  ui.reset.addEventListener("click", resetDraft);
  ui.refresh.addEventListener("click", refreshCatalog);
  ui.promptReset.addEventListener("click", resetPrompt);
}

main();
