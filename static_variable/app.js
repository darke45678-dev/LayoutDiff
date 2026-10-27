/**
 * Variable Layout Inspector & Builder Frontend Logic
 */

let allLayouts = [];
let currentLayout = null;
let currentInspectionResult = null;
let inspectFile = null;
let inspectFilter = "ALL";
let currentCropMode = "composite";
let selectedCell = null;

// Builder state
let builderImage = new Image();
let builderLayout = null;
let builderScale = 1.0;
let builderSelectedBox = null;
let isDrawingBox = false;
let drawStartPos = null;
let builderAutoLayout = null;
let builderWarnings = [];
let builderErrors = [];
let builderAddMode = false;
let builderPanMode = false;
let builderDrag = null;
let candidateCells = [];
let candidateSourceVisibility = { image_search: true, grid_inferred: true };
let builderUploadId = null;
let builderValidationTimer = null;
let builderValidationRevision = 0;
let builderCanvasResizeObserver = null;

// DOM Ready
document.addEventListener("DOMContentLoaded", () => {
  const portBadge = document.getElementById("port-badge");
  if (portBadge) portBadge.textContent = `Port ${window.location.port || (window.location.protocol === "https:" ? 443 : 80)}`;
  initTabs();
  loadLayouts();
  initInspectorEvents();
  initBuilderEvents();
});

// ---------------- Tab Navigation ----------------
function initTabs() {
  const tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(tc => tc.classList.remove("active"));
      btn.classList.add("active");
      const targetId = btn.dataset.tab;
      document.getElementById(targetId).classList.add("active");
      if (targetId.includes("builder") || targetId.includes("build")) {
        requestAnimationFrame(applyBuilderCanvasZoom);
      }
    });
  });
}

// ---------------- Layouts Management ----------------
async function loadLayouts(preselectId = null) {
  try {
    const res = await fetch("/api/layouts");
    const data = await res.json();
    allLayouts = data.layouts || [];

    const select = document.getElementById("inspect-layout-select");
    select.innerHTML = '<option value="">-- 請選取版型 --</option>';

    const demoGroup = document.createElement("optgroup");
    demoGroup.label = "合成示範版型（測試資料）";
    allLayouts.forEach(layout => {
      const opt = document.createElement("option");
      opt.value = layout.id;
      opt.textContent = `${layout.name} (${layout.die_count} 模)`;
      demoGroup.appendChild(opt);
    });
    if (demoGroup.children.length) select.appendChild(demoGroup);

    if (preselectId) {
      select.value = preselectId;
      onLayoutSelected(preselectId);
    } else if (allLayouts.length > 0) {
      const defaultLayout = allLayouts[0];
      select.value = defaultLayout.id;
      onLayoutSelected(defaultLayout.id);
    }
  } catch (err) {
    console.error("Failed to load layouts:", err);
  }
}

async function onLayoutSelected(layoutId) {
  if (!layoutId) return;
  try {
    const res = await fetch(`/api/layouts/${layoutId}`);
    currentLayout = await res.json();

    // Update layout summary card
    document.getElementById("sum-name").textContent = currentLayout.name || currentLayout.layout_id;
    document.getElementById("sum-dies").textContent = `${currentLayout.cells.length} 模`;
    const spec = currentLayout.image_spec || {};
    document.getElementById("sum-spec").textContent = `${spec.width} × ${spec.height} px`;

    const groups = Array.from(new Set(currentLayout.cells.map(c => c.size_group || "default")));
    document.getElementById("sum-groups").textContent = groups.join(", ");

    // Update 4 fiducials previews
    ["top_left", "top_right", "bottom_left", "bottom_right"].forEach(c => {
      const imgEl = document.getElementById(`fid-img-${c.replace("top_", "t").replace("bottom_", "b").replace("left", "l").replace("right", "r")}`);
      if (imgEl) {
        imgEl.src = `/api/layouts/${layoutId}/fiducial/${c}?t=${Date.now()}`;
      }
    });

    // Show only quick samples that actually exist for this layout.
    const quickSelect = document.getElementById("quick-test-select");
    quickSelect.innerHTML = '<option value="">-- 此版型沒有快速測試樣品 --</option>';
    try {
      const sampleRes = await fetch("/api/quick_samples");
      const sampleData = await sampleRes.json();
      const available = (sampleData.samples || []).filter(file => file.startsWith(`${layoutId}_`));
      if (available.length) {
        quickSelect.innerHTML = '<option value="">-- 點此選取快速測試樣品 --</option>';
        available.forEach(file => {
          const opt = document.createElement("option");
          opt.value = file;
          const variant = file.match(/_(V\d+)\.[^.]+$/i)?.[1];
          const descriptions = {
            V1: "原版自我比對（預期 PASS）",
            V2: "注入模位瑕疵（預期 FAIL）",
            V3: "整版平移（測試對位）",
            V4: "準星毀損（預期 REVIEW）",
            V5: "次像素平移（觀察重採樣差異）"
          };
          opt.textContent = `${variant || "樣品"}: ${descriptions[variant] || file}`;
          quickSelect.appendChild(opt);
        });
      }
    } catch (err) {
      console.warn("Could not load quick samples:", err);
    }

    document.getElementById("btn-run-inspect").disabled = false;
  } catch (err) {
    console.error("Failed to load layout details:", err);
  }
}

