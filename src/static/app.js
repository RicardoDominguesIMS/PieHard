const searchForm = document.querySelector("#search-form");
const queryInput = document.querySelector("#recipe-query");
const clarification = document.querySelector("#clarification");
const clarificationForm = document.querySelector("#clarification-form");
const indexButton = document.querySelector("#index-button");
const notice = document.querySelector("#notice");
const recipeResult = document.querySelector("#recipe-result");
let activeSessionId = null;

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request could not be completed.");
  return data;
}

function setNotice(message, kind = "info") {
  notice.textContent = message;
  notice.className = `notice notice-${kind}`;
  notice.hidden = !message;
}

function setBusy(form, busy, buttonLabel) {
  const button = form.querySelector("button[type='submit']");
  button.disabled = busy;
  if (buttonLabel) button.querySelector(".button-label").textContent = buttonLabel;
}

function clearResult() {
  recipeResult.hidden = true;
  clarification.hidden = true;
}

function renderResult(result) {
  clearResult();
  setNotice("");
  if (result.status === "clarification") {
    activeSessionId = result.session_id;
    document.querySelector("#question-title").textContent = result.question;
    document.querySelector("#clarification-number").textContent =
      `· ${result.clarifications_asked} of 2`;
    clarification.hidden = false;
    document.querySelector("#clarification-answer").focus();
    return;
  }
  if (result.status === "no_match") {
    setNotice(result.message, "info");
    return;
  }
  if (result.status !== "selected" || !result.recipe) {
    throw new Error("The recipe service returned an invalid result.");
  }
  displayRecipe(result.recipe, result.reason);
}

function displayRecipe(recipe, reason) {
  const servings = recipe.servings;
  document.querySelector("#recipe-name").textContent = recipe.name;
  document.querySelector("#match-reason").textContent = reason || "";

  const difficulty = recipe.difficulty || "Unknown";
  const badge = document.querySelector("#difficulty-badge");
  badge.textContent = difficulty;
  badge.className = `difficulty-badge difficulty-${difficulty.toLowerCase()}`;

  const servingsText = servings ? `${servings} servings` : "Not specified";
  const servingNumber = /^\s*\d+\s*$/.test(servings || "") ? Number(servings) : Number.NaN;
  const forkCount = Number.isInteger(servingNumber) && servingNumber > 0 && servingNumber <= 40
    ? " 🍴".repeat(servingNumber)
    : (Number.isInteger(servingNumber) && servingNumber > 40 ? `🍴 × ${servingNumber}` : "");
  document.querySelector("#servings-count").textContent = `${forkCount} ${servingsText}`.trim();

  const utensils = Array.isArray(recipe.utensils) ? recipe.utensils : [];
  document.querySelector("#utensils-summary").textContent =
    utensils.length ? utensils.map((item) => item.name).join(", ") : "Not specified";

  const ingredientList = document.querySelector("#ingredient-list");
  ingredientList.replaceChildren();
  (recipe.ingredients || []).forEach((item) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    const recognized = item.recognition_status === "recognized";
    name.textContent = `${item.name || "Unknown ingredient"}${recognized ? "" : " (needs review)"}`;
    li.append(name);
    if (item.quantity) {
      const quantity = document.createElement("span");
      quantity.className = "ingredient-quantity";
      quantity.textContent = item.quantity;
      li.append(quantity);
    }
    ingredientList.append(li);
  });

  document.querySelector("#instructions-text").textContent =
    recipe.instructions || "Preparation instructions are not available in the source document.";

  const allergens = Array.isArray(recipe.allergens) ? recipe.allergens : [];
  const ingredients = Array.isArray(recipe.ingredients) ? recipe.ingredients : [];
  const unrecognizedIngredients = ingredients
    .filter((item) => item.recognition_status !== "recognized")
    .map((item) => item.recognition_note
      ? `${item.name || "Unnamed ingredient"} (${item.recognition_note})`
      : (item.name || "Unnamed ingredient"));
  const allergenText = allergens.length
    ? allergens.map((item) => item.name).join(", ")
    : "No listed allergens were identified from the recognized ingredients.";
  document.querySelector("#allergen-list").textContent = allergenText;
  const needsReview = recipe.allergen_status !== "assessed";
  document.querySelector("#allergen-caveat").textContent = needsReview
    ? `Allergen identification needs review because ${unrecognizedIngredients.length
      ? `these ingredients were not recognized: ${unrecognizedIngredients.join(", ")}`
      : "the ingredient list is unavailable"}.`
    : "Allergens are identified from the listed ingredients only; product-specific details and cross-contamination are not included.";
  document.querySelector("#source-reference").textContent = `Source: ${recipe.source}`;
  recipeResult.hidden = false;
  recipeResult.scrollIntoView({ behavior: "smooth", block: "start" });
}

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearResult();
  setNotice("");
  setBusy(searchForm, true, "Searching…");
  try {
    renderResult(await postJson("/api/search", { query: queryInput.value }));
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(searchForm, false, "Find my recipe");
  }
});

clarificationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setBusy(clarificationForm, true, "Checking…");
  try {
    const answer = document.querySelector("#clarification-answer").value;
    renderResult(await postJson("/api/clarify", {
      session_id: activeSessionId,
      answer,
    }));
    clarificationForm.reset();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(clarificationForm, false, "Continue");
  }
});

indexButton?.addEventListener("click", async () => {
  indexButton.disabled = true;
  indexButton.textContent = "Refreshing…";
  setNotice("Reading recipe documents and evaluating changed files. This may take a moment.");
  try {
    const result = await postJson("/api/index", {});
    const messages = [
      `Recipe library refreshed: ${result.indexed} recipes indexed, ${result.unchanged} unchanged.`,
    ];
    if (result.errors.length) messages.push(`${result.errors.length} source document(s) could not be processed.`);
    setNotice(messages.join(" "), result.errors.length ? "error" : "success");
    await updateLibraryCount();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    indexButton.disabled = false;
    indexButton.textContent = "Refresh recipe library";
  }
});

async function updateLibraryCount() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    document.querySelector("#library-count").textContent =
      `${data.recipe_count} ${data.recipe_count === 1 ? "recipe" : "recipes"} in the collection`;
  } catch {
    document.querySelector("#library-count").textContent = "Recipe collection status unavailable.";
  }
}

updateLibraryCount();
