"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const DEBUG_MODE = new URLSearchParams(window.location.search).get("debug") === "true";
const loadingStages = [
  "Checking available carriers",
  "Comparing current freight rates",
  "Preparing your best options"
];

const quoteState = {
  marketOptions: [],
  sort: "price",
  selectedQuote: null,
  lastAction: "",
  lastPayload: null,
  contact: { whatsapp: "", phone: "", email: "" }
};

const quoteLocations = window.LodestarLocationResolver.attach([
  { inputId: "originLocation", zipId: "originZip", cityId: "originCity", stateId: "originState", optionsId: "originLocationOptions", errorId: "originLocationError", label: "Origin" },
  { inputId: "destinationLocation", zipId: "destinationZip", cityId: "destinationCity", stateId: "destinationState", optionsId: "destinationLocationOptions", errorId: "destinationLocationError", label: "Destination" }
]);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function compact(object) {
  return Object.fromEntries(Object.entries(object).filter(([, value]) => {
    if (value === undefined || value === null || value === "") return false;
    if (Array.isArray(value)) return value.length > 0;
    return true;
  }));
}

function futureDate(days = 1) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function selectedAction() {
  return $('input[name="quoteType"]:checked')?.value || "";
}

function checkedServices(name) {
  return $$(`input[name="${name}"]:checked`).map((input) => input.value);
}

function numberFrom(selector) {
  const number = Number($(selector).value);
  return Number.isFinite(number) ? number : undefined;
}

function clearValidation(input) {
  input.classList.remove("is-invalid");
  input.closest(".form-field")?.classList.remove("has-error");
}

function validateInput(input) {
  clearValidation(input);
  if (input.checkValidity()) return true;
  input.classList.add("is-invalid");
  input.closest(".form-field")?.classList.add("has-error");
  return false;
}

function clearFreightTypeValidation() {
  const fieldset = $(".freight-fieldset");
  fieldset.classList.remove("has-error");
  fieldset.removeAttribute("aria-invalid");
}

function validateFreightType() {
  clearFreightTypeValidation();
  if (selectedAction()) return true;
  const fieldset = $(".freight-fieldset");
  fieldset.classList.add("has-error");
  fieldset.setAttribute("aria-invalid", "true");
  return false;
}

function validateForm() {
  const requiredInputs = $$("#quoteForm input[required]:not([name=\"quoteType\"]), #quoteForm select[required]");
  const inputResults = requiredInputs.map(validateInput);
  const freightTypeIsValid = validateFreightType();
  const isValid = inputResults.every(Boolean) && freightTypeIsValid;
  if (!isValid) {
    const invalidInput = requiredInputs.find((input) => !input.checkValidity());
    (invalidInput || $('input[name="quoteType"]'))?.focus();
  }
  return isValid;
}

function updateFreightType() {
  const action = selectedAction();
  const mode = action === "quote-ltl-market" ? "LTL" : action === "quote-ftl" ? "FTL" : "";
  $("#loadDetails").classList.add("is-active");
  $("#loadDetails").dataset.mode = mode;
}

function buildPayload(action) {
  const pickup = checkedServices("pickupService");
  const delivery = checkedServices("deliveryService");
  const base = {
    origin_zip: $("#originZip").value.trim(),
    destination_zip: $("#destinationZip").value.trim(),
    pickup_date: $("#pickupDate").value,
    pallets: numberFrom("#pallets"),
    total_weight_lbs: numberFrom("#totalWeight"),
    length_in: numberFrom("#length"),
    width_in: numberFrom("#width"),
    height_in: numberFrom("#height"),
    freight_class: $("#freightClass").value,
    commodity: $("#commodity").value.trim()
  };

  if (action === "quote-ltl-market") {
    return {
      ...base,
      pickup_services: pickup,
      delivery_services: delivery
    };
  }

  return compact({
    ...base,
    accessorials: pickup.length || delivery.length ? { pickup, delivery } : undefined,
    hazmat: $("#hazmat").checked || undefined,
    stackable: $("#stackable").checked || undefined
  });
}

function showDebug(payload) {
  if (!DEBUG_MODE) return;
  $("#debugPanel").hidden = false;
  $("#debugOutput").textContent = JSON.stringify(payload, null, 2);
}