// ---------------- Inspector Logic ----------------
function initInspectorEvents() {
  const layoutSelect = document.getElementById("inspect-layout-select");
  layoutSelect.addEventListener("change", (e) => onLayoutSelected(e.target.value));

  const dropZone = document.getElementById("inspect-drop-zone");
  const fileInput = document.getElementById("inspect-file-input");
  const filenameLabel = document.getElementById("inspect-selected-filename");
  const quickSelect = document.getElementById("quick-test-select");

  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      inspectFile = e.dataTransfer.files[0];
      filenameLabel.textContent = inspectFile.name;
      quickSelect.value = "";
      document.getElementById("btn-run-inspect").disabled = false;
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files.length > 0) {
      inspectFile = e.target.files[0];
      filenameLabel.textContent = inspectFile.name;
      quickSelect.value = "";
      document.getElementById("btn-run-inspect").disabled = false;
    }
  });

  quickSelect.addEventListener("change", () => {
    if (quickSelect.value) {
      inspectFile = null;
      filenameLabel.textContent = `[假資料] ${quickSelect.value}`;
      document.getElementById("btn-run-inspect").disabled = false;
    }
  });

  // Run Inspect Button
  document.getElementById("btn-run-inspect").addEventListener("click", runInspection);

  // Filter Pills
  document.querySelectorAll(".die-navigator-box .pill").forEach(pill => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".die-navigator-box .pill").forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      inspectFilter = pill.dataset.filter;
      renderDieTable();
    });
  });

  // Zoom Buttons
  document.getElementById("btn-zoom-fit").addEventListener("click", () => {
    const img = document.getElementById("inspect-preview-img");
    img.style.maxWidth = "100%";
    img.style.maxHeight = "100%";
    img.style.width = "auto";
  });
  document.getElementById("btn-zoom-100").addEventListener("click", () => {
    const img = document.getElementById("inspect-preview-img");
    img.style.maxWidth = "none";
    img.style.maxHeight = "none";
    img.style.width = "1800px";
  });

  // 1:1 Crop Mode Switcher in Card
  document.querySelectorAll("#card-view-modes .pill").forEach(pill => {
    pill.addEventListener("click", () => {
      currentCropMode = pill.dataset.cropmode;
      updateCellCropImages();
    });
  });

  // 1:1 Crop Mode Switcher in Modal
  document.querySelectorAll("#modal-view-modes .pill").forEach(pill => {
    pill.addEventListener("click", () => {
      currentCropMode = pill.dataset.cropmode;
      updateCellCropImages();
    });
  });

  // 1:1 Pixel Modal open/close
  const pixelModal = document.getElementById("pixel-modal");
  const openModalBtn = document.getElementById("btn-open-pixel-modal");
  const closeModalBtn = document.getElementById("btn-close-modal");
  const cardCropImg = document.getElementById("dt-crop-img");

  function openPixelModal() {
    if (!selectedCell) return;
    const modalTitle = document.getElementById("modal-title");
    if (modalTitle) {
      modalTitle.textContent = `模位 #${selectedCell.id} 1:1 原始無損像素對照 (${selectedCell.w}×${selectedCell.h} px 原圖取樣 - 差異 ${selectedCell.difference_pixel_count} px)`;
    }
    pixelModal.classList.remove("hidden");
    updateCellCropImages();
  }

  function closePixelModal() {
    pixelModal.classList.add("hidden");
  }

  if (openModalBtn) openModalBtn.addEventListener("click", openPixelModal);
  if (cardCropImg) cardCropImg.addEventListener("click", openPixelModal);
  if (closeModalBtn) closeModalBtn.addEventListener("click", closePixelModal);

  if (pixelModal) {
    pixelModal.addEventListener("click", (e) => {
      if (e.target === pixelModal) closePixelModal();
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pixelModal && !pixelModal.classList.contains("hidden")) {
      closePixelModal();
    }
  });
}

async function runInspection() {
  const btn = document.getElementById("btn-run-inspect");
  const quickSelect = document.getElementById("quick-test-select");
  if (!currentLayout) return;

  btn.disabled = true;
  btn.textContent = "⏳ 檢測中 (零容差差分計算)...";

  try {
    let res;
    if (inspectFile) {
      const formData = new FormData();
      formData.append("file", inspectFile);
      res = await fetch(`/api/inspect?layout_id=${currentLayout.layout_id}`, {
        method: "POST",
        body: inspectFile
      });
    } else if (quickSelect.value) {
      res = await fetch("/api/inspect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          layout_id: currentLayout.layout_id,
          sample_file: quickSelect.value
        })
      });
    } else {
      alert("請上傳待測檔案或選取快速測試樣品！");
      btn.disabled = false;
      btn.textContent = "▶ 執行零容差逐像素檢測";
      return;
    }

    const result = await res.json();
    currentInspectionResult = result;
    displayInspectionResults(result);

  } catch (err) {
    alert("檢測執行失敗: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ 執行零容差逐像素檢測";
  }
}

function displayInspectionResults(data) {
  const banner = document.getElementById("result-banner");
  banner.className = `result-banner ${data.status}`;

  document.getElementById("banner-status-badge").textContent = data.status;
  document.getElementById("banner-status-desc").textContent = data.message;

  const alignBadge = document.getElementById("banner-align-badge");
  alignBadge.textContent = data.alignment?.badge || "-";
  if (data.alignment?.shift) {
    alignBadge.textContent += ` (X:${data.alignment.shift[0].toFixed(1)}, Y:${data.alignment.shift[1].toFixed(1)})`;
  }

  document.getElementById("banner-total-dies").textContent = data.total_cells || 0;
  document.getElementById("banner-pass-dies").textContent = data.pass_count || 0;
  document.getElementById("banner-fail-dies").textContent = data.fail_count || 0;
  document.getElementById("banner-elapsed").textContent = `${data.elapsed_ms || 0} ms`;

  // Update preview image
  const previewImg = document.getElementById("inspect-preview-img");
  previewImg.src = `${data.preview_url}?t=${Date.now()}`;

  // Update die table counters
  document.getElementById("cnt-all").textContent = data.total_cells || 0;
  document.getElementById("cnt-fail").textContent = data.fail_count || 0;
  document.getElementById("cnt-pass").textContent = data.pass_count || 0;

  renderDieTable();
}

function renderDieTable() {
  const tbody = document.getElementById("die-table-body");
  tbody.innerHTML = "";

  if (!currentInspectionResult || !currentInspectionResult.cells) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-hint">無資料</td></tr>';
    return;
  }

  let list = currentInspectionResult.cells;
  if (inspectFilter === "FAIL") {
    list = list.filter(c => c.verdict === "FAIL");
  } else if (inspectFilter === "PASS") {
    list = list.filter(c => c.verdict === "PASS");
  }

  if (list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-hint">無 ${inspectFilter} 項目</td></tr>`;
    return;
  }

  list.forEach(c => {
    const tr = document.createElement("tr");
    tr.className = `die-row row-${c.verdict.toLowerCase()}`;
    tr.innerHTML = `
      <td><strong>#${c.id}</strong></td>
      <td><span class="badge-dies">${c.size_group || "default"}</span></td>
      <td>(${c.x}, ${c.y}, ${c.w}×${c.h})</td>
      <td><strong>${c.difference_pixel_count}</strong> px</td>
      <td><strong>${c.verdict}</strong></td>
    `;
    tr.addEventListener("click", () => selectDie(c, tr));
    tbody.appendChild(tr);
  });
}

