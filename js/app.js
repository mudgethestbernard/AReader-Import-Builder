/* AReader Import Builder - web front end.
 *
 * The conversion is the same Python the desktop version runs: the .py files
 * next to this page are loaded into Pyodide and called there, so the browser
 * and the window cannot drift apart. Nothing leaves the page.
 *
 * Unlike the command line this runs in two halves - `api.analyze` reads the
 * books and reports what it found, the reader corrects it, and `api.build`
 * writes the archive from those corrections.
 */

const PYODIDE_INDEX = "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";
const MODULES = ["metadata", "blocks", "chapters", "archive", "txt_source",
                 "epub_source", "zip_source", "sources", "cli", "api"];
const PY_DIR = "/home/pyodide";
const WORK_DIR = "/work";
const OUT_DIR = "/out";
const ACCEPTED = /\.(txt|epub|zip)$/i;
const LONG_CHAPTER = 120000;

const el = (id) => document.getElementById(id);
const ui = {
  boot: el("boot"), bootText: el("bootText"),
  dropZone: el("dropZone"), fileInput: el("fileInput"),
  fileList: el("fileList"), fileLabel: el("fileLabel"),
  loadBtn: el("loadBtn"), status: el("status"),
  reviewStep: el("reviewStep"), review: el("review"),
  saveStep: el("saveStep"), saveBtn: el("saveBtn"), saveStatus: el("saveStatus"),
  downloads: el("downloads"), results: el("results"),
  separate: el("optSeparate"), separateOpt: el("separateOpt"),
};

let pyodide = null;
let files = [];
let works = [];          // analyze() result, edited in place by the review UI
let busy = false;
let objectUrls = [];

/* ------------------------------------------------------------------- boot */

async function boot() {
  try {
    ui.bootText.textContent = "Loading the converter… (downloaded once)";
    pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });

    const sources = await Promise.all(MODULES.map(async (name) => {
      const response = await fetch(`${name}.py`, { cache: "no-cache" });
      if (!response.ok) throw new Error(`${name}.py (${response.status})`);
      return [name, await response.text()];
    }));
    for (const [name, source] of sources) {
      pyodide.FS.writeFile(`${PY_DIR}/${name}.py`, source);
    }
    pyodide.runPython(
      `import sys\nif ${JSON.stringify(PY_DIR)} not in sys.path: sys.path.insert(0, ${JSON.stringify(PY_DIR)})`);

    ui.boot.classList.add("boot--done");
    ui.bootText.textContent = "Ready. Your files never leave this browser.";
    setTimeout(() => { ui.boot.hidden = true; }, 2500);
    refresh();
  } catch (error) {
    ui.boot.classList.add("boot--error");
    ui.bootText.textContent =
      `Could not load the converter: ${error.message}. Check your connection and reload.`;
  }
}

/* ------------------------------------------------------------------ files */

function addFiles(incoming) {
  const rejected = [];
  for (const file of incoming) {
    if (!ACCEPTED.test(file.name)) { rejected.push(file.name); continue; }
    if (!files.some((f) => f.name === file.name && f.size === file.size)) files.push(file);
  }
  resetReview();
  if (rejected.length) setStatus(`Only .txt, .epub and .zip are supported — skipped: ${rejected.join(", ")}`, "bad");
  refresh();
}

function removeFile(index) {
  files.splice(index, 1);
  resetReview();
  refresh();
}

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function refresh() {
  ui.fileList.hidden = files.length === 0;
  ui.fileList.innerHTML = "";
  files.forEach((file, index) => {
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = file.name;
    const size = document.createElement("span");
    size.className = "size";
    size.textContent = humanSize(file.size);
    const remove = document.createElement("button");
    remove.className = "remove";
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Remove";
    remove.addEventListener("click", () => removeFile(index));
    row.append(name, size, remove);
    ui.fileList.append(row);
  });

  ui.fileLabel.textContent = files.length
    ? `${files.length} file(s) chosen — click to add more`
    : "Drop .txt, .epub or .zip files here, or click to choose";

  ui.loadBtn.disabled = busy || !pyodide || files.length === 0;
  if (!busy) {
    if (!pyodide) setStatus("Still loading the converter…");
    else if (!files.length) setStatus("Add a file to start.");
    else if (!works.length) setStatus(`${files.length} file(s) ready. Press Read files.`);
  }
}

function setStatus(text, kind) {
  ui.status.textContent = text;
  ui.status.className = "status" + (kind ? ` status--${kind}` : "");
}