function renderLoading(isLtl) {
  $("#resultsSection").hidden = false;
  $("#quoteResult").innerHTML = `
    <div class="loading-card" role="status" aria-live="polite">
      <div class="loader" aria-hidden="true"><span>LS</span></div>
      <h2 id="loadingTitle">${isLtl ? loadingStages[0] : "Preparing your freight quote"}</h2>
      <p>${isLtl ? "This may take a few seconds while we contact multiple carrier networks." : "We are calculating the current network rate for your shipment."}</p>
      ${isLtl ? `<div class="loading-progress" aria-hidden="true">${loadingStages.map((stage, index) => `<span class="${index === 0 ? "is-active" : ""}">${escapeHtml(stage)}</span>`).join("")}</div>` : ""}
    </div>`;
  $("#resultsSection").scrollIntoView({ behavior: "smooth", block: "start" });

  if (!isLtl) return null;
  let index = 0;
  return window.setInterval(() => {
    index = Math.min(index + 1, loadingStages.length - 1);
    const heading = $("#loadingTitle");
    if (heading) heading.textContent = loadingStages[index];
    $$(".loading-progress span").forEach((item, itemIndex) => item.classList.toggle("is-active", itemIndex === index));
  }, 7000);
}

function safeCustomerNote(note) {
  const text = String(note || "").trim();
  if (!text) return "";
  if (/sandbox|json|endpoint|sign in|account|credential|api.?key|rate limit|debug|traceback/i.test(text)) {
    return "The carrier network did not return a current rate for this shipment.";
  }
  return text;
}

function friendlyError(result, status) {
  const detail = result?.detail ?? result?.data?.detail ?? result?.data?.message ?? result?.data?.error;
  if (status === 504) return "The carrier search timed out. Please try again.";
  if (status === 502 || /network|connection|traceback/i.test(String(detail ?? ""))) {
    return "The quote service is temporarily unavailable. Please try again shortly.";
  }
  if (status === 400) {
    if (/missing|field|dimension|length|width|height|ltl/i.test(String(detail ?? ""))) {
      return "Please complete the ZIP codes, pickup date, pallet count, weight, and dimensions.";
    }
    return "One or more shipment details are invalid. Please review the form and try again.";
  }
  return "We could not retrieve freight rates. Please review your shipment details and try again.";
}

function renderMessage(type, title, message, note = "") {
  $("#quoteResult").innerHTML = `
    <div class="message-card ${type === "empty" ? "is-empty" : "is-error"}" role="alert">
      <span class="message-symbol" aria-hidden="true">${type === "empty" ? "–" : "!"}</span>
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(message)}</p>
      ${note ? `<p class="message-note">${escapeHtml(note)}</p>` : ""}
      <button class="secondary-button" type="button" data-retry>Review Shipment Details</button>
    </div>`;
}