function selectDie(cell, trEl) {
  selectedCell = cell;
  document.querySelectorAll(".die-row").forEach(r => r.classList.remove("selected"));
  trEl.classList.add("selected");

  const card = document.getElementById("die-detail-card");
  card.classList.remove("hidden");
  document.getElementById("dt-id").textContent = cell.id;
  const verdictEl = document.getElementById("dt-verdict");
  verdictEl.textContent = cell.verdict;
  verdictEl.className = `badge-verdict ${cell.verdict}`;
  const dimEl = document.getElementById("dt-dim");
  if (dimEl) dimEl.textContent = `${cell.w} × ${cell.h}`;
  document.getElementById("dt-px").textContent = cell.difference_pixel_count;

  const defectList = document.getElementById("dt-defects-list");
  defectList.innerHTML = "";

  if (cell.defects && cell.defects.length > 0) {
    cell.defects.forEach((d, idx) => {
      const p = document.createElement("p");
      p.style.fontSize = "11px";
      p.style.color = "#f87171";
      p.textContent = `瑕疵區塊 #${idx + 1}: 位置 (${d.bbox[0]}, ${d.bbox[1]}), 尺寸 ${d.bbox[2]}×${d.bbox[3]} px, 面積 ${d.area_px} px`;
      defectList.appendChild(p);
    });
  } else {
    defectList.innerHTML = '<span style="color:#10b981; font-size:11px;">零差異像素，檢測全數合格</span>';
  }

  updateCellCropImages();
}

function updateCellCropImages() {
  if (!selectedCell) return;
  const cropUrl = `/api/inspect/cell_crop?cell_id=${selectedCell.id}&mode=${currentCropMode}&t=${Date.now()}`;

  const cardImg = document.getElementById("dt-crop-img");
  if (cardImg) {
    cardImg.src = cropUrl;
  }

  const modalImg = document.getElementById("modal-crop-img");
  if (modalImg) {
    modalImg.src = cropUrl;
  }

  // Synchronize active states across card & modal pill selectors
  document.querySelectorAll("#card-view-modes .pill, #modal-view-modes .pill").forEach(p => {
    if (p.dataset.cropmode === currentCropMode) {
      p.classList.add("active");
    } else {
      p.classList.remove("active");
    }
  });
}

