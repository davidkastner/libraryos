const state = { query: "", availability: "all", sort: "recent" };
const papers = document.querySelector("#papers");
const template = document.querySelector("#paper-template");
const search = document.querySelector("#search");
const resultCount = document.querySelector("#result-count");
const empty = document.querySelector("#empty");
const toast = document.querySelector("#toast");
let searchTimer;
let selected = null;

async function operation(name, arguments_) {
  const response = await fetch(`/v1/operations/${name}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(arguments_),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error?.message || "LibraryOS could not complete the request.");
  }
  return payload.result;
}

function authorText(authors) {
  if (!authors.length) return "Unknown authors";
  if (authors.length <= 3) return authors.join(", ");
  return `${authors.slice(0, 3).join(", ")} et al.`;
}

function identifierText(identifiers) {
  const preferred = identifiers.find(item => item.scheme === "doi") || identifiers[0];
  return preferred ? `${preferred.scheme.toUpperCase()} ${preferred.value}` : "";
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 3200);
}

async function openPaper(item, button) {
  button.disabled = true;
  try {
    await operation("read.open", { work_id: item.id, application: "preview" });
    showToast("Opened in Preview · No review was recorded");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

function selectPaper(article, item) {
  papers.querySelectorAll(".paper.selected").forEach(row => row.classList.remove("selected"));
  article.classList.add("selected");
  selected = { article, item };
}

function render(items) {
  papers.replaceChildren();
  selected = null;
  for (const item of items) {
    const fragment = template.content.cloneNode(true);
    const article = fragment.querySelector(".paper");
    fragment.querySelector("h2").textContent = item.title || "Untitled work";
    fragment.querySelector(".authors").textContent = authorText(item.authors);
    fragment.querySelector(".venue").textContent = item.container_title || "";
    fragment.querySelector(".year").textContent = item.issued || "";
    fragment.querySelector(".identifier").textContent = identifierText(item.identifiers);
    const sourceState = fragment.querySelector(".source-state");
    const button = fragment.querySelector(".open-button");
    if (item.pdf_count) {
      sourceState.textContent = item.pdf_count === 1 ? "Downloaded" : `${item.pdf_count} files`;
      button.setAttribute("aria-label", `Open ${item.title || "paper"} in Preview`);
      button.title = "Open in Preview";
      button.addEventListener("click", event => {
        event.stopPropagation();
        openPaper(item, button);
      });
      article.addEventListener("dblclick", () => openPaper(item, button));
    } else {
      sourceState.textContent = item.source_count ? "No PDF" : "Missing";
      sourceState.classList.add("missing");
      article.classList.add("no-pdf");
      button.disabled = true;
    }
    article.addEventListener("click", () => selectPaper(article, item));
    article.addEventListener("focus", () => selectPaper(article, item));
    article.addEventListener("keydown", event => {
      if (event.key === "Enter" && item.pdf_count) openPaper(item, button);
    });
    papers.append(fragment);
  }
}

async function loadPapers() {
  papers.hidden = false;
  empty.hidden = true;
  papers.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  try {
    const result = await operation("work.query", {
      query: state.query,
      availability: state.availability,
      sort: state.sort,
      limit: 100,
      offset: 0,
    });
    document.querySelector("#nav-count").textContent = result.total.toLocaleString();
    resultCount.textContent = `${result.total.toLocaleString()} ${result.total === 1 ? "paper" : "papers"}`;
    if (result.items.length) {
      render(result.items);
    } else {
      papers.hidden = true;
      empty.hidden = false;
    }
  } catch (error) {
    papers.hidden = true;
    empty.hidden = false;
    empty.querySelector("h2").textContent = "Library unavailable";
    empty.querySelector("p").textContent = error.message;
    resultCount.textContent = "Unable to load papers";
  }
}

async function initialize() {
  const status = await operation("library.status", {});
  const descriptor = status.descriptor;
  document.querySelector("#library-subtitle").textContent = descriptor.title || "LibraryOS";
  await loadPapers();
}

search.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  state.query = search.value.trim();
  searchTimer = window.setTimeout(loadPapers, 180);
});
document.querySelector("#sort").addEventListener("change", event => {
  state.sort = event.target.value;
  loadPapers();
});
document.querySelectorAll(".nav-item[data-filter]").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    document.querySelector("#page-title").textContent =
      button.dataset.filter === "all" ? "All Papers" :
      button.dataset.filter === "local_pdf" ? "Downloaded" : "Needs Source";
    state.availability = button.dataset.filter;
    loadPapers();
  });
});
document.querySelector("[data-recent]").addEventListener("click", event => {
  document.querySelectorAll(".nav-item").forEach(item => item.classList.remove("active"));
  event.currentTarget.classList.add("active");
  document.querySelector("#page-title").textContent = "Recently Added";
  state.availability = "all";
  state.sort = "recent";
  document.querySelector("#sort").value = "recent";
  loadPapers();
});
document.querySelector("#clear-search").addEventListener("click", () => {
  search.value = "";
  state.query = "";
  state.availability = "all";
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.filter === "all"));
  document.querySelector("#page-title").textContent = "All Papers";
  loadPapers();
});
document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    search.focus();
  }
});

initialize().catch(error => {
  resultCount.textContent = "Unable to connect";
  showToast(error.message);
});
