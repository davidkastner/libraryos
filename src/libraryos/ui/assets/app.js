const PAGE_SIZE = 100;
const state = {
  query: "",
  availability: "all",
  sort: "recent",
  collectionId: null,
  items: [],
  total: 0,
  collections: [],
};
const papers = document.querySelector("#papers");
const template = document.querySelector("#paper-template");
const search = document.querySelector("#search");
const resultCount = document.querySelector("#result-count");
const listFooter = document.querySelector("#list-footer");
const visibleCount = document.querySelector("#visible-count");
const showMore = document.querySelector("#show-more");
const empty = document.querySelector("#empty");
const toast = document.querySelector("#toast");
const collectionNav = document.querySelector("#collection-nav");
const collectionDialog = document.querySelector("#collection-dialog");
const collectionChoices = document.querySelector("#collection-choices");
const newCollectionDialog = document.querySelector("#new-collection-dialog");
let searchTimer;
let selected = null;
let requestNumber = 0;
let papersController = null;

async function operation(name, arguments_, signal = undefined) {
  const response = await fetch(`/v1/operations/${name}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(arguments_),
    signal,
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

async function writeClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("The PDF path could not be copied.");
}

async function copyPaperPath(item, button) {
  button.disabled = true;
  try {
    const result = await operation("read.resolve", {
      work_id: item.id,
      preference: ["local_pdf"],
    });
    if (result.representation !== "local_pdf" || !result.path) {
      throw new Error("No local PDF is available for this paper.");
    }
    await writeClipboard(result.path);
    showToast("PDF path copied");
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

function collectionIncludes(collection, item) {
  return collection.members.some(member => {
    if (typeof member.work === "string") return member.work === item.id;
    const wanted = member.work.identifier;
    return item.identifiers.some(identifier =>
      identifier.scheme === wanted.scheme &&
      identifier.value.toLowerCase() === (wanted.normalized || wanted.value).toLowerCase()
    );
  });
}

function activeCollections() {
  return state.collections.filter(
    item => !item.error && item.collection.status === "active"
  );
}

function collectionNavButton(entry) {
  const collection = entry.collection;
  const button = document.createElement("button");
  button.className = "nav-item";
  button.type = "button";
  button.dataset.collectionId = collection.id;
  button.innerHTML = `
    <span class="nav-symbol">
      <svg viewBox="0 0 24 24"><path d="M3.5 6.5h6l1.7 2h9.3v10h-17z"/></svg>
    </span>
    <span class="nav-title"></span>
    <span class="count"></span>
  `;
  button.querySelector(".nav-title").textContent = collection.title;
  button.querySelector(".count").textContent = collection.members.length.toLocaleString();
  button.classList.toggle("active", state.collectionId === collection.id);
  button.addEventListener("click", () => selectCollection(collection));
  return button;
}

function renderCollections() {
  collectionNav.replaceChildren(
    ...activeCollections()
      .sort((a, b) => a.collection.title.localeCompare(b.collection.title))
      .map(collectionNavButton)
  );
}

async function loadCollections() {
  state.collections = await operation("collection.list", {});
  renderCollections();
}

function setActiveNavigation(target) {
  document.querySelectorAll(".nav-item").forEach(item => item.classList.remove("active"));
  target?.classList.add("active");
}

function selectCollection(collection) {
  state.collectionId = collection.id;
  state.availability = "all";
  setActiveNavigation(document.querySelector(`[data-collection-id="${CSS.escape(collection.id)}"]`));
  document.querySelector("#page-title").textContent = collection.title;
  loadPapers();
}

async function showCollectionPicker(item) {
  const current = activeCollections();
  collectionChoices.replaceChildren();
  document.querySelector("#collection-dialog-subtitle").textContent =
    item.title || "Untitled work";
  if (!current.length) {
    const message = document.createElement("p");
    message.className = "collection-choice";
    message.textContent = "Create a collection first.";
    collectionChoices.append(message);
  }
  for (const entry of current) {
    const collection = entry.collection;
    const label = document.createElement("label");
    label.className = "collection-choice";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = collectionIncludes(collection, item);
    const title = document.createElement("span");
    title.textContent = collection.title;
    const location = document.createElement("small");
    location.textContent = entry.location.kind === "external" ? "External" : "";
    input.addEventListener("change", async () => {
      input.disabled = true;
      try {
        const result = await operation("collection.membership.set", {
          collection_id: collection.id,
          work_id: item.id,
          included: input.checked,
        });
        entry.collection = result.collection;
        entry.location = result.location;
        renderCollections();
        showToast(input.checked ? `Added to ${collection.title}` : `Removed from ${collection.title}`);
        if (state.collectionId === collection.id && !input.checked) loadPapers();
      } catch (error) {
        input.checked = !input.checked;
        showToast(error.message);
      } finally {
        input.disabled = false;
      }
    });
    label.append(input, title, location);
    collectionChoices.append(label);
  }
  collectionDialog.showModal();
}

function render(items, append = false) {
  if (!append) {
    papers.replaceChildren();
    selected = null;
  }
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
    const copyButton = fragment.querySelector(".copy-button");
    const collectionButton = fragment.querySelector(".collection-button");
    collectionButton.title = "Add to Collection";
    collectionButton.setAttribute("aria-label", `Manage collections for ${item.title || "paper"}`);
    collectionButton.addEventListener("click", event => {
      event.stopPropagation();
      showCollectionPicker(item);
    });
    if (item.pdf_count) {
      sourceState.textContent = item.pdf_count === 1 ? "Downloaded" : `${item.pdf_count} files`;
      button.setAttribute("aria-label", `Open ${item.title || "paper"} in Preview`);
      button.title = "Open in Preview";
      copyButton.setAttribute("aria-label", `Copy PDF path for ${item.title || "paper"}`);
      copyButton.title = "Copy PDF path";
      button.addEventListener("click", event => {
        event.stopPropagation();
        openPaper(item, button);
      });
      copyButton.addEventListener("click", event => {
        event.stopPropagation();
        copyPaperPath(item, copyButton);
      });
      copyButton.addEventListener("dblclick", event => event.stopPropagation());
      article.addEventListener("dblclick", () => openPaper(item, button));
    } else {
      sourceState.textContent = item.source_count ? "No PDF" : "Missing";
      sourceState.classList.add("missing");
      article.classList.add("no-pdf");
      button.disabled = true;
      copyButton.disabled = true;
    }
    article.addEventListener("click", () => selectPaper(article, item));
    article.addEventListener("focus", () => selectPaper(article, item));
    article.addEventListener("keydown", event => {
      if (event.key === "Enter" && item.pdf_count) openPaper(item, button);
    });
    papers.append(fragment);
  }
}

function updateCounts() {
  const shown = state.items.length;
  const noun = state.total === 1 ? "paper" : "papers";
  document.querySelector("#nav-count").textContent = state.total.toLocaleString();
  resultCount.textContent = shown < state.total
    ? `Showing ${shown.toLocaleString()} of ${state.total.toLocaleString()} ${noun}`
    : `${state.total.toLocaleString()} ${noun}`;
  visibleCount.textContent = `${shown.toLocaleString()} of ${state.total.toLocaleString()} shown`;
  listFooter.hidden = shown === 0 || shown >= state.total;
}

async function loadPapers({ append = false } = {}) {
  papersController?.abort();
  papersController = new AbortController();
  const signal = papersController.signal;
  const currentRequest = ++requestNumber;
  const offset = append ? state.items.length : 0;
  papers.hidden = false;
  empty.hidden = true;
  if (!append) {
    listFooter.hidden = true;
    papers.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  } else {
    showMore.disabled = true;
    showMore.textContent = "Loading…";
  }
  try {
    const result = await operation("work.query", {
      query: state.query,
      availability: state.availability,
      sort: state.sort,
      limit: PAGE_SIZE,
      offset,
      collection_id: state.collectionId,
    }, signal);
    if (currentRequest !== requestNumber) return;
    state.total = result.total;
    if (result.items.length) {
      state.items = append ? [...state.items, ...result.items] : result.items;
      render(result.items, append);
      updateCounts();
    } else if (append) {
      updateCounts();
    } else {
      state.items = [];
      papers.hidden = true;
      listFooter.hidden = true;
      empty.hidden = false;
      updateCounts();
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    if (currentRequest !== requestNumber) return;
    if (append) {
      showToast(error.message);
      listFooter.hidden = false;
      return;
    }
    state.items = [];
    state.total = 0;
    papers.hidden = true;
    listFooter.hidden = true;
    empty.hidden = false;
    empty.querySelector("h2").textContent = "Library unavailable";
    empty.querySelector("p").textContent = error.message;
    resultCount.textContent = "Unable to load papers";
  } finally {
    if (currentRequest === requestNumber) {
      showMore.disabled = false;
      showMore.textContent = "Show 100 More";
    }
  }
}

async function initialize() {
  const [status] = await Promise.all([
    operation("library.status", {}),
    loadCollections(),
  ]);
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
    setActiveNavigation(button);
    document.querySelector("#page-title").textContent =
      button.dataset.filter === "all" ? "All Papers" :
      button.dataset.filter === "local_pdf" ? "Downloaded" : "Needs Source";
    state.availability = button.dataset.filter;
    state.collectionId = null;
    loadPapers();
  });
});
document.querySelector("[data-recent]").addEventListener("click", event => {
  setActiveNavigation(event.currentTarget);
  document.querySelector("#page-title").textContent = "Recently Added";
  state.availability = "all";
  state.collectionId = null;
  state.sort = "recent";
  document.querySelector("#sort").value = "recent";
  loadPapers();
});
document.querySelector("#clear-search").addEventListener("click", () => {
  search.value = "";
  state.query = "";
  state.availability = "all";
  state.collectionId = null;
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.filter === "all"));
  document.querySelector("#page-title").textContent = "All Papers";
  loadPapers();
});
document.querySelector("#new-collection").addEventListener("click", () => {
  document.querySelector("#collection-title").value = "";
  newCollectionDialog.showModal();
  window.setTimeout(() => document.querySelector("#collection-title").focus(), 0);
});
document.querySelector("#new-collection-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (event.submitter?.value === "cancel") {
    newCollectionDialog.close();
    return;
  }
  const input = document.querySelector("#collection-title");
  const title = input.value.trim();
  if (!title) return;
  const base = title.toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "collection";
  const ids = new Set(state.collections.map(entry => entry.collection.id));
  let collectionId = base;
  let suffix = 2;
  while (ids.has(collectionId)) collectionId = `${base}-${suffix++}`;
  const createButton = document.querySelector("#create-collection");
  createButton.disabled = true;
  try {
    await operation("collection.create", { collection_id: collectionId, title });
    await loadCollections();
    newCollectionDialog.close();
    const created = state.collections.find(entry => entry.collection.id === collectionId);
    if (created) selectCollection(created.collection);
    showToast(`Created ${title}`);
  } catch (error) {
    showToast(error.message);
  } finally {
    createButton.disabled = false;
  }
});
showMore.addEventListener("click", () => loadPapers({ append: true }));
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