// ---------------- Builder Logic ----------------
function initBuilderEvents() {
  const dropZone = document.getElementById("builder-drop-zone");
  const fileInput = document.getElementById("builder-file-input");
  const filenameLabel = document.getElementById("builder-selected-filename");
  const detectBtn = document.getElementById("btn-builder-detect");
  const saveBtn = document.getElementById("btn-builder-save");
  const redetectBtn = document.getElementById("btn-builder-redetect");
  const findCandidatesBtn = document.getElementById("btn-find-same-size");

  let builderFile = null;

  const invalidateBuilderSource = () => {
    builderUploadId = null;
    builderLayout = null;
    builderAutoLayout = null;
    builderSelectedBox = null;
    clearBuilderCandidates();
    builderErrors = [];
    document.getElementById("builder-review-confirm").checked = false;
    document.getElementById("builder-review-confirm").disabled = true;
    saveBtn.disabled = true;
    redetectBtn.disabled = true;
    const canvas = document.getElementById("builder-canvas");
    canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    document.getElementById("builder-warnings-box").innerHTML = '<p class="empty-hint">新樣本已選取，請重新執行自動辨識。</p>';
  };

  findCandidatesBtn.addEventListener("click", async () => {
    if (!builderUploadId || !builderLayout || !builderSelectedBox) {
      alert("請先選取一個既有模位框作為尺寸範本。");
      return;
    }
    findCandidatesBtn.disabled = true;
    findCandidatesBtn.textContent = "⏳ 搜尋中…";
    try {
      const response = await fetch("/api/builder/find_candidates", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upload_id: builderUploadId, cells: builderLayout.cells,
          fiducials: builderLayout.fiducials, ref_cell: builderSelectedBox })
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || "候選框搜尋失敗");
      candidateCells = (data.candidates || []).map(candidate => ({ ...candidate, selected: true }));
      candidateSourceVisibility = { image_search: true, grid_inferred: true };
      renderBuilderCandidateControls(data.counts || {});
      redrawBuilderCanvas();
      if (!candidateCells.length) alert("沒有找到符合條件的候選模位。");
    } catch (error) {
      alert("尋找同尺寸候選框失敗：" + error.message);
    } finally {
      findCandidatesBtn.disabled = false;
      findCandidatesBtn.textContent = "🔎 尋找同尺寸候選框";
    }
  });

  document.getElementById("btn-candidate-image-toggle").addEventListener("click", () => {
    candidateSourceVisibility.image_search = !candidateSourceVisibility.image_search;
    renderBuilderCandidateControls(); redrawBuilderCanvas();
  });
  document.getElementById("btn-candidate-grid-toggle").addEventListener("click", () => {
    candidateSourceVisibility.grid_inferred = !candidateSourceVisibility.grid_inferred;
    renderBuilderCandidateControls(); redrawBuilderCanvas();
  });
  document.getElementById("btn-candidate-cancel").addEventListener("click", clearBuilderCandidates);
  document.getElementById("btn-candidate-accept").addEventListener("click", () => {
    const kept = candidateCells.filter(candidate => candidate.selected);
    if (!kept.length) { clearBuilderCandidates(); return; }
    let nextId = builderLayout.cells.reduce((max, cell) => Math.max(max, Number(cell.id) || 0), -1) + 1;
    kept.forEach(candidate => builderLayout.cells.push({
      id: nextId++, x: candidate.x, y: candidate.y, w: candidate.w, h: candidate.h,
      size_group: nearestBuilderSizeGroup(candidate.w, candidate.h), confidence: candidate.score,
      manually_added: true, candidate_source: candidate.source, candidate_sources: candidate.sources
    }));
    clearBuilderCandidates();
    markBuilderEdited();
    reclusterBuilderSizeGroups();
    scheduleBuilderValidation();
  });

  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      invalidateBuilderSource();
      builderFile = e.dataTransfer.files[0];
      filenameLabel.textContent = builderFile.name;
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files.length > 0) {
      invalidateBuilderSource();
      builderFile = e.target.files[0];
      filenameLabel.textContent = builderFile.name;
    }
  });

  detectBtn.addEventListener("click", async () => {
    detectBtn.disabled = true;
    detectBtn.textContent = "⏳ 自動辨識中 (掃描準星與背景分離)...";

    try {
      let res;
      if (builderFile) {
        res = await fetch("/api/builder/detect", {
          method: "POST",
          body: builderFile
        });
      } else {
        alert("請先上傳一張版型影像。");
        detectBtn.disabled = false;
        detectBtn.textContent = "⚡ 自動辨識準星與模位";
        return;
      }

      const data = await res.json();
      if (!data.success) {
        alert("辨識失敗: " + (data.error || "未知錯誤"));
        return;
      }

      builderLayout = data.layout;
      builderValidationRevision++;
      builderAutoLayout = JSON.parse(JSON.stringify(data.layout));
      builderUploadId = data.upload_id;
      builderWarnings = data.warnings || [];
      builderErrors = [];
      displayBuilderDiagnostics(builderWarnings, builderLayout, builderErrors);

      // Load preview image onto builder canvas
      builderImage.src = `${data.preview_url}${data.preview_url.includes("?") ? "&" : "?"}t=${Date.now()}`;
      builderImage.onload = () => {
        setupBuilderCanvas();
        refreshBuilderSizeOptions();
        scheduleBuilderValidation();
      };

      saveBtn.disabled = false;
      const reviewConfirm = document.getElementById("builder-review-confirm");
      reviewConfirm.disabled = false;
      reviewConfirm.checked = false;
      saveBtn.disabled = true;
      redetectBtn.disabled = false;

    } catch (err) {
      alert("建版辨識錯誤: " + err.message);
    } finally {
      detectBtn.disabled = false;
      detectBtn.textContent = "⚡ 自動辨識準星與模位";
    }
  });

  redetectBtn.addEventListener("click", async () => {
    if (!builderUploadId || !builderLayout) return;
    redetectBtn.disabled = true;
    try {
      const res = await fetch("/api/builder/detect", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upload_id: builderUploadId, fiducials: builderLayout.fiducials })
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.message || data.error || "重新偵測失敗");
      builderLayout = data.layout;
      builderValidationRevision++;
      builderAutoLayout = JSON.parse(JSON.stringify(data.layout));
      builderWarnings = data.warnings || [];
      builderErrors = [];
      displayBuilderDiagnostics(builderWarnings, builderLayout, builderErrors);
      builderImage.onload = () => { setupBuilderCanvas(); refreshBuilderSizeOptions(); scheduleBuilderValidation(); };
      builderImage.src = `${data.preview_url}&t=${Date.now()}`;
      builderSelectedBox = null;
      markBuilderEdited();
    } catch (err) {
      alert("重新偵測失敗: " + err.message);
    } finally {
      redetectBtn.disabled = false;
    }
  });

  saveBtn.addEventListener("click", async () => {
    if (!builderLayout || !document.getElementById("builder-review-confirm").checked) {
      alert("請先確認畫布上的模位框與準星，並勾選人工確認。 ");
      return;
    }
    const layoutId = document.getElementById("b-layout-id").value.trim();
    const name = document.getElementById("b-layout-name").value.trim() || layoutId;

    if (!layoutId) {
      alert("請輸入版型代碼 (Layout ID)！");
      return;
    }

    saveBtn.disabled = true;
    saveBtn.textContent = "⏳ 儲存中...";

    try {
      const payload = {
        upload_id: builderUploadId,
        layout_id: layoutId,
        name: name,
        layout: builderLayout,
        user_confirmed: true,
        confirmed_warnings: true
      };
      let res = await fetch("/api/builder/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      let data = await res.json();
      if (res.status === 409 && data.error === "LAYOUT_EXISTS") {
        if (!window.confirm(`版型代碼 ${layoutId} 已存在。要覆寫原版型嗎？`)) return;
        payload.overwrite = true;
        res = await fetch("/api/builder/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        data = await res.json();
      }
      if (res.status === 422 && data.error === "WARNINGS_REQUIRE_CONFIRMATION") {
        const warningText = (data.warnings || []).map(w => `• ${w.message}`).join("\n");
        if (!window.confirm(`仍有建版警告，確認人工檢查後繼續儲存？\n\n${warningText}`)) return;
        payload.confirmed_warnings = true;
        res = await fetch("/api/builder/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        data = await res.json();
      }
      if (data.success) {
        alert(`版型 [${name}] 儲存成功！專屬四角準星已裁切建檔，現在可在檢測工作台直接使用。`);
        // Refresh layouts and select new one
        await loadLayouts(layoutId);
        // Switch to inspect tab
        document.querySelector('.tab-btn[data-tab="tab-inspect"]').click();
      } else {
        alert("儲存失敗: " + (data.error || "未知錯誤"));
      }
    } catch (err) {
      alert("儲存錯誤: " + err.message);
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "💾 儲存版型與專屬準星範本";
    }
  });

  // Canvas edit tools
  document.getElementById("btn-canvas-del-box").addEventListener("click", () => {
    if (builderSelectedBox && builderLayout) {
      builderLayout.cells = builderLayout.cells.filter(c => c.id !== builderSelectedBox.id);
      builderSelectedBox = null;
      markBuilderEdited();
      redrawBuilderCanvas();
      reclusterBuilderSizeGroups();
    }
  });

  document.getElementById("btn-canvas-add-box").addEventListener("click", (e) => {
    builderAddMode = !builderAddMode;
    e.currentTarget.classList.toggle("active", builderAddMode);
    e.currentTarget.textContent = builderAddMode ? "✚ 在畫布拖曳新增（取消）" : "➕ 手動拉框";
  });

  document.getElementById("btn-canvas-pan").addEventListener("click", (e) => {
    builderPanMode = !builderPanMode;
    e.currentTarget.classList.toggle("active", builderPanMode);
    e.currentTarget.textContent = builderPanMode ? "✋ 移動＋滾輪縮放中" : "✋ 移動／滾輪縮放";
    e.currentTarget.setAttribute("aria-pressed", String(builderPanMode));
    document.getElementById("builder-canvas").classList.toggle("pan-mode", builderPanMode);
  });

  document.getElementById("builder-canvas-wrap").addEventListener("wheel", (e) => {
    if (!builderPanMode || !builderImage.width) return;
    e.preventDefault();
    const zoomSelect = document.getElementById("builder-zoom-select");
    const levels = Array.from(zoomSelect.options).map(option => Number(option.value)).filter(Number.isFinite);
    const current = Number(zoomSelect.value) || 1;
    const currentIndex = levels.reduce((best, value, index) =>
      Math.abs(value - current) < Math.abs(levels[best] - current) ? index : best, 0);
    const nextIndex = Math.max(0, Math.min(levels.length - 1, currentIndex + (e.deltaY < 0 ? 1 : -1)));
    if (nextIndex === currentIndex) return;
    zoomSelect.value = String(levels[nextIndex]);
    applyBuilderCanvasZoom(e.clientX, e.clientY);
  }, { passive: false });

  document.getElementById("builder-zoom-select").addEventListener("change", (e) => {
    applyBuilderCanvasZoom();
  });

  document.getElementById("btn-canvas-reset").addEventListener("click", () => {
    if (!builderAutoLayout) return;
    builderLayout = JSON.parse(JSON.stringify(builderAutoLayout));
    builderSelectedBox = null;
    builderDrag = null;
    builderAddMode = false;
    markBuilderEdited();
    displayBuilderDiagnostics(builderWarnings, builderLayout);
    setupBuilderCanvas();
  });

  document.getElementById("builder-review-confirm").addEventListener("change", (e) => {
    if (builderLayout) builderLayout.review_status = e.target.checked ? "confirmed" : "pending";
    refreshBuilderSaveState();
  });
}

function markBuilderEdited() {
  const confirm = document.getElementById("builder-review-confirm");
  if (confirm) {
    confirm.checked = false;
    document.getElementById("btn-builder-save").disabled = true;
  }
  if (builderLayout) builderLayout.review_status = "pending";
  scheduleBuilderValidation();
}

function refreshBuilderSaveState() {
  const confirm = document.getElementById("builder-review-confirm");
  const save = document.getElementById("btn-builder-save");
  if (save) save.disabled = !confirm?.checked || builderErrors.length > 0 || !builderUploadId;
}

function displayBuilderDiagnostics(warnings, layout, errors = []) {
  const box = document.getElementById("builder-warnings-box");
  box.innerHTML = "";

  errors.forEach(error => {
    const div = document.createElement("div");
    div.className = "warn-card ERROR";
    div.textContent = `⛔ ${error.message || error.code}`;
    box.appendChild(div);
  });
  if ((!warnings || warnings.length === 0) && errors.length === 0) {
    box.innerHTML = '<div class="warn-card INFO">✓ 防呆檢查通過：無異常漏框、無重疊框、準星信心度充足。</div>';
  } else {
    (warnings || []).forEach(w => {
      const div = document.createElement("div");
      div.className = `warn-card ${w.severity || "WARNING"}`;
      div.textContent = `${w.severity === "WARNING" ? "⚠" : "ℹ"} ${w.message}`;
      box.appendChild(div);
    });
  }

  updateBuilderStats();
  refreshBuilderSaveState();
}

function scheduleBuilderValidation() {
  if (!builderLayout || !builderUploadId) return;
  clearTimeout(builderValidationTimer);
  builderErrors = [{ code: "VALIDATION_PENDING", message: "正在重新檢查建版資料…" }];
  refreshBuilderSaveState();
  builderValidationTimer = setTimeout(async () => {
    const revision = ++builderValidationRevision;
    try {
      const response = await fetch("/api/builder/validate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ upload_id: builderUploadId, layout: builderLayout })
      });
      const report = await response.json();
      if (revision !== builderValidationRevision) return;
      if (!response.ok) throw new Error(report.error || "驗證失敗");
      builderErrors = report.errors || [];
      builderWarnings = report.warnings || [];
      if (report.layout?.fiducials) builderLayout.fiducials = report.layout.fiducials;
      displayBuilderDiagnostics(builderWarnings, builderLayout, builderErrors);
      redrawBuilderCanvas();
    } catch (err) {
      if (revision !== builderValidationRevision) return;
      builderErrors = [{ code: "VALIDATION_FAILED", message: `驗證服務失敗：${err.message}` }];
      displayBuilderDiagnostics(builderWarnings, builderLayout, builderErrors);
    }
  }, 220);
}

function refreshBuilderSizeOptions() {
  const select = document.getElementById("builder-size-select");
  if (!select || !builderLayout) return;
  const previous = select.value;
  const groups = new Map();
  builderLayout.cells.forEach(cell => {
    const key = `${cell.w}x${cell.h}`;
    if (!groups.has(key)) groups.set(key, { w: cell.w, h: cell.h, count: 0, group: cell.size_group || "default" });
    groups.get(key).count++;
  });
  select.innerHTML = '<option value="free">自由拖曳</option>';
  [...groups.entries()].sort((a, b) => b[1].count - a[1].count).forEach(([key, item]) => {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = `${item.group}: ${item.w}×${item.h} (${item.count})`;
    option.dataset.group = item.group;
    option.dataset.width = item.w;
    option.dataset.height = item.h;
    select.appendChild(option);
  });
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

function nearestBuilderSizeGroup(w, h) {
  if (!builderLayout?.cells?.length) return "default";
  const groups = new Map();
  builderLayout.cells.forEach(cell => {
    const key = cell.size_group || "default";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(cell);
  });
  let best = "default", bestScore = Infinity;
  groups.forEach((cells, group) => {
    const rw = cells.reduce((sum, cell) => sum + cell.w, 0) / cells.length;
    const rh = cells.reduce((sum, cell) => sum + cell.h, 0) / cells.length;
    const score = Math.abs(w - rw) / Math.max(1, rw) + Math.abs(h - rh) / Math.max(1, rh);
    if (score < bestScore) { best = group; bestScore = score; }
  });
  return best;
}

function reclusterBuilderSizeGroups() {
  if (!builderLayout?.cells?.length) return;
  const cells = builderLayout.cells;
  const medianSide = [...cells.map(c => Math.min(c.w, c.h))].sort((a, b) => a - b)[Math.floor(cells.length / 2)];
  const tol = Math.max(3, Math.round(medianSide * 0.12));
  const keyOf = c => `${Math.round(c.w / tol)},${Math.round(c.h / tol)}`;
  const counts = new Map();
  cells.forEach(c => counts.set(keyOf(c), (counts.get(keyOf(c)) || 0) + 1));
  const ordered = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b));
  const labels = new Map(ordered.map((key, index) => [key, index < 26 ? `size_${String.fromCharCode(65 + index)}` : `size_${index}`]));
  cells.forEach(c => { c.size_group = labels.get(keyOf(c)); });
  updateBuilderStats();
  refreshBuilderSizeOptions();
}

