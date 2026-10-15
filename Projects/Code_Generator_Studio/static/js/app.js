// Ultron Code Generation Studio - Client Engine

let currentTemplates = [];
let activeTemplateId = "lru_cache"; // Default starting template
let currentLanguage = "python";

document.addEventListener("DOMContentLoaded", () => {
  initTemplates();
  initEditorKeybindings();
});

// Switch Sidebar Tabs
function switchSidebarTab(tabName) {
  const tabTemplatesBtn = document.getElementById("tabTemplatesBtn");
  const tabCustomBtn = document.getElementById("tabCustomBtn");
  const templatesPane = document.getElementById("templatesTabContent");
  const customPane = document.getElementById("customTabContent");

  if (tabName === "templates") {
    tabTemplatesBtn.classList.add("active");
    tabCustomBtn.classList.remove("active");
    templatesPane.classList.add("active");
    customPane.classList.remove("active");
  } else {
    tabCustomBtn.classList.add("active");
    tabTemplatesBtn.classList.remove("active");
    customPane.classList.add("active");
    templatesPane.classList.remove("active");
  }
}

// Fetch Templates from Server
async function initTemplates() {
  const container = document.getElementById("templatesList");
  try {
    const res = await fetch("/api/templates");
    const data = await res.json();
    currentTemplates = data.templates || [];

    if (currentTemplates.length === 0) {
      container.innerHTML = `<div class="term-greeting">No templates found.</div>`;
      return;
    }

    container.innerHTML = "";
    currentTemplates.forEach((tpl) => {
      const card = document.createElement("div");
      card.className = `template-card ${tpl.id === activeTemplateId ? "active" : ""}`;
      card.id = `card-${tpl.id}`;
      card.onclick = () => loadTemplate(tpl.id);

      card.innerHTML = `
        <div class="card-top">
          <span class="tpl-category">${escapeHtml(tpl.category)}</span>
          <span class="badge lang-badge">${escapeHtml(tpl.language.toUpperCase())}</span>
        </div>
        <div class="card-title">${escapeHtml(tpl.title)}</div>
        <div class="card-desc">${escapeHtml(tpl.description)}</div>
      `;
      container.appendChild(card);
    });

    // Load initial default template
    loadTemplate(activeTemplateId);

  } catch (err) {
    console.error("Error loading templates:", err);
    container.innerHTML = `<div class="term-stderr">Failed to load templates: ${err.message}</div>`;
  }
}

// Load Selected Template
function loadTemplate(tplId) {
  const tpl = currentTemplates.find((t) => t.id === tplId);
  if (!tpl) return;

  activeTemplateId = tplId;
  currentLanguage = tpl.language || "python";

  // Update Active Class
  document.querySelectorAll(".template-card").forEach((c) => c.classList.remove("active"));
  const activeCard = document.getElementById(`card-${tplId}`);
  if (activeCard) activeCard.classList.add("active");

  // Update Toolbar
  const fileExt = currentLanguage === "python" ? "py" : (currentLanguage === "javascript" ? "js" : "sh");
  document.getElementById("currentFileName").textContent = `${tpl.id}.${fileExt}`;
  document.getElementById("activeLanguageBadge").textContent = currentLanguage.toUpperCase();

  // Populate Editor
  document.getElementById("codeEditor").value = tpl.code;

  // Run initial static analysis
  if (currentLanguage === "python") {
    analyzeCurrentCode();
  }
}