function setSaveStatus(text, kind) {
  ui.saveStatus.textContent = text;
  ui.saveStatus.className = "status" + (kind ? ` status--${kind}` : "");
}

function resetReview() {
  works = [];
  ui.reviewStep.hidden = true;
  ui.saveStep.hidden = true;
  ui.review.innerHTML = "";
  clearDownloads();
  clearResults();
}

function clearDownloads() {
  objectUrls.forEach(URL.revokeObjectURL);
  objectUrls = [];
  ui.downloads.innerHTML = "";
  ui.downloads.hidden = true;
}

/* ---------------------------------------------------------------- analyze */

async function stageFiles() {
  pyodide.runPython(`
import os, shutil
for _d in (${JSON.stringify(WORK_DIR)}, ${JSON.stringify(OUT_DIR)}):
    shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(_d)
`);
  const paths = [];
  for (const file of files) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const target = `${WORK_DIR}/${file.name}`;
    pyodide.FS.writeFile(target, bytes);
    // A file written into the virtual FS is stamped "now", and a book with no
    // date of its own falls back to that mtime for publishedDate. Restore the
    // real one so the web result matches what the desktop version produces.
    try {
      pyodide.FS.utime(target, file.lastModified, file.lastModified);
    } catch (_) { /* a browser that hides lastModified just keeps today */ }
    paths.push(target);
  }
  return paths;
}

async function analyze() {
  if (busy || !pyodide || !files.length) return;
  busy = true;
  ui.loadBtn.disabled = true;
  resetReview();
  setStatus("Reading… a large file takes a moment.");
  await new Promise((r) => setTimeout(r, 0));   // let the status paint

  try {
    const paths = await stageFiles();
    pyodide.globals.set("JS_PATHS", pyodide.toPy(paths));
    pyodide.globals.set("JS_OPTS", pyodide.toPy({}));
    const json = pyodide.runPython(`
import importlib, api
importlib.reload(api)
api.analyze(list(JS_PATHS), **dict(JS_OPTS))
`);
    works = JSON.parse(json);
    renderReview();

    const ok = works.filter((w) => !w.error).length;
    const bad = works.length - ok;
    setStatus(bad ? `Read ${ok}, failed ${bad}.` : `Read ${ok} work(s). Check them below.`,
              bad ? "bad" : "good");
  } catch (error) {
    setStatus(`Error: ${error.message}`, "bad");
  } finally {
    busy = false;
    refresh();
    // renderReview ran while this was still busy, which left the save button
    // disabled until something else refreshed it. Settle it now.
    updateSaveState();
  }
}

/* ----------------------------------------------------------------- review */

function field(labelText, value, onInput, wide) {
  const wrap = document.createElement("div");
  wrap.className = "field" + (wide ? " field--wide" : "");
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "text";
  input.value = value || "";
  input.addEventListener("input", () => onInput(input.value));
  wrap.append(label, input);
  return wrap;
}