function updateBuilderStats() {
  if (!builderLayout) return;
  document.getElementById("b-stat-count").textContent = builderLayout.cells.length;
  const groups = Array.from(new Set(builderLayout.cells.map(c => c.size_group || "default")));
  document.getElementById("b-stat-groups").textContent = groups.join(", ");
}

function setupBuilderCanvas() {
  const canvas = document.getElementById("builder-canvas");
  const ctx = canvas.getContext("2d");

  canvas.width = builderImage.width;
  canvas.height = builderImage.height;

  // Calculate scale relative to full golden resolution
  const fullW = builderLayout.image_spec.width;
  builderScale = canvas.width / float(fullW);
  if (!builderCanvasResizeObserver && window.ResizeObserver) {
    builderCanvasResizeObserver = new ResizeObserver(() => applyBuilderCanvasZoom());
    builderCanvasResizeObserver.observe(document.getElementById("builder-canvas-wrap"));
  }
  requestAnimationFrame(applyBuilderCanvasZoom);

  redrawBuilderCanvas();

  const toImagePoint = (e) => {
    const rect = canvas.getBoundingClientRect();
    const fullH = builderLayout.image_spec.height;
    return {
      x: Math.max(0, Math.min(fullW, (e.clientX - rect.left) * fullW / rect.width)),
      y: Math.max(0, Math.min(fullH, (e.clientY - rect.top) * fullH / rect.height))
    };
  };
  canvas.onpointerdown = (e) => {
    if (!builderLayout) return;
    if (builderPanMode) {
      canvas.setPointerCapture(e.pointerId);
      builderDrag = { type: "pan", startX: e.clientX, startY: e.clientY,
        scrollLeft: document.getElementById("builder-canvas-wrap").scrollLeft,
        scrollTop: document.getElementById("builder-canvas-wrap").scrollTop };
      return;
    }
    const p = toImagePoint(e);
    canvas.setPointerCapture(e.pointerId);
    const hitCandidate = candidateCells.find(candidate => {
      const visible = candidate.sources.some(source => candidateSourceVisibility[source]);
      return visible && p.x >= candidate.x && p.x <= candidate.x + candidate.w &&
        p.y >= candidate.y && p.y <= candidate.y + candidate.h;
    });
    if (hitCandidate) {
      hitCandidate.selected = !hitCandidate.selected;
      renderBuilderCandidateControls();
      redrawBuilderCanvas();
      return;
    }
    if (builderAddMode) {
      drawStartPos = p;
      isDrawingBox = true;
      const sizeOption = document.getElementById("builder-size-select").selectedOptions[0];
      const preset = sizeOption?.value !== "free" ? {
        w: Number(sizeOption.dataset.width), h: Number(sizeOption.dataset.height), group: sizeOption.dataset.group
      } : null;
      builderDrag = { type: preset ? "place" : "draw", start: p, end: p, preset };
      redrawBuilderCanvas();
      return;
    }

    const fids = builderLayout.fiducials?.points || {};
    const hitRadius = Math.min(builderLayout.image_spec.width, builderLayout.image_spec.height) * 0.012;
    const fidHit = Object.entries(fids).find(([, pt]) => Math.hypot(p.x - pt.x, p.y - pt.y) <= hitRadius);
    if (fidHit) {
      builderDrag = { type: "fiducial", name: fidHit[0] };
      return;
    }

    // Selected-box corner handles take priority over moving the whole box.
    if (builderSelectedBox) {
      const c = builderSelectedBox;
      const corners = {
        nw: [c.x, c.y], n: [c.x + c.w / 2, c.y], ne: [c.x + c.w, c.y],
        w: [c.x, c.y + c.h / 2], e: [c.x + c.w, c.y + c.h / 2],
        sw: [c.x, c.y + c.h], s: [c.x + c.w / 2, c.y + c.h], se: [c.x + c.w, c.y + c.h]
      };
      const rect = canvas.getBoundingClientRect();
      const hit = 12 * fullW / rect.width;
      const handle = Object.entries(corners).find(([, xy]) => Math.hypot(p.x - xy[0], p.y - xy[1]) <= hit);
      if (handle) {
        builderDrag = { type: "resize", corner: handle[0], start: p,
          original: { x: c.x, y: c.y, w: c.w, h: c.h } };
        return;
      }
    }

    builderSelectedBox = null;
    for (let i = builderLayout.cells.length - 1; i >= 0; i--) {
      const c = builderLayout.cells[i];
      if (p.x >= c.x && p.x <= c.x + c.w && p.y >= c.y && p.y <= c.y + c.h) {
        builderSelectedBox = c;
        builderDrag = { type: "cell", last: p };
        break;
      }
    }
    redrawBuilderCanvas();
  };
  canvas.onpointermove = (e) => {
    if (!builderDrag) return;
    if (builderDrag.type === "pan") {
      const wrap = document.getElementById("builder-canvas-wrap");
      wrap.scrollLeft = builderDrag.scrollLeft - (e.clientX - builderDrag.startX);
      wrap.scrollTop = builderDrag.scrollTop - (e.clientY - builderDrag.startY);
      return;
    }
    const p = toImagePoint(e);
    if (builderDrag.type === "draw") {
      builderDrag.end = p;
    } else if (builderDrag.type === "place") {
      builderDrag.end = p;
    } else if (builderDrag.type === "fiducial") {
      const pt = builderLayout.fiducials.points[builderDrag.name];
      pt.x = Math.round(p.x);
      pt.y = Math.round(p.y);
      pt.manual_override = true;
      markBuilderEdited();
    } else if (builderDrag.type === "cell" && builderSelectedBox) {
      const dx = p.x - builderDrag.last.x, dy = p.y - builderDrag.last.y;
      const c = builderSelectedBox;
      c.x = Math.round(Math.max(0, Math.min(builderLayout.image_spec.width - c.w, c.x + dx)));
      c.y = Math.round(Math.max(0, Math.min(builderLayout.image_spec.height - c.h, c.y + dy)));
      builderDrag.last = p;
      markBuilderEdited();
    } else if (builderDrag.type === "resize" && builderSelectedBox) {
      const c = builderSelectedBox, o = builderDrag.original;
      const dx = p.x - builderDrag.start.x, dy = p.y - builderDrag.start.y;
      const maxX = builderLayout.image_spec.width, maxY = builderLayout.image_spec.height;
      if (builderDrag.corner.includes("w")) {
        c.x = Math.round(Math.max(0, Math.min(o.x + o.w - 10, o.x + dx)));
        c.w = o.x + o.w - c.x;
      } else {
        c.x = o.x;
        c.w = Math.round(Math.max(10, Math.min(maxX - o.x, o.w + dx)));
      }
      if (builderDrag.corner.includes("n")) {
        c.y = Math.round(Math.max(0, Math.min(o.y + o.h - 10, o.y + dy)));
        c.h = o.y + o.h - c.y;
      } else {
        c.y = o.y;
        c.h = Math.round(Math.max(10, Math.min(maxY - o.y, o.h + dy)));
      }
      markBuilderEdited();
    }
    redrawBuilderCanvas();
  };
  canvas.onpointerup = (e) => {
    if (!builderDrag) return;
    if (builderDrag.type === "draw") {
      const end = toImagePoint(e);
      const x = Math.round(Math.min(drawStartPos.x, end.x));
      const y = Math.round(Math.min(drawStartPos.y, end.y));
      const w = Math.round(Math.abs(end.x - drawStartPos.x));
      const h = Math.round(Math.abs(end.y - drawStartPos.y));
      if (w >= 8 && h >= 8) {
        const id = builderLayout.cells.reduce((m, c) => Math.max(m, Number(c.id) || 0), -1) + 1;
        const group = nearestBuilderSizeGroup(w, h);
        builderLayout.cells.push({ id, x, y, w, h, size_group: group, confidence: 1.0, manually_added: true });
        builderSelectedBox = builderLayout.cells[builderLayout.cells.length - 1];
        markBuilderEdited();
        reclusterBuilderSizeGroups();
      }
      isDrawingBox = false;
    } else if (builderDrag.type === "place") {
      const { w, h, group } = builderDrag.preset;
      if (w >= 10 && h >= 10) {
        const x = Math.round(Math.max(0, Math.min(builderLayout.image_spec.width - w, drawStartPos.x)));
        const y = Math.round(Math.max(0, Math.min(builderLayout.image_spec.height - h, drawStartPos.y)));
        const id = builderLayout.cells.reduce((m, c) => Math.max(m, Number(c.id) || 0), -1) + 1;
        builderLayout.cells.push({ id, x, y, w, h, size_group: group || nearestBuilderSizeGroup(w, h), confidence: 1.0, manually_added: true });
        builderSelectedBox = builderLayout.cells[builderLayout.cells.length - 1];
        markBuilderEdited();
        reclusterBuilderSizeGroups();
      }
      isDrawingBox = false;
    } else if (builderDrag.type === "fiducial" || builderDrag.type === "cell" || builderDrag.type === "resize") {
      markBuilderEdited();
      if (builderDrag.type === "resize") reclusterBuilderSizeGroups();
    }
    builderDrag = null;
    canvas.releasePointerCapture(e.pointerId);
    redrawBuilderCanvas();
  };
  canvas.onpointercancel = () => {
    builderDrag = null;
    isDrawingBox = false;
    scheduleBuilderValidation();
    redrawBuilderCanvas();
  };
}