// Custom Code Generation
async function handleCustomGenerate(event) {
  event.preventDefault();

  const taskType = document.getElementById("taskType").value.trim();
  const language = document.getElementById("codeLang").value;
  const architecture = document.getElementById("architecture").value;
  const userPrompt = document.getElementById("userPrompt").value.trim();

  const featCheckboxes = document.querySelectorAll('input[name="feat"]:checked');
  const features = Array.from(featCheckboxes).map((cb) => cb.value);

  const generateBtn = document.getElementById("generateBtn");
  const originalHtml = generateBtn.innerHTML;
  generateBtn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Synthesizing...`;
  generateBtn.disabled = true;

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task_type: taskType,
        language: language,
        architecture: architecture,
        features: features,
        user_prompt: userPrompt
      })
    });

    const data = await res.json();
    if (data.success && data.result) {
      currentLanguage = language;
      const fileExt = language === "python" ? "py" : (language === "javascript" ? "js" : "sh");
      const safeName = taskType.toLowerCase().replace(/[^a-z0-9]/g, "_");

      document.getElementById("currentFileName").textContent = `${safeName}.${fileExt}`;
      document.getElementById("activeLanguageBadge").textContent = language.toUpperCase();
      document.getElementById("codeEditor").value = data.result.code;

      // Update terminal with notice
      const terminal = document.getElementById("terminalOutput");
      terminal.innerHTML = `<span class="term-stdout">[Ultron Synthesizer] Code generated successfully for '${taskType}'. Ready to execute.</span>`;
      setExecStatus("ready", "0 ms");

      // Auto-analyze if python
      if (language === "python") {
        analyzeCurrentCode();
      }
    }
  } catch (err) {
    alert("Generation failed: " + err.message);
  } finally {
    generateBtn.innerHTML = originalHtml;
    generateBtn.disabled = false;
  }
}

// Run Code in Sandbox Subprocess
async function runCurrentCode() {
  const code = document.getElementById("codeEditor").value;
  const terminal = document.getElementById("terminalOutput");
  const runBtn = document.getElementById("runCodeBtn");

  if (!code.trim()) {
    terminal.innerHTML = `<span class="term-stderr">[Error] Editor is empty. Nothing to execute.</span>`;
    return;
  }

  setExecStatus("running", "--");
  terminal.innerHTML = `<span class="term-greeting"><i class="fas fa-spinner fa-spin"></i> Executing code in Python sandbox...</span>`;
  runBtn.disabled = true;

  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code })
    });

    const data = await res.json();
    runBtn.disabled = false;

    let outputHtml = "";
    if (data.stdout) {
      outputHtml += `<span class="term-stdout">${escapeHtml(data.stdout)}</span>`;
    }
    if (data.stderr) {
      outputHtml += `<span class="term-stderr">${escapeHtml(data.stderr)}</span>`;
    }
    if (!data.stdout && !data.stderr) {
      outputHtml = `<span class="term-greeting">Process completed with exit code ${data.exit_code} (no output).</span>`;
    }

    terminal.innerHTML = outputHtml;
    terminal.scrollTop = terminal.scrollHeight;

    if (data.success) {
      setExecStatus("success", `${data.duration_ms} ms`);
    } else {
      setExecStatus("failed", `${data.duration_ms} ms`);
    }

  } catch (err) {
    runBtn.disabled = false;
    setExecStatus("failed", "0 ms");
    terminal.innerHTML = `<span class="term-stderr">Subprocess Communication Error: ${escapeHtml(err.message)}</span>`;
  }
}

// Analyze Code (AST Metrics)
async function analyzeCurrentCode() {
  const code = document.getElementById("codeEditor").value;
  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code })
    });

    const data = await res.json();
    const metrics = data.metrics || {};

    if (!metrics.valid_syntax) {
      document.getElementById("metricLines").textContent = `${metrics.total_lines || 0} / --`;
      document.getElementById("metricFuncs").textContent = "--";
      document.getElementById("metricClasses").textContent = "--";
      document.getElementById("metricImports").textContent = "--";
      document.getElementById("metricCyclomatic").textContent = "--";
      document.getElementById("metricDocstring").textContent = "--";

      const banner = document.getElementById("metricComplexityBanner");
      banner.className = "metric-banner banner-danger";
      banner.textContent = metrics.error || "Syntax Error detected in code.";
      return;
    }

    document.getElementById("metricLines").textContent = `${metrics.total_lines} / ${metrics.code_lines}`;
    document.getElementById("metricFuncs").textContent = `${metrics.functions_count}`;
    document.getElementById("metricClasses").textContent = `${metrics.classes_count}`;
    document.getElementById("metricImports").textContent = `${metrics.imports_count}`;
    document.getElementById("metricCyclomatic").textContent = `${metrics.cyclomatic_score}`;
    document.getElementById("metricDocstring").textContent = `${metrics.docstring_coverage_pct}%`;

    const banner = document.getElementById("metricComplexityBanner");
    banner.className = `metric-banner banner-${metrics.complexity_color || "success"}`;
    banner.textContent = `Complexity: ${metrics.complexity_level}`;

  } catch (err) {
    console.error("Analysis failed:", err);
  }
}

// Copy to Clipboard
function copyCode() {
  const code = document.getElementById("codeEditor").value;
  navigator.clipboard.writeText(code).then(() => {
    const origText = document.getElementById("currentFileName").textContent;
    document.getElementById("currentFileName").textContent = "Copied to Clipboard!";
    setTimeout(() => {
      document.getElementById("currentFileName").textContent = origText;
    }, 1500);
  });
}

// Download Code File
function downloadCode() {
  const code = document.getElementById("codeEditor").value;
  const fileName = document.getElementById("currentFileName").textContent || "script.py";
  const blob = new Blob([code], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// Clear Terminal
function clearTerminal() {
  const terminal = document.getElementById("terminalOutput");
  terminal.innerHTML = `<span class="term-greeting">Console output cleared. Ready for execution.</span>`;
  setExecStatus("ready", "0 ms");
}

// Helper: Execution Status Pill
function setExecStatus(state, duration) {
  const pill = document.getElementById("execStatusPill");
  const timeEl = document.getElementById("execDuration");

  timeEl.textContent = duration;
  pill.className = `status-pill status-${state}`;

  if (state === "ready") pill.textContent = "IDLE";
  else if (state === "running") pill.textContent = "RUNNING";
  else if (state === "success") pill.textContent = "SUCCESS (0)";
  else if (state === "failed") pill.textContent = "FAILED";
}

// Setup Tab key indent & Ctrl+Enter shortcut in textarea
function initEditorKeybindings() {
  const editor = document.getElementById("codeEditor");

  editor.addEventListener("keydown", (e) => {
    // Ctrl + Enter to Run
    if (e.ctrlKey && e.key === "Enter") {
      e.preventDefault();
      runCurrentCode();
      return;
    }

    // Tab key indentation (4 spaces)
    if (e.key === "Tab") {
      e.preventDefault();
      const start = editor.selectionStart;
      const end = editor.selectionEnd;
      editor.value = editor.value.substring(0, start) + "    " + editor.value.substring(end);
      editor.selectionStart = editor.selectionEnd = start + 4;
    }
  });
}

// Helper: Escape HTML
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
