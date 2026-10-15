(() => {
  const form = document.getElementById("chunk-form");
  const instruction = document.getElementById("chunk-instruction");
  const submit = document.getElementById("chunk-submit");
  const selectedLabel = document.getElementById("selected-chunk");
  const message = document.getElementById("backoffice-message");
  const rows = document.getElementById("chunk-rows");
  const chunks = new Map();
  let selectedId = null;
  let sortKey = null;
  let sortDirection = "asc";
  const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
  const DIFFICULTY_ORDER = { easy: 0, medium: 1, elaborate: 2 };

  function names(items) {
    return (items || []).map((item) => item.name).join(", ");
  }

  // Each accessor returns a number or a string; empty values always sort last.
  const SORT_VALUES = {
    select: (chunk) => (chunk.recipe_id === selectedId ? 0 : 1),
    source: (chunk) => chunk.page ?? chunk.source_path,
    name: (chunk) => chunk.metadata.name,
    difficulty: (chunk) => DIFFICULTY_ORDER[String(chunk.metadata.difficulty).toLowerCase()],
    servings: (chunk) => {
      const value = parseFloat(chunk.metadata.servings);
      return Number.isNaN(value) ? undefined : value;
    },
    ingredients: (chunk) => names(chunk.metadata.ingredients),
    allergens: (chunk) => names(chunk.metadata.allergens),
    utensils: (chunk) => names(chunk.metadata.utensils),
    status: (chunk) => (chunk.metadata.allergen_status === "needs_review" ? 0 : 1),
  };

  function compare(a, b) {
    const accessor = SORT_VALUES[sortKey];
    const first = accessor(a);
    const second = accessor(b);
    const firstEmpty = first === undefined || first === "";
    const secondEmpty = second === undefined || second === "";
    if (firstEmpty || secondEmpty) return firstEmpty === secondEmpty ? 0 : firstEmpty ? 1 : -1;
    const result =
      typeof first === "number" && typeof second === "number"
        ? first - second
        : collator.compare(String(first), String(second));
    return sortDirection === "asc" ? result : -result;
  }

  function render() {
    const ordered = [...chunks.values()];
    if (sortKey) ordered.sort(compare);
    rows.replaceChildren(...ordered.map(buildRow));
    if (selectedId) markSelected();
    document.querySelectorAll("th[data-sort]").forEach((th) => {
      const active = th.dataset.sort === sortKey;
      th.setAttribute("aria-sort", active ? (sortDirection === "asc" ? "ascending" : "descending") : "none");
      th.querySelector(".sort-indicator").textContent = active ? (sortDirection === "asc" ? " ▲" : " ▼") : "";
    });
  }

  document.querySelectorAll("th[data-sort] .sort-button").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.parentElement.dataset.sort;
      sortDirection = sortKey === key && sortDirection === "asc" ? "desc" : "asc";
      sortKey = key;
      render();
    });
  });

  function showMessage(text, kind) {
    message.textContent = text;
    message.className = `notice${kind ? ` notice-${kind}` : ""}`;
    message.hidden = false;
  }

  function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    return td;
  }

  function ingredientsCell(metadata) {
    const td = document.createElement("td");
    (metadata.ingredients || []).forEach((ingredient, position) => {
      if (position) td.append(", ");
      const span = document.createElement("span");
      const unknown = ingredient.recognition_status !== "recognized";
      span.textContent = unknown ? `${ingredient.name} (unknown)` : ingredient.name;
      if (unknown) {
        span.className = "ingredient-unknown";
        const icon = document.createElement("span");
        icon.className = "review-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "!";
        span.prepend(icon);
      }
      td.append(span);
    });
    if (!td.childNodes.length) td.textContent = "None";
    return td;
  }

  function buildRow(chunk) {
    const metadata = chunk.metadata;
    const tr = document.createElement("tr");
    tr.dataset.id = chunk.recipe_id;

    const selectCell = document.createElement("td");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "chunk";
    radio.value = chunk.recipe_id;
    radio.setAttribute("aria-label", `Select ${metadata.name}`);
    selectCell.append(radio);
    tr.append(selectCell);

    const source = chunk.page ? `Page ${chunk.page}` : chunk.source_path;
    const allergens = (metadata.allergens || [])
      .map((a) => `${a.name} (${(a.source_ingredients || []).join(", ")})`)
      .join("; ");
    const needsReview = metadata.allergen_status === "needs_review";

    tr.append(
      cell(source),
      cell(metadata.name),
      cell(metadata.difficulty),
      cell(String(metadata.servings)),
      ingredientsCell(metadata),
      cell(allergens || "None listed"),
      cell((metadata.utensils || []).map((u) => u.name).join(", ") || "None"),
      cell(needsReview ? "Needs review" : "Assessed", needsReview ? "status-review" : "status-ok")
    );
    tr.addEventListener("click", () => select(chunk.recipe_id));
    return tr;
  }

  function markSelected() {
    rows.querySelectorAll("tr").forEach((tr) => {
      const active = tr.dataset.id === selectedId;
      tr.classList.toggle("is-selected", active);
      tr.querySelector("input").checked = active;
    });
  }

  function select(id) {
    selectedId = id;
    markSelected();
    const chunk = chunks.get(id);
    selectedLabel.textContent = `Selected: ${chunk.metadata.name}`;
    submit.disabled = false;
    instruction.focus();
  }

  async function loadChunks() {
    const response = await fetch("/api/chunks");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load chunks.");
    chunks.clear();
    data.chunks.forEach((chunk) => chunks.set(chunk.recipe_id, chunk));
    render();
    if (!data.chunks.length) showMessage("No chunks exist yet. Index the recipe library first.");
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = instruction.value.trim();
    if (!selectedId) return showMessage("Select a chunk row first.", "error");
    if (!text) return showMessage("Please describe the change you want to make.", "error");

    submit.disabled = true;
    showMessage("Updating the chunk…");
    try {
      const response = await fetch(`/api/chunks/${encodeURIComponent(selectedId)}/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: text }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "The chunk update failed.");
      chunks.set(data.chunk.recipe_id, data.chunk);
      render();
      select(selectedId);
      if (data.updated) {
        rows.querySelector(`tr[data-id="${CSS.escape(selectedId)}"]`).classList.add("is-updated");
        instruction.value = "";
      }
      showMessage(data.updated ? data.summary : `No changes made. ${data.summary}`, data.updated ? "success" : "");
    } catch (error) {
      showMessage(error.message, "error");
    } finally {
      submit.disabled = !selectedId;
    }
  });

  loadChunks().catch((error) => showMessage(error.message, "error"));
})();