function applyBuilderCanvasZoom(anchorClientX = null, anchorClientY = null) {
  const canvas = document.getElementById("builder-canvas");
  const wrap = document.getElementById("builder-canvas-wrap");
  if (!canvas || !wrap || !canvas.width || !canvas.height) return;

  const availableWidth = wrap.clientWidth - 32;
  if (availableWidth <= 0) return;

  const oldRect = canvas.getBoundingClientRect();
  const wrapRect = wrap.getBoundingClientRect();
  const anchorX = anchorClientX ?? (wrapRect.left + wrap.clientWidth / 2);
  const anchorY = anchorClientY ?? (wrapRect.top + wrap.clientHeight / 2);
  const contentX = anchorX - wrapRect.left;
  const contentY = anchorY - wrapRect.top;
  const imageX = oldRect.width ? (wrap.scrollLeft + contentX - (oldRect.left - wrapRect.left)) / oldRect.width : 0.5;
  const imageY = oldRect.height ? (wrap.scrollTop + contentY - (oldRect.top - wrapRect.top)) / oldRect.height : 0.5;

  // Fit the canvas to the available width; taller sheets remain vertically scrollable.
  const fitScale = Math.min(availableWidth / canvas.width, 1);
  const zoomSelect = document.getElementById("builder-zoom-select");
  const zoom = Number(zoomSelect?.value) || 1;
  const displayScale = fitScale * zoom;
  canvas.style.width = `${Math.max(1, canvas.width * displayScale)}px`;
  canvas.style.height = `${Math.max(1, canvas.height * displayScale)}px`;
  wrap.scrollLeft = Math.max(0, imageX * canvas.offsetWidth - contentX);
  wrap.scrollTop = Math.max(0, imageY * canvas.offsetHeight - contentY);
}