function renderReview() {
  ui.review.innerHTML = "";
  let usable = 0;

  works.forEach((work) => {
    const card = document.createElement("div");
    card.className = "work" + (work.error ? " work--bad" : "");

    const head = document.createElement("div");
    head.className = "work__head";
    const file = document.createElement("div");
    file.className = "work__file";
    file.textContent = work.file;
    head.append(file);

    if (work.error) {
      card.append(head);
      const message = document.createElement("div");
      message.className = "work__error";
      message.textContent = work.error;
      card.append(message);
      ui.review.append(card);
      return;
    }
    usable++;

    const fields = document.createElement("div");
    fields.className = "fields";
    fields.append(
      field("Title", work.title, (v) => { work.title = v; }),
      field("Author", work.author, (v) => { work.author = v; }),
    );

    // A work with no summary shows none in the app; this is where one can be
    // written by hand, and left empty it stays absent rather than blank.
    const summaryWrap = document.createElement("div");
    summaryWrap.className = "field field--wide";
    const summaryLabel = document.createElement("label");
    summaryLabel.textContent = work.summary ? "Summary" : "Summary (optional)";
    const summaryBox = document.createElement("textarea");
    summaryBox.className = "field__area";
    summaryBox.rows = 3;
    summaryBox.placeholder = "No summary was found. Leave empty to show none.";
    summaryBox.value = work.summary || "";
    summaryBox.addEventListener("input", () => { work.summary = summaryBox.value; });
    summaryWrap.append(summaryLabel, summaryBox);
    fields.append(summaryWrap);

    const row = document.createElement("div");
    row.className = "field field--wide field-row";
    const done = document.createElement("label");
    done.className = "opt";
    const doneBox = document.createElement("input");
    doneBox.type = "checkbox";
    doneBox.checked = work.complete;
    doneBox.addEventListener("change", () => { work.complete = doneBox.checked; });
    const doneText = document.createElement("span");
    doneText.textContent = "Completed";
    done.append(doneBox, doneText);
    row.append(done);

    const lang = document.createElement("span");
    lang.style.cssText = "font-size:13px;color:var(--muted)";
    lang.textContent = work.language ? `Language: ${work.language}` : "";
    row.append(lang);
    fields.append(row);
    head.append(fields);

    const meta = document.createElement("div");
    meta.className = "meta-line";
    const pills = [];
    if (work.ao3Id) pills.push(["pill", `AO3 ${work.ao3Id}`]);
    if (work.fandoms?.length) pills.push(["pill", work.fandoms[0]]);
    if (work.tagCount) pills.push(["pill", `${work.tagCount} tags`]);
    if (work.volumes?.length) pills.push(["pill", `${work.volumes.length} volumes joined`]);
    if (work.droppedImages) pills.push(["pill pill--warn", `${work.droppedImages} images dropped`]);
    for (const [cls, text] of pills) {
      const pill = document.createElement("span");
      pill.className = cls;
      pill.textContent = text;
      meta.append(pill);
    }
    if (pills.length) head.append(meta);
    card.append(head);

    // --- chapter list -------------------------------------------------------
    work.chapters.forEach((c) => { c.keep = true; });

    const bar = document.createElement("div");
    bar.className = "chapters__bar";
    const all = document.createElement("button");
    all.type = "button";
    all.className = "linkbtn";
    all.textContent = "Select all";
    const none = document.createElement("button");
    none.type = "button";
    none.className = "linkbtn";
    none.textContent = "Clear all";
    const count = document.createElement("span");
    count.className = "chapters__count";
    bar.append(all, none, count);
    card.append(bar);

    const list = document.createElement("div");
    list.className = "chapters";
    const rows = [];

    work.chapters.forEach((chapter, index) => {
      const line = document.createElement("div");
      line.className = "chapter";

      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;

      const num = document.createElement("span");
      num.className = "chapter__num";

      const title = document.createElement("input");
      title.type = "text";
      title.className = "chapter__title";
      title.value = chapter.title;
      title.addEventListener("input", () => { chapter.title = title.value; });

      const chars = document.createElement("span");
      chars.className = "chapter__chars" + (chapter.chars > LONG_CHAPTER ? " is-long" : "");
      chars.textContent = chapter.chars.toLocaleString();
      if (chapter.chars > LONG_CHAPTER) {
        chars.title = "A very long chapter — this is how the source is split.";
      }

      box.addEventListener("change", () => {
        chapter.keep = box.checked;
        line.classList.toggle("is-out", !box.checked);
        renumber();
      });

      line.append(box, num, title, chars);
      list.append(line);
      rows.push({ line, num, box });
    });

    function renumber() {
      let n = 0;
      rows.forEach((row, index) => {
        const keep = work.chapters[index].keep;
        row.num.textContent = keep ? String(++n) : "—";
      });
      count.textContent = `${n} of ${work.chapters.length} chapters`;
      const totalChars = work.chapters.reduce((sum, c) => sum + (c.keep ? c.chars : 0), 0);
      count.textContent += ` · ${totalChars.toLocaleString()} chars`;
      updateSaveState();
    }

    all.addEventListener("click", () => {
      rows.forEach((row, index) => {
        row.box.checked = true;
        work.chapters[index].keep = true;
        row.line.classList.remove("is-out");
      });
      renumber();
    });
    none.addEventListener("click", () => {
      rows.forEach((row, index) => {
        row.box.checked = false;
        work.chapters[index].keep = false;
        row.line.classList.add("is-out");
      });
      renumber();
    });

    card.append(list);
    ui.review.append(card);
    renumber();
  });

  ui.reviewStep.hidden = works.length === 0;
  ui.saveStep.hidden = usable === 0;
  updateSaveState();
}

function readyWorks() {
  return works.filter((w) => !w.error && w.chapters.some((c) => c.keep));
}

/** Button and option state. `keepStatus` leaves a finished run's own message
 *  in place, which would otherwise be overwritten the moment it appeared. */
