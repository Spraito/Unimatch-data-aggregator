const $ = (selector, root = document) => root.querySelector(selector);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const icon = (name) =>
  `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const e = escapeHTML;
const viewTitles = {
  universities: "Explore colleges",
  programs: "Degree programs",
  saved: "Saved schools",
  compare: "Compare schools",
};
function storedList(key) {
  try {
    const list = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(list)
      ? [...new Set(list.filter((v) => typeof v === "string"))].slice(0, 100)
      : [];
  } catch {
    return [];
  }
}
const state = {
  view: "universities",
  query: "",
  city: "",
  sector: "",
  school: "",
  topic: "",
  sort: "featured",
  page: 1,
  saved: storedList("unimatch.saved.v1"),
  compare: storedList("unimatch.compare.v1").slice(0, 3),
  schools: [],
  stats: null,
  detail: null,
  tab: "overview",
};
let renderVersion = 0,
  searchTimer,
  toastTimer,
  detailVersion = 0;

async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== "" && value != null) url.searchParams.set(key, value);
  });
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(
      problem.error ||
        `The server returned ${response.status}. Please try again.`,
    );
  }
  return response.json();
}

function persist() {
  try {
    localStorage.setItem("unimatch.saved.v1", JSON.stringify(state.saved));
    localStorage.setItem("unimatch.compare.v1", JSON.stringify(state.compare));
  } catch {
    toast(
      "Your browser couldn't save this shortlist. It will last for this visit.",
    );
  }
  $("#saved-count").textContent = state.saved.length;
  $("#stat-saved").textContent = state.saved.length;
  $("#compare-count").textContent = state.compare.length;
}

function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").classList.add("visible");
  toastTimer = setTimeout(() => $("#toast").classList.remove("visible"), 3500);
}

function dateLabel(value) {
  if (!value) return "Not yet collected";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Date not recorded"
    : new Intl.DateTimeFormat("en-PH", {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "Asia/Manila",
      }).format(date);
}

function safeURL(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function sourceLink(value, label = "View official source") {
  const url = safeURL(value);
  return url
    ? `<a href="${e(url)}" target="_blank" rel="noopener noreferrer">${e(label)} ↗</a>`
    : "";
}

function schoolMark(school, small = false) {
  return `<span class="school-mark ${e(school.color)} ${small ? "small" : ""}" aria-hidden="true">${e(school.short_name === "UP Diliman" ? "UP" : school.short_name)}</span>`;
}

function statusTag(school) {
  return school.collection_status === "pending"
    ? '<span class="tag neutral"><span class="tag-dot"></span>Awaiting collection</span>'
    : '<span class="tag amber"><span class="tag-dot"></span>Partial data · needs review</span>';
}

function schoolCard(school) {
  const saved = state.saved.includes(school.id),
    compared = state.compare.includes(school.id);
  return `<article class="school-card ${e(school.color)}" data-school-card="${e(school.id)}">
    <div class="card-top">${schoolMark(school)}<button class="icon-button save-button" data-action="save" data-school="${e(school.id)}" aria-pressed="${saved}" aria-label="${saved ? "Unsave" : "Save"} ${e(school.short_name)}">${icon("save")}</button></div>
    <h3>${e(school.name)}</h3><p class="card-location">${icon("pin")}${e(school.campus_location)}</p>
    <div class="card-tags"><span class="tag">${e(school.sector)} university</span>${statusTag(school)}</div>
    <div class="card-metrics"><div><span class="metric-label">PROGRAM ENTRIES COLLECTED</span><span class="metric-value">${school.program_count ? `<strong>${school.program_count}</strong> entries` : "Not yet indexed"}</span></div><div><span class="metric-label">TUITION INFORMATION</span><span class="metric-value">${school.tuition_evidence_count ? "See fee evidence" : "Not available"}</span></div></div>
    <div class="card-footer"><button class="text-button" data-action="details" data-school="${e(school.id)}">View university ${icon("arrow")}</button><button class="compare-button" data-action="compare" data-school="${e(school.id)}" aria-pressed="${compared}" aria-label="${compared ? "Remove" : "Add"} ${e(school.short_name)} ${compared ? "from" : "to"} comparison"><span class="compare-check">${icon("check")}</span>Compare</button></div>
  </article>`;
}

function programCard(program) {
  return `<article class="program-card"><div class="program-school">${schoolMark(program, true)}<span>${e(program.short_name)} <span aria-hidden="true">·</span> ${e(program.city)}</span></div>
    <h3>${e(program.name)}</h3><span class="tag">${e(program.topic)}</span>
    <p class="program-disclaimer">${program.strand_alignment ? `Published strand note: ${e(program.strand_alignment)}` : "Strand eligibility has not been published in this record."}</p>
    <button class="text-button" data-action="details" data-school="${e(program.university_id)}" data-tab="programs">View source university ${icon("arrow")}</button></article>`;
}

function emptyState(
  title,
  description,
  action = "reset",
  label = "Clear filters",
  symbol = "search",
) {
  return `<div class="empty-state"><div class="empty-icon">${icon(symbol)}</div><h3>${e(title)}</h3><p>${e(description)}</p><button class="button secondary" data-action="${action}">${e(label)} ${icon("arrow")}</button></div>`;
}

function controls() {
  const programs = state.view === "programs",
    compare = state.view === "compare";
  $("#hero").hidden = state.view !== "universities";
  $("#filter-bar").hidden = compare;
  $("#results-meta").hidden = compare;
  $("#city-wrap").hidden = programs;
  $("#sector-wrap").hidden = programs;
  $("#school-wrap").hidden = !programs;
  $("#topic-wrap").hidden = !programs;
  $("#sort-wrap").hidden = programs;
  $("#search").placeholder = programs
    ? "Search a degree, subject, or university…"
    : "Search a university or program…";
  const descriptions = {
    universities: [
      "A PLACE TO BEGIN",
      "Explore universities",
      "Different paths. New possibilities. Find your starting point.",
    ],
    programs: [
      "FOLLOW YOUR CURIOSITY",
      "Discover degree programs",
      "Explore collected program entries. Topics are browsing aids, not eligibility advice.",
    ],
    saved: [
      "YOUR NEXT-CHAPTER SHORTLIST",
      "Saved schools",
      "A little collection of possibilities, saved in this browser.",
    ],
    compare: [
      "SEE THE POSSIBILITIES SIDE BY SIDE",
      "Compare your schools",
      "Choose up to three universities. Missing information stays visible.",
    ],
  };
  ["#section-eyebrow", "#section-title", "#section-description"].forEach(
    (id, index) => {
      $(id).textContent = descriptions[state.view][index];
    },
  );
  $("#breadcrumb-view").textContent = viewTitles[state.view];
  document.querySelectorAll(".nav-item").forEach((button) => {
    const selected = button.dataset.view === state.view;
    button.classList.toggle("active", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function closeMenu() {
  const wasOpen = $("#sidebar").classList.contains("open");
  $("#sidebar").classList.remove("open");
  $("#sidebar-shade").hidden = true;
  $("#menu-toggle").setAttribute("aria-expanded", "false");
  if (wasOpen) $("#menu-toggle").focus();
}

function setView(view, school = "") {
  clearTimeout(searchTimer);
  if (!viewTitles[view]) return;
  state.view = view;
  Object.assign(state, {
    query: "",
    city: "",
    sector: "",
    topic: "",
    school,
    sort: "featured",
    page: 1,
  });
  ["search", "city", "sector", "topic", "school", "sort"].forEach((id) => {
    $("#" + id).value = id === "search" ? "" : state[id];
  });
  history.replaceState(null, "", "#" + view);
  closeMenu();
  controls();
  render();
}

async function render() {
  const version = ++renderVersion;
  const results = $("#results");
  results.setAttribute("aria-busy", "true");
  $("#pagination").hidden = true;
  try {
    if (state.view === "compare") {
      if (!state.compare.length) {
        results.innerHTML = emptyState(
          "A clearer choice starts with a pair",
          "Select Compare on any university card to add it here. You can compare up to three schools.",
          "browse",
          "Explore colleges",
          "compare",
        );
      } else {
        const data = await api("/api/compare", {
          ids: state.compare.join(","),
        });
        if (version !== renderVersion) return;
        renderComparison(data.items);
      }
      return;
    }
    if (state.view === "programs") {
      const data = await api("/api/programs", {
        search: state.query,
        university_id: state.school,
        topic: state.topic,
        page: state.page,
        limit: 12,
      });
      if (version !== renderVersion) return;
      $("#results-count").innerHTML =
        `<strong>${data.total}</strong> collected program ${data.total === 1 ? "entry" : "entries"}`;
      results.innerHTML = data.items.length
        ? `<div class="program-grid">${data.items.map(programCard).join("")}</div>`
        : emptyState(
            "No collected programs match",
            "Try another search or school. An empty result does not mean a university offers no programs.",
          );
      const pages = Math.ceil(data.total / data.limit);
      if (pages > 1) {
        $("#pagination").hidden = false;
        $("#pagination").innerHTML =
          `<button data-action="previous" ${data.page === 1 ? "disabled" : ""}>Previous</button><span>Page ${data.page} of ${pages}</span><button data-action="next" ${data.page === pages ? "disabled" : ""}>Next</button>`;
      }
    } else {
      const data = await api("/api/universities", {
        search: state.query,
        city: state.city,
        sector: state.sector,
        sort: state.sort,
      });
      if (version !== renderVersion) return;
      const schools =
        state.view === "saved"
          ? data.items.filter((s) => state.saved.includes(s.id))
          : data.items;
      $("#results-count").innerHTML =
        `<strong>${schools.length}</strong> ${schools.length === 1 ? "university" : "universities"} ${state.view === "saved" ? "on your shortlist" : "to explore"}`;
      results.innerHTML = schools.length
        ? `<div class="school-grid">${schools.map(schoolCard).join("")}</div>`
        : state.view === "saved" && !state.saved.length
          ? emptyState(
              "Keep a few possibilities close",
              "Tap the bookmark on a university card to build your shortlist. It stays in this browser.",
              "browse",
              "Explore colleges",
              "save",
            )
          : emptyState(
              "A different search might open a door",
              "Try a university name, degree, or another location to explore more possibilities.",
            );
    }
  } catch (error) {
    if (version !== renderVersion) return;
    $("#results-count").textContent = "Unable to load results";
    results.innerHTML = `<div class="error-state"><p>${e(error.message)}</p><button class="button secondary" data-action="retry">Try again</button></div>`;
  } finally {
    if (version === renderVersion) results.setAttribute("aria-busy", "false");
  }
}

function renderComparison(schools) {
  const field = (school, key) => {
    const entries = school.evidence?.[key] || [];
    return entries.length
      ? entries
          .map(
            (entry) =>
              `<p>${e(entry.text || "See source evidence")}</p>${(entry.source_context || []).map((text) => `<p class="context">${e(text)}</p>`).join("")}`,
          )
          .join("")
      : '<span class="muted">Not available</span>';
  };
  const rows = [
    ["Campus", (school) => e(school.campus_location)],
    ["University type", (school) => e(school.sector)],
    [
      "Program entries",
      (school) =>
        school.program_count
          ? `${school.program_count} collected; verify campus availability`
          : "Not yet indexed",
    ],
    ["Tuition evidence", (school) => field(school, "tuition_estimates")],
    ["Exam coverage", (school) => field(school, "entrance_exam_coverage")],
    [
      "Published application notice",
      (school) => field(school, "application_status"),
    ],
    ["Dated deadlines", (school) => field(school, "application_deadlines")],
    ["Collected", (school) => e(dateLabel(school.collected_at))],
    [
      "Review notes",
      (school) =>
        (school.evidence?.source_notes || [])
          .map((note) => `<p>${e(note)}</p>`)
          .join("") || "Confirm details with official admissions sources.",
    ],
  ];
  $("#results").innerHTML =
    `<div class="compare-scroll" role="region" aria-label="University comparison" tabindex="0"><table class="compare-table"><thead><tr><th scope="col">Your possibilities</th>${schools.map((school) => `<th scope="col" class="compare-school">${schoolMark(school)}<h3>${e(school.name)}</h3>${statusTag(school)}<br><button class="text-button" data-action="details" data-school="${e(school.id)}">View details ${icon("arrow")}</button><button class="compare-remove" data-action="compare" data-school="${e(school.id)}" aria-label="Remove ${e(school.short_name)} from comparison">Remove</button></th>`).join("")}</tr></thead><tbody>${rows.map(([label, value]) => `<tr><th scope="row">${e(label)}</th>${schools.map((school) => `<td>${value(school)}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="comparison-note">Program counts describe this dataset’s coverage, not the university’s complete offerings or its quality. Published notices may refer to an earlier intake.</p><button class="button secondary" data-action="browse">Add another school ${icon("arrow")}</button>`;
}

function evidenceBlock(school, name, label) {
  const entries = school.evidence?.[name] || [];
  return `<h3>${e(label)}</h3>${entries.length ? `<ul class="evidence-list">${entries.map((item) => `<li><p>${e(item.text || "Source evidence available")}</p>${(item.source_context || []).map((context) => `<p class="context">${e(context)}</p>`).join("")}${sourceLink(item.source_url)}</li>`).join("")}</ul>` : "<p>Not available in the collected sources. Check with the university for current information.</p>"}`;
}

async function openDetails(id, tab = "overview") {
  const version = ++detailVersion;
  const dialog = $("#school-dialog");
  state.detail = null;
  state.tab = tab;
  $("#detail-body").innerHTML =
    '<div class="detail-panel"><h2 id="dialog-title">Loading university…</h2><p>Gathering the collected records and sources.</p></div>';
  if (!dialog.open) dialog.showModal();
  try {
    const school = await api("/api/universities/" + encodeURIComponent(id));
    if (version !== detailVersion || !dialog.open) return;
    state.detail = school;
    renderDetails();
  } catch (error) {
    if (version !== detailVersion) return;
    $("#detail-body").innerHTML =
      `<div class="detail-panel"><h2 id="dialog-title">Unable to load university</h2><p>${e(error.message)}</p><button class="button secondary" data-action="details" data-school="${e(id)}">Try again</button></div>`;
  }
}

function renderDetails() {
  const school = state.detail;
  if (!school) return;
  const tabs = [
    ["overview", "Overview"],
    ["programs", `Programs (${school.program_count})`],
    ["admissions", "Admissions & fees"],
  ];
  const notes = school.evidence?.source_notes || [];
  let content = "";
  if (state.tab === "overview") {
    const sources = (school.sources || [])
      .map((source) => safeURL(source.final_url || source.requested_url))
      .filter(Boolean);
    content = `<h3>A starting point for ${e(school.short_name)}</h3><p>${school.collection_status === "pending" ? "This university is included in the Metro Manila directory. Its admissions and program information has not yet been collected for this prototype." : "These entries were collected from official public pages. They remain source evidence requiring review, including confirmation of the campus, academic year and current availability."}</p><dl class="detail-facts"><div><dt>UNIVERSITY TYPE</dt><dd>${e(school.sector)}</dd></div><div><dt>PROGRAM ENTRIES</dt><dd>${school.program_count || "Not indexed"}</dd></div><div><dt>LAST COLLECTED</dt><dd>${e(dateLabel(school.collected_at))}</dd></div></dl>${notes.length ? `<div class="evidence-note">${notes.map((note) => `<p>${e(note)}</p>`).join("")}</div>` : ""}<h3>Official sources</h3><ul class="source-list">${[...new Set(sources)].map((url) => `<li>${sourceLink(url, new URL(url).hostname + new URL(url).pathname)}</li>`).join("") || "<li>No source links recorded.</li>"}</ul><p>Missing data does not mean a fee is zero or a program is unavailable. Verify details directly with admissions.</p>`;
  } else if (state.tab === "programs") {
    content = `<h3>Collected program entries</h3><p>Program names are quoted from the source listing. Topic labels help you browse; strand eligibility and campus availability need confirmation.</p>${school.program_count ? `<label><span class="sr-only">Search this university's programs</span><input class="detail-search" id="detail-program-search" type="search" placeholder="Search these programs…" maxlength="200"></label><div id="detail-program-results">${detailPrograms(school.programs)}</div>` : "<p>No program entries have been collected for this university yet.</p>"}`;
  } else {
    content =
      `<div class="evidence-note"><p>Published statements below are unverified. Check their academic year and source before using them for an application.</p>${notes.map((note) => `<p>${e(note)}</p>`).join("")}</div>` +
      evidenceBlock(
        school,
        "admission_requirements",
        "Admission requirements",
      ) +
      evidenceBlock(
        school,
        "entrance_exam_coverage",
        "Entrance exam coverage",
      ) +
      evidenceBlock(school, "tuition_estimates", "Tuition and cost evidence") +
      evidenceBlock(school, "application_deadlines", "Application dates") +
      evidenceBlock(
        school,
        "application_status",
        "Published application notice",
      );
  }
  $("#detail-body").innerHTML =
    `<header class="detail-head">${schoolMark(school)}<span class="eyebrow">YOUR NEXT CHAPTER, IN FOCUS</span><h2 id="dialog-title">${e(school.name)}</h2><p class="card-location">${icon("pin")}${e(school.campus_location)}</p>${statusTag(school)}</header><div class="detail-tabs" role="tablist" aria-label="University details">${tabs.map(([id, label]) => `<button id="tab-${id}" role="tab" data-action="tab" data-tab="${id}" aria-controls="detail-panel" aria-selected="${state.tab === id}" tabindex="${state.tab === id ? "0" : "-1"}">${e(label)}</button>`).join("")}</div><section class="detail-panel" id="detail-panel" role="tabpanel" aria-labelledby="tab-${state.tab}" tabindex="0">${content}<div class="dialog-actions"><button class="button secondary" data-action="save" data-school="${e(school.id)}" aria-pressed="${state.saved.includes(school.id)}">${icon("save")}${state.saved.includes(school.id) ? "Remove from saved" : "Save this school"}</button>${safeURL(school.official_url) ? `<a class="button primary" href="${e(safeURL(school.official_url))}" target="_blank" rel="noopener noreferrer">Official university page ${icon("external")}</a>` : ""}</div></section>`;
}

function detailPrograms(programs) {
  return programs.length
    ? `<ul class="detail-program-list">${programs.map((program) => `<li>${e(program.name)}<br><span class="tag">${e(program.topic)}</span>${program.strand_alignment ? `<p>${e(program.strand_alignment)}</p>` : ""}<br>${sourceLink(program.source_url)}</li>`).join("")}</ul>`
    : "<p>No collected program entries match your search.</p>";
}

function openNotes() {
  ++detailVersion;
  state.detail = null;
  $("#detail-body").innerHTML =
    `<header class="detail-head"><span class="eyebrow">A LITTLE CONTEXT</span><h2 id="dialog-title">Clearer choices start with honest data.</h2></header><div class="detail-panel"><h3>What you’re exploring</h3><p>UniMatch PH is an undergraduate research prototype for discovering Metro Manila universities and degree programs. The directory currently includes four institutions. Only PUP has collected program and admissions evidence.</p><h3>Evidence, with room for review</h3><p>The PUP sample was collected on 21 October 2026. Its admissions page refers to different academic years. All collected information needs verification; quoted notices should not be treated as current admissions decisions.</p><h3>A few things to keep in mind</h3><p>Missing tuition does not mean free tuition. Missing strand alignment does not establish eligibility. Program counts show collection coverage, not rankings or complete university offerings. Topics are simple browsing categories, not academic recommendations.</p><h3>Your shortlist</h3><p>Saved schools and comparison choices stay in this browser’s local storage. There are no student accounts, application submissions, or applicant profiles in this prototype.</p><h3>Respectful collection</h3><p>The separate collector checks robots.txt, spaces requests and stops on access blocks. Exploring this website reads saved records; it does not scrape university websites on each visit.</p></div>`;
  if (!$("#school-dialog").open) $("#school-dialog").showModal();
}

document.addEventListener("click", (event) => {
  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    setView(viewButton.dataset.view);
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const id = button.dataset.school;
  switch (button.dataset.action) {
    case "details":
      openDetails(id, button.dataset.tab || "overview");
      break;
    case "save": {
      const index = state.saved.indexOf(id);
      if (index >= 0) state.saved.splice(index, 1);
      else state.saved.push(id);
      persist();
      toast(
        index >= 0
          ? "School removed from your shortlist."
          : "School saved to your shortlist.",
      );
      document.querySelectorAll('[data-action="save"]').forEach((item) => {
        if (item.dataset.school !== id) return;
        const saved = state.saved.includes(id);
        item.setAttribute("aria-pressed", String(saved));
        if (item.classList.contains("save-button"))
          item.setAttribute(
            "aria-label",
            `${saved ? "Unsave" : "Save"} ${state.schools.find((s) => s.id === id)?.short_name || "school"}`,
          );
        else
          item.innerHTML = `${icon("save")}${saved ? "Remove from saved" : "Save this school"}`;
      });
      if (state.view === "saved") render();
      break;
    }
    case "compare": {
      const index = state.compare.indexOf(id);
      if (index >= 0) state.compare.splice(index, 1);
      else if (state.compare.length >= 3) {
        toast(
          "You can compare three schools at a time. Remove one to add another.",
        );
        return;
      } else state.compare.push(id);
      persist();
      render();
      toast(
        index >= 0
          ? "School removed from comparison."
          : "Added to Compare schools in the sidebar.",
      );
      break;
    }
    case "tab":
      state.tab = button.dataset.tab;
      renderDetails();
      $("#tab-" + state.tab)?.focus();
      break;
    case "close-dialog":
      ++detailVersion;
      $("#school-dialog").close();
      break;
    case "notes":
      openNotes();
      break;
    case "browse":
      setView("universities");
      break;
    case "reset":
      setView(state.view);
      break;
    case "retry":
      render();
      break;
    case "next":
      state.page++;
      render();
      break;
    case "previous":
      if (state.page > 1) {
        state.page--;
        render();
      }
      break;
  }
});

$("#search").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  state.page = 1;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(render, 220);
});
["city", "sector", "topic", "school", "sort"].forEach((id) =>
  $("#" + id).addEventListener("change", (event) => {
    state[id] = event.target.value;
    state.page = 1;
    render();
  }),
);
document.addEventListener("input", (event) => {
  if (event.target.id === "detail-program-search" && state.detail) {
    const query = event.target.value.trim().toLowerCase();
    $("#detail-program-results").innerHTML = detailPrograms(
      state.detail.programs.filter((p) => p.name.toLowerCase().includes(query)),
    );
  }
});
document.addEventListener("keydown", (event) => {
  if (
    (event.ctrlKey || event.metaKey) &&
    event.key.toLowerCase() === "k" &&
    !$("#school-dialog").open &&
    state.view !== "compare"
  ) {
    event.preventDefault();
    $("#search").focus();
  }
  if (event.key === "Escape") closeMenu();
  if (
    event.target.getAttribute("role") === "tab" &&
    ["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)
  ) {
    event.preventDefault();
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(event.target);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
            tabs.length;
    tabs[next].click();
  }
});
$("#school-dialog").addEventListener("click", (event) => {
  if (event.target === $("#school-dialog")) {
    ++detailVersion;
    $("#school-dialog").close();
  }
});
$("#menu-toggle").addEventListener("click", () => {
  const open = $("#sidebar").classList.toggle("open");
  $("#sidebar-shade").hidden = !open;
  $("#menu-toggle").setAttribute("aria-expanded", String(open));
});
$("#sidebar-shade").addEventListener("click", closeMenu);

async function initialize() {
  try {
    const [stats, schools] = await Promise.all([
      api("/api/stats"),
      api("/api/universities"),
    ]);
    state.stats = stats;
    state.schools = schools.items;
    const ids = new Set(state.schools.map((s) => s.id));
    state.saved = state.saved.filter((id) => ids.has(id));
    state.compare = state.compare.filter((id) => ids.has(id));
    persist();
    $("#stat-schools").textContent = stats.universities;
    $("#stat-programs").textContent = stats.programs;
    $("#stat-coverage").textContent =
      `From ${stats.collected_universities} collected ${stats.collected_universities === 1 ? "school" : "schools"}`;
    $("#city").innerHTML += stats.cities
      .map((city) => `<option>${e(city)}</option>`)
      .join("");
    $("#topic").innerHTML += stats.topics
      .map((topic) => `<option>${e(topic)}</option>`)
      .join("");
    $("#school").innerHTML += state.schools
      .map((s) => `<option value="${e(s.id)}">${e(s.short_name)}</option>`)
      .join("");
    const requestedView = location.hash.slice(1);
    if (viewTitles[requestedView]) state.view = requestedView;
    controls();
    await render();
  } catch (error) {
    $("#results").setAttribute("aria-busy", "false");
    $("#results-count").textContent = "Unable to load the dataset";
    $("#results").innerHTML =
      `<div class="error-state"><p>${e(error.message)}</p><p>Refresh this page after the server is available.</p></div>`;
  }
}
initialize();