function clearBuilderCandidates() {
  candidateCells = [];
  const toolbar = document.getElementById("builder-candidate-toolbar");
  if (toolbar) toolbar.classList.add("hidden");
  const list = document.getElementById("builder-candidate-list");
  if (list) list.replaceChildren();
  if (typeof redrawBuilderCanvas === "function") redrawBuilderCanvas();
}

function renderBuilderCandidateControls(counts = null) {
  const toolbar = document.getElementById("builder-candidate-toolbar");
  if (!toolbar) return;
  toolbar.classList.toggle("hidden", candidateCells.length === 0);
  const sourceCounts = counts || {
    image_search: candidateCells.filter(c => c.sources.includes("image_search")).length,
    grid_inferred: candidateCells.filter(c => c.sources.includes("grid_inferred")).length
  };
  const selectedCount = candidateCells.filter(c => c.selected).length;
  document.getElementById("builder-candidate-count").textContent =
    `找到 ${candidateCells.length} 個候選框；已選 ${selectedCount} 個（影像 ${sourceCounts.image_search || 0}、網格 ${sourceCounts.grid_inferred || 0}）`;
  document.getElementById("btn-candidate-image-toggle").classList.toggle("active", candidateSourceVisibility.image_search);
  document.getElementById("btn-candidate-grid-toggle").classList.toggle("active", candidateSourceVisibility.grid_inferred);
  const list = document.getElementById("builder-candidate-list");
  list.replaceChildren();
  candidateCells.forEach((candidate, index) => {
    const button = document.createElement("button");
    const sourceNames = candidate.sources.map(source => source === "image_search" ? "影像" : "網格").join("+");
    const breakdown = candidate.score_breakdown || {};
    button.className = `builder-candidate-item${candidate.selected ? "" : " excluded"}`;
    button.textContent = `#${index + 1} ${sourceNames} ${(candidate.score * 100).toFixed(0)}% (尺${(breakdown.size_sim || 0).toFixed(2)} 框${(breakdown.border_edge || 0).toFixed(2)} 填${(breakdown.fill_contrast || 0).toFixed(2)})`;
    button.title = `x=${candidate.x}, y=${candidate.y}, ${candidate.w}×${candidate.h}；點擊切換納入`;
    button.addEventListener("click", () => {
      candidate.selected = !candidate.selected;
      renderBuilderCandidateControls();
      redrawBuilderCanvas();
    });
    list.appendChild(button);
  });
}