async function fetchQuote(action, payload) {
  const isLtl = action === "quote-ltl-market";
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 60_000);
  const loadingTimer = renderLoading(isLtl);

  try {
    const response = await fetch(`/api/warp/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal
    });
    const result = await response.json().catch(() => ({}));
    showDebug(result);
    if (!response.ok || result.ok === false) {
      renderMessage("error", "Quote unavailable", friendlyError(result, result.status_code ?? response.status));
      return;
    }
    renderQuoteResult(action, result.data ?? result);
  } catch (error) {
    if (error.name === "AbortError") {
      renderMessage("error", "Carrier search timed out", "The carrier search took longer than expected. Please try again.");
    } else {
      renderMessage("error", "Unable to connect", "The quote service is temporarily unavailable. Please try again shortly.");
    }
  } finally {
    window.clearTimeout(timeoutId);
    if (loadingTimer) window.clearInterval(loadingTimer);
  }
}

function numericPrice(option) {
  const value = Number(String(option?.price_usd ?? "").replaceAll("$", "").replaceAll(",", ""));
  return Number.isFinite(value) && String(option?.price_usd ?? "").trim() !== "" ? value : Number.POSITIVE_INFINITY;
}

function numericTransit(option) {
  const value = Number.parseFloat(String(option?.transit_days ?? ""));
  return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
}

function formatPrice(value) {
  const price = numericPrice({ price_usd: value });
  if (!Number.isFinite(price)) return "Rate unavailable";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2
  }).format(price);
}

function transitText(value) {
  if (value === undefined || value === null || value === "") return "Not provided";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return `${numeric} Business ${numeric === 1 ? "Day" : "Days"}`;
}

function monogram(name) {
  const letters = String(name || "C").trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join("");
  return escapeHtml((letters || "C").toUpperCase());
}

function sortedOptions() {
  const options = [...quoteState.marketOptions];
  if (quoteState.sort === "speed") {
    return options.sort((a, b) => numericTransit(a) - numericTransit(b) || numericPrice(a) - numericPrice(b));
  }
  if (quoteState.sort === "carrier") {
    return options.sort((a, b) => String(a.carrier_name ?? "").localeCompare(String(b.carrier_name ?? ""), "en"));
  }
  return options.sort((a, b) => numericPrice(a) - numericPrice(b));
}

function competitiveRateEstimate(options) {
  const validPrices = options.map(numericPrice).filter(Number.isFinite).sort((a, b) => a - b);
  const validQuoteCount = validPrices.length;
  if (validQuoteCount === 0) return null;
  const sampleCount = validQuoteCount <= 2
    ? validQuoteCount
    : Math.min(10, Math.max(3, Math.ceil(validQuoteCount / 3)));
  const selectedPrices = validPrices.slice(0, sampleCount);
  return {
    average: selectedPrices.reduce((sum, price) => sum + price, 0) / sampleCount,
    sampleCount,
    validQuoteCount
  };
}

function rateEstimateMarkup(estimate) {
  if (!estimate) return "";
  return `
    <aside class="rate-estimate" role="note" aria-label="Competitive Rate Estimate">
      <h3>Competitive Rate Estimate</h3>
      <strong class="rate-estimate-price">${escapeHtml(formatPrice(estimate.average))}</strong>
    </aside>`;
}

function optionTags(option, lowestPrice, shortestTransit) {
  const tags = [];
  if (numericPrice(option) === lowestPrice) tags.push('<span class="offer-tag offer-tag-best">Best Price</span>');
  if (numericTransit(option) === shortestTransit) tags.push('<span class="offer-tag offer-tag-fast">Fastest</span>');
  return tags.join("");
}

function renderCarrierCard(option, index, lowestPrice, shortestTransit) {
  const carrier = String(option.carrier_name || "Carrier unavailable");
  const isBest = numericPrice(option) === lowestPrice;
  return `
    <article class="offer-card ${isBest ? "is-best" : ""}" data-offer-index="${index}" data-network-source="${option.is_warp === true ? "direct" : "carrier"}">
      <div class="offer-tags">${optionTags(option, lowestPrice, shortestTransit)}</div>
      <div class="carrier-heading">
        <span class="carrier-avatar" aria-hidden="true">${monogram(carrier)}</span>
        <div><h3 title="${escapeHtml(carrier)}">${escapeHtml(carrier)}</h3><small>${escapeHtml(option.service_level || "Service level not provided")}</small></div>
      </div>
      <div class="offer-price">${escapeHtml(formatPrice(option.price_usd))}<small>USD</small></div>
      <dl class="offer-metrics">
        <div><dt>Estimated Transit</dt><dd>${escapeHtml(transitText(option.transit_days))}</dd></div>
      </dl>
      <button class="offer-select" type="button" data-select-offer="${index}">Select Quote</button>
    </article>`;
}

function renderMarketResults() {
  const options = sortedOptions();
  const rateEstimate = competitiveRateEstimate(quoteState.marketOptions);
  const prices = quoteState.marketOptions.map(numericPrice).filter(Number.isFinite);
  const transits = quoteState.marketOptions.map(numericTransit).filter(Number.isFinite);
  const lowestPrice = prices.length ? Math.min(...prices) : Number.NEGATIVE_INFINITY;
  const shortestTransit = transits.length ? Math.min(...transits) : Number.NEGATIVE_INFINITY;

  $("#quoteResult").innerHTML = `
    <div class="results-toolbar">
      <div><p class="section-kicker results-eyebrow">LIVE FREIGHT PRICING</p><h2>Available Carrier Quotes</h2><p class="results-count"><strong>${options.length}</strong> Carrier ${options.length === 1 ? "Option" : "Options"} Available</p></div>
      <label class="sort-box">SORT BY
        <select id="sortOffers" class="sort-select">
          <option value="price" ${quoteState.sort === "price" ? "selected" : ""}>Lowest Price</option>
          <option value="speed" ${quoteState.sort === "speed" ? "selected" : ""}>Fastest Transit</option>
          <option value="carrier" ${quoteState.sort === "carrier" ? "selected" : ""}>Carrier Name</option>
        </select>
      </label>
    </div>
    ${rateEstimateMarkup(rateEstimate)}
    <div class="offer-grid">${options.map((option, index) => renderCarrierCard(option, index, lowestPrice, shortestTransit)).join("")}</div>`;

  $("#sortOffers").addEventListener("change", (event) => {
    quoteState.sort = event.target.value;
    renderMarketResults();
  });
  $$('[data-select-offer]').forEach((button) => button.addEventListener("click", () => {
    openQuoteDialog(sortedOptions()[Number(button.dataset.selectOffer)]);
  }));
}

function firstNetworkResult(data) {
  if (Array.isArray(data?.results)) return data.results.find((item) => item && item.available !== false) || data.results[0] || {};
  if (data?.quote && typeof data.quote === "object") return data.quote;
  return data || {};
}

function firstValue(item, keys) {
  for (const key of keys) {
    const value = item?.[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function humanDate(value) {
  if (!value) return "Not provided";
  const text = String(value);
  const date = new Date(text.includes("T") ? text : `${text.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function renderNetworkResult(data) {
  const item = firstNetworkResult(data);
  const equipmentLabel = "53' Dry Van";
  const pickup = firstValue(item, ["pickup_date", "pickup_at"]) || $("#pickupDate").value;
  const delivery = firstValue(item, ["delivery_date", "estimated_delivery_date", "delivery_at"]);
  const expiration = firstValue(item, ["quote_expiration", "expires_at", "expiration", "valid_until"]);
  const quote = {
    ...item,
    request_token: data?.request_token || item?.request_token,
    public_mode: "ftl",
    carrier_name: firstValue(item, ["carrier_name", "carrier", "provider_name"]) || "Carrier to be confirmed",
    service_level: equipmentLabel,
    price_usd: firstValue(item, ["price_usd", "total_price_usd", "price", "total_price"]),
    transit_days: firstValue(item, ["transit_days", "estimated_transit_days"]),
    quote_id: firstValue(item, ["quote_id", "id"]),
    pickup_date: pickup,
    delivery_date: delivery,
    expires_at: expiration
  };

  $("#quoteResult").innerHTML = `
    <div class="results-toolbar">
      <div><p class="section-kicker results-eyebrow">FULL TRUCKLOAD RATE</p><h2>Your FTL Quote</h2></div>
    </div>
    <article class="network-offer ftl-offer">
      <div class="ftl-primary">
        <div><p class="ftl-label">EQUIPMENT</p><h3>${equipmentLabel}</h3></div>
        <div class="offer-price">${escapeHtml(formatPrice(quote.price_usd))}<small>USD</small></div>
      </div>
      <div class="network-metrics">
        <div><span>Estimated Transit</span><strong>${escapeHtml(transitText(quote.transit_days))}</strong></div>
        <div><span>Pickup Date</span><strong>${escapeHtml(humanDate(pickup))}</strong></div>
        <div><span>Delivery Date</span><strong>${escapeHtml(humanDate(delivery))}</strong></div>
      </div>
      <button id="selectNetwork" class="offer-select" type="button">Select Quote</button>
    </article>`;
  $("#selectNetwork").addEventListener("click", () => openQuoteDialog(quote));
}

function renderQuoteResult(action, data) {
  if (action === "quote-ltl-market") {
    const options = Array.isArray(data?.market_options) ? data.market_options : [];
    if (!options.length) {
      renderMessage(
        "empty",
        "No carrier rates are currently available for this route.",
        "Review the shipment details or try another pickup date.",
        safeCustomerNote(data?.note)
      );
      return;
    }
    quoteState.marketOptions = options;
    quoteState.sort = "price";
    renderMarketResults();
    return;
  }
  renderNetworkResult(data);
}

function summaryMarkup(quote) {
  if (quote.public_mode === "ftl") {
    return `
      <div><span>Equipment</span><strong>${escapeHtml(quote.service_level)}</strong></div>
      <div><span>Price</span><strong>${escapeHtml(formatPrice(quote.price_usd))} USD</strong></div>
      <div><span>Estimated Transit</span><strong>${escapeHtml(transitText(quote.transit_days))}</strong></div>
      <div><span>Pickup Date</span><strong>${escapeHtml(humanDate(quote.pickup_date))}</strong></div>
      <div><span>Delivery Date</span><strong>${escapeHtml(humanDate(quote.delivery_date))}</strong></div>`;
  }
  return `
    <div><span>Carrier</span><strong>${escapeHtml(quote.carrier_name || "Lodestar Logistics")}</strong></div>
    <div><span>Price</span><strong>${escapeHtml(formatPrice(quote.price_usd))} USD</strong></div>
    <div><span>Transit Time</span><strong>${escapeHtml(transitText(quote.transit_days))}</strong></div>
    <div><span>Service Level</span><strong>${escapeHtml(quote.service_level || "Not provided")}</strong></div>
    <div><span>Quote ID</span><strong>${escapeHtml(quote.quote_id || "Not provided")}</strong></div>`;
}

function openQuoteDialog(quote) {
  if (!quote) return;
  quoteState.selectedQuote = quote;
  $("#selectedQuoteSummary").innerHTML = summaryMarkup(quote);
  $("#quoteRequestForm").reset();
  $("#quoteRequestForm").hidden = false;
  $("#quoteRequestSuccess").hidden = true;
  $("#quoteRequestError").hidden = true;
  $("#quoteRequestError").textContent = "";
  const dialog = $("#quoteDialog");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

async function loadContact() {
  try {
    const response = await fetch("/api/public-config");
    if (!response.ok) return;
    quoteState.contact = await response.json();
  } catch {
    quoteState.contact = { whatsapp: "", phone: "", email: "" };
  }
}

$("#quoteForm").addEventListener("input", (event) => {
  if (event.target.matches("input, select")) clearValidation(event.target);
});

$$('input[name="quoteType"]').forEach((input) => input.addEventListener("change", () => {
  clearFreightTypeValidation();
  updateFreightType();
}));

$("#quoteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!await quoteLocations.resolveAll()) return;
  if (!validateForm()) return;
  const action = selectedAction();
  if (!action) return;
  const payload = buildPayload(action);
  quoteState.lastAction = action;
  quoteState.lastPayload = payload;
  const button = $("#submitQuote");
  button.disabled = true;
  button.innerHTML = "Finding Current Rates <span aria-hidden=\"true\">…</span>";
  await fetchQuote(action, payload);
  button.disabled = false;
  button.textContent = "Get My Quote";
});

$("#quoteResult").addEventListener("click", (event) => {
  if (event.target.closest("[data-retry]")) $("#originLocation").focus();
});

$("#quoteRequestForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const requestToken = quoteState.selectedQuote?.request_token;
  const error = $("#quoteRequestError");
  if (!requestToken) {
    error.textContent = "This quote can no longer be submitted. Please request a new quote.";
    error.hidden = false;
    return;
  }
  const button = $("#submitQuoteRequest");
  button.disabled = true;
  button.textContent = "Submitting…";
  error.hidden = true;
  try {
    const response = await fetch("/api/quote-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        request_token: requestToken,
        full_name: $("#requestFullName").value.trim(),
        email: $("#requestEmail").value.trim(),
        phone: $("#requestPhone").value.trim()
      })
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(String(result.detail || "Unable to submit this quote request."));
    form.hidden = true;
    $("#quoteRequestSuccess").hidden = false;
  } catch (requestError) {
    error.textContent = requestError.message;
    error.hidden = false;
    button.disabled = false;
    button.textContent = "Submit Quote Request";
  }
});

$(".dialog-close").addEventListener("click", () => $("#quoteDialog").close());
$("#quoteDialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

document.addEventListener("click", (event) => {
  const disabled = event.target.closest('[aria-disabled="true"]');
  if (disabled) event.preventDefault();
});

const servicesLink = $('a[href="#services"]');
servicesLink?.addEventListener("click", () => { $("#services").open = true; });

$("#pickupDate").min = new Date().toISOString().slice(0, 10);
$("#pickupDate").value = futureDate();
$("#year").textContent = new Date().getFullYear();
if (DEBUG_MODE) $("#debugPanel").hidden = false;
updateFreightType();
loadContact();