function updateSaveState({ keepStatus = false } = {}) {
  const ready = readyWorks().length;
  ui.saveBtn.disabled = busy || ready === 0;
  // With a single work there is nothing to split apart, so the choice is noise.
  ui.separateOpt.hidden = ready < 2;
  if (!busy && !keepStatus) {
    setSaveStatus(ready ? `${ready} work(s) will be saved.` : "No chapters selected.",
                  ready ? "" : "bad");
  }
}

/* ------------------------------------------------------------------- save */

function clearResults() {
  ui.results.hidden = true;
  ui.results.innerHTML = "";
}

/** One line of the finished-work summary, in the page's own style. */
function addResult(title, detail, kind) {
  ui.results.hidden = false;
  const row = document.createElement("li");
  row.className = "result" + (kind ? ` result--${kind}` : "");
  const name = document.createElement("span");
  name.className = "result__name";
  name.textContent = title;
  row.append(name);
  if (detail) {
    const note = document.createElement("span");
    note.className = "result__detail";
    note.textContent = detail;
    row.append(note);
  }
  ui.results.append(row);
}

async function save() {
  const ready = readyWorks();
  if (busy || !ready.length) return;
  busy = true;
  ui.saveBtn.disabled = true;
  clearDownloads();
  clearResults();
  setSaveStatus("Building…");
  await new Promise((r) => setTimeout(r, 0));

  try {
    const payload = ready.map((w) => ({
      key: w.key,
      file: w.file,
      title: w.title,
      author: w.author,
      complete: w.complete,
      summary: w.summary,
      language: w.language,
      chapters: w.chapters.map((c) => ({ title: c.title, keep: !!c.keep })),
    }));
    const names = ready.map((w) => `${WORK_DIR}/${w.file}`);
    pyodide.globals.set("JS_PATHS", pyodide.toPy(names));
    const zipName = pyodide.runPython("import archive\narchive.suggest_zip_name(list(JS_PATHS))");

    pyodide.globals.set("JS_PAYLOAD", JSON.stringify(payload));
    pyodide.globals.set("JS_OUT", `${OUT_DIR}/${zipName}.zip`);
    pyodide.globals.set("JS_SEPARATE", ui.separate.checked);
    const result = JSON.parse(pyodide.runPython(`
import api
api.build(JS_PAYLOAD, JS_OUT, separate=bool(JS_SEPARATE))
`));

    if (!result.files.length) {
      setSaveStatus("Nothing to save.", "bad");
    } else {
      for (const name of result.files) offerDownload(name);
      setSaveStatus(`Done — ${result.works} work(s).`, "good");
      for (const w of ready) {
        const kept = w.chapters.filter((c) => c.keep).length;
        const dropped = w.chapters.length - kept;
        addResult(w.title, `${kept} chapters` + (dropped ? `, ${dropped} left out` : ""));
      }
    }
    if (result.skipped?.length) {
      addResult("Skipped", result.skipped.join(", "), "warn");
    }
  } catch (error) {
    addResult("Error", error.message, "bad");
    setSaveStatus("Something went wrong.", "bad");
  } finally {
    busy = false;
    updateSaveState({ keepStatus: true });
  }
}

function offerDownload(name) {
  const bytes = pyodide.FS.readFile(`${OUT_DIR}/${name}`);
  const blob = new Blob([bytes], { type: "application/zip" });
  const url = URL.createObjectURL(blob);
  objectUrls.push(url);

  const link = document.createElement("a");
  link.className = "download";
  link.href = url;
  link.download = name;
  link.append(document.createTextNode(`⬇ ${name}`));
  const size = document.createElement("span");
  size.className = "size";
  size.textContent = humanSize(blob.size);
  link.append(size);

  ui.downloads.hidden = false;
  ui.downloads.append(link);
}

/* ----------------------------------------------------------------- events */

ui.fileInput.addEventListener("change", (event) => {
  addFiles(event.target.files);
  event.target.value = "";
});

["dragenter", "dragover"].forEach((type) =>
  ui.dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    ui.dropZone.classList.add("is-over");
  }));

["dragleave", "drop"].forEach((type) =>
  ui.dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    ui.dropZone.classList.remove("is-over");
  }));

ui.dropZone.addEventListener("drop", (event) => {
  if (event.dataTransfer?.files?.length) addFiles(event.dataTransfer.files);
});

ui.dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    ui.fileInput.click();
  }
});

ui.loadBtn.addEventListener("click", analyze);
ui.saveBtn.addEventListener("click", save);

refresh();
boot();