function float(val) {
  return parseFloat(val) || 1.0;
}

function redrawBuilderCanvas() {
  const canvas = document.getElementById("builder-canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx || !builderImage.width) return;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(builderImage, 0, 0);

  if (!builderLayout) return;

  const colorPalette = {
    "size_A": "#38bdf8",
    "size_B": "#a855f7",
    "size_C": "#34d399",
    "size_D": "#fbbf24",
    "default": "#60a5fa"
  };

  // Draw cells
  builderLayout.cells.forEach(c => {
    const sx = c.x * builderScale;
    const sy = c.y * builderScale;
    const sw = c.w * builderScale;
    const sh = c.h * builderScale;

    const isSel = builderSelectedBox && builderSelectedBox.id === c.id;
    const color = isSel ? "#ef4444" : (colorPalette[c.size_group] || "#60a5fa");

    ctx.strokeStyle = color;
    ctx.lineWidth = isSel ? 3 : 1.5;
    ctx.strokeRect(sx, sy, sw, sh);

    if (isSel) {
      ctx.fillStyle = "rgba(239, 68, 68, 0.2)";
      ctx.fillRect(sx, sy, sw, sh);
      ctx.fillStyle = "#fff";
      [[sx, sy], [sx + sw / 2, sy], [sx + sw, sy], [sx, sy + sh / 2],
        [sx + sw, sy + sh / 2], [sx, sy + sh], [sx + sw / 2, sy + sh], [sx + sw, sy + sh]].forEach(([hx, hy]) => {
        ctx.fillRect(hx - 4, hy - 4, 8, 8);
        ctx.strokeStyle = "#ef4444";
        ctx.lineWidth = 1;
        ctx.strokeRect(hx - 4, hy - 4, 8, 8);
      });
    }

    // Label
    ctx.fillStyle = color;
    ctx.font = "10px sans-serif";
    ctx.fillText(`#${c.id}`, sx + 3, sy + 12);
  });

  candidateCells.forEach(candidate => {
    if (!candidate.sources.some(source => candidateSourceVisibility[source])) return;
    ctx.save();
    ctx.strokeStyle = candidate.selected
      ? (candidate.sources.includes("grid_inferred") ? "#f59e0b" : "#22d3ee") : "#64748b";
    ctx.lineWidth = 2;
    ctx.setLineDash([9, 6]);
    ctx.strokeRect(candidate.x * builderScale, candidate.y * builderScale,
      candidate.w * builderScale, candidate.h * builderScale);
    ctx.setLineDash([]);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.font = "bold 12px sans-serif";
    ctx.fillText(`${Math.round(candidate.score * 100)}%`, candidate.x * builderScale + 3,
      candidate.y * builderScale + 14);
    ctx.restore();
  });

  if (builderDrag?.type === "draw" && drawStartPos && builderDrag.end) {
    const x = Math.min(drawStartPos.x, builderDrag.end.x) * builderScale;
    const y = Math.min(drawStartPos.y, builderDrag.end.y) * builderScale;
    const w = Math.abs(builderDrag.end.x - drawStartPos.x) * builderScale;
    const h = Math.abs(builderDrag.end.y - drawStartPos.y) * builderScale;
    ctx.strokeStyle = "#facc15";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);
  }

  // Draw fiducials
  const fids = builderLayout.fiducials?.points || {};
  Object.entries(fids).forEach(([name, pt]) => {
    const fx = pt.x * builderScale;
    const fy = pt.y * builderScale;

    ctx.strokeStyle = "#e11d48";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(fx, fy, 10, 0, 2 * Math.PI);
    ctx.moveTo(fx - 14, fy);
    ctx.lineTo(fx + 14, fy);
    ctx.moveTo(fx, fy - 14);
    ctx.lineTo(fx, fy + 14);
    ctx.stroke();

    ctx.fillStyle = "#e11d48";
    ctx.font = "bold 10px sans-serif";
    const label = pt.manual_override ? "人工指定" : `${Math.round((pt.local_quality ?? pt.confidence ?? 0) * 100)}%`;
    ctx.fillText(`${name} (${label})`, fx + 14, fy - 4);
  });
}
