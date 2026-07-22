const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];

const quoteState = {
  step: 1,
  action: "quote-ltl-market",
  marketOptions: [],
  sort: "price",
  contact: { whatsapp: "", phone: "", email: "" }
};

const transportNames = {
  "quote-ltl-market": "LTL",
  "quote-ftl": "FTL",
  "quote-box": "Box Truck",
  "quote-van": "Cargo Van"
};

const loadingStages = [
  "Taşıyıcılar sorgulanıyor",
  "Güncel fiyatlar karşılaştırılıyor",
  "En uygun teklifler hazırlanıyor"
];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function futureDate(days = 7) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function selectedAction() {
  return $('input[name="quoteType"]:checked').value;
}

function numberFrom(selector) {
  const value = $(selector).value;
  return value === "" ? undefined : Number(value);
}

function checkedServices(name) {
  return $$(`input[name="${name}"]:checked`).map((input) => input.value);
}

function compact(object) {
  return Object.fromEntries(Object.entries(object).filter(([, value]) => {
    if (value === undefined || value === null || value === "") return false;
    if (Array.isArray(value) && value.length === 0) return false;
    return true;
  }));
}

function updateDimensionRules() {
  quoteState.action = selectedAction();
  const ltlSelected = quoteState.action === "quote-ltl-market";
  ["#length", "#width", "#height"].forEach((selector) => {
    $(selector).required = ltlSelected;
  });
}

function setStep(step) {
  quoteState.step = step;
  $$(".form-panel").forEach((panel) => {
    const active = Number(panel.dataset.panel) === step;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  $$("[data-progress]").forEach((item) => {
    const itemStep = Number(item.dataset.progress);
    item.classList.toggle("is-active", itemStep === step);
    item.classList.toggle("is-complete", itemStep < step);
    if (itemStep === step) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
    if (itemStep < step) item.querySelector(".progress-number").textContent = "✓";
    else item.querySelector(".progress-number").textContent = String(itemStep);
  });
  $$(".progress-divider").forEach((divider, index) => {
    divider.classList.toggle("is-complete", index + 1 < step);
  });
  $("#quoteCard").scrollIntoView({ behavior: "smooth", block: "start" });
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

function validatePanel(step) {
  updateDimensionRules();
  const panel = $(`.form-panel[data-panel="${step}"]`);
  const requiredInputs = $$('input:not([type="radio"]):not([type="checkbox"]), select', panel)
    .filter((input) => input.required);
  const valid = requiredInputs.map(validateInput).every(Boolean);
  if (!valid) requiredInputs.find((input) => !input.checkValidity())?.focus();
  return valid;
}

function buildPayload(action) {
  const pickup = checkedServices("pickupService");
  const delivery = checkedServices("deliveryService");
  const base = {
    origin_zip: $("#originZip").value.trim(),
    destination_zip: $("#destinationZip").value.trim(),
    pickup_date: $("#pickupDate").value,
    pallets: numberFrom("#pallets"),
    weight_lbs_per_pallet: numberFrom("#weight")
  };

  if (action === "quote-ltl-market") {
    return {
      ...base,
      length_in: numberFrom("#length"),
      width_in: numberFrom("#width"),
      height_in: numberFrom("#height"),
      pickup_services: pickup,
      delivery_services: delivery
    };
  }

  return compact({
    ...base,
    accessorials: pickup.length || delivery.length ? { pickup, delivery } : undefined,
    commodity: $("#commodity").value.trim(),
    freight_class: $("#freightClass").value,
    hazmat: $("#hazmat").checked || undefined
  });
}

function renderLoading(isLtl) {
  $("#resultsSection").hidden = false;
  $("#quoteResult").innerHTML = `
    <div class="loading-card" role="status" aria-live="polite">
      <div class="loader" aria-hidden="true"><span></span><i>LS</i></div>
      <h2 id="loadingTitle">${isLtl ? loadingStages[0] : "Teklifiniz hazırlanıyor"}</h2>
      <p>${isLtl ? "Birden fazla taşıyıcıdan güncel fiyat toplandığı için bu işlem kısa bir süre alabilir." : "Gönderiniz için güncel taşıma fiyatı hesaplanıyor."}</p>
      ${isLtl ? `<div class="loading-progress" aria-hidden="true">${loadingStages.map((stage, index) => `<span class="${index === 0 ? "is-active" : ""}">${escapeHtml(stage)}</span>`).join("")}</div>` : ""}
    </div>`;
  $("#resultsSection").scrollIntoView({ behavior: "smooth", block: "start" });

  if (!isLtl) return null;
  let index = 0;
  return window.setInterval(() => {
    index = Math.min(index + 1, loadingStages.length - 1);
    $("#loadingTitle").textContent = loadingStages[index];
    $$(".loading-progress span").forEach((item, itemIndex) => item.classList.toggle("is-active", itemIndex === index));
  }, 7000);
}

function safeCustomerNote(note) {
  const text = String(note || "").trim();
  if (!text) return "";
  if (/sandbox|json|endpoint|sign in|account|credential|key|rate limit|debug/i.test(text)) {
    return "Taşıyıcı ağı bu sorgu için firma bazlı güncel bir seçenek oluşturamadı.";
  }
  return text;
}

function friendlyError(result, status) {
  const detail = result?.detail ?? result?.data?.detail ?? result?.data?.message ?? result?.data?.error;
  if (status === 504) return "Taşıyıcı sorgusu zaman aşımına uğradı. Lütfen tekrar deneyin.";
  if (status === 502 || /network|connection|traceback/i.test(String(detail ?? ""))) {
    return "Teklif hizmetine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.";
  }
  if (status === 400) {
    if (/missing|field|dimension|length|width|height|ltl/i.test(String(detail ?? ""))) {
      return "Eksik veya geçersiz alanlar var. LTL teklifleri için ZIP kodları, tarih, palet, ağırlık ve tüm ölçüler zorunludur.";
    }
    return "Gönderi bilgilerinden biri eksik veya geçersiz. Alanları kontrol ederek yeniden deneyin.";
  }
  return "Teklifler şu anda alınamadı. Bilgileri kontrol ederek yeniden deneyin.";
}

function renderMessage(type, title, message, note = "") {
  $("#quoteResult").innerHTML = `
    <div class="message-card ${type === "empty" ? "is-empty" : "is-error"}" role="alert">
      <span class="message-symbol" aria-hidden="true">${type === "empty" ? "–" : "!"}</span>
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(message)}</p>
      ${note ? `<p class="message-note">${escapeHtml(note)}</p>` : ""}
      <button class="secondary-button" type="button" data-retry>Bilgileri Kontrol Et</button>
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
    if (!response.ok || result.ok === false) {
      renderMessage("error", "Teklif alınamadı", friendlyError(result, result.status_code ?? response.status));
      return;
    }
    renderQuoteResult(action, result.data ?? result);
  } catch (error) {
    if (error.name === "AbortError") {
      renderMessage("error", "Sorgu zaman aşımına uğradı", "Taşıyıcı sorgusu zaman aşımına uğradı. Lütfen tekrar deneyin.");
    } else {
      renderMessage("error", "Bağlantı kurulamadı", "Teklif hizmetine şu anda ulaşılamıyor. Lütfen kısa bir süre sonra tekrar deneyin.");
    }
  } finally {
    window.clearTimeout(timeoutId);
    if (loadingTimer) window.clearInterval(loadingTimer);
  }
}

function numericPrice(option) {
  const value = Number(String(option?.price_usd ?? "").replaceAll("$", "").replaceAll(",", ""));
  return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
}

function numericTransit(option) {
  const value = Number.parseFloat(String(option?.transit_days ?? ""));
  return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
}

function formatPrice(value) {
  const price = numericPrice({ price_usd: value });
  if (!Number.isFinite(price)) return "Fiyat paylaşılmadı";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2
  }).format(price);
}

function transitText(value) {
  if (value === undefined || value === null || value === "") return "Paylaşılmadı";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric} iş günü` : String(value);
}

function monogram(name) {
  const letters = String(name || "T").trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join("");
  return escapeHtml((letters || "T").toUpperCase());
}

function sortedOptions() {
  const options = [...quoteState.marketOptions];
  if (quoteState.sort === "speed") {
    return options.sort((a, b) => numericTransit(a) - numericTransit(b) || numericPrice(a) - numericPrice(b));
  }
  if (quoteState.sort === "carrier") {
    return options.sort((a, b) => String(a.carrier_name ?? "").localeCompare(String(b.carrier_name ?? ""), "tr"));
  }
  return options.sort((a, b) => numericPrice(a) - numericPrice(b));
}

function optionTags(option, lowestPrice, shortestTransit) {
  const tags = [];
  if (numericPrice(option) === lowestPrice) tags.push('<span class="offer-tag offer-tag-best">En Uygun</span>');
  if (numericTransit(option) === shortestTransit) tags.push('<span class="offer-tag offer-tag-fast">En Hızlı</span>');
  return tags.join("");
}

function renderCarrierCard(option, index, lowestPrice, shortestTransit) {
  const carrier = option.carrier_name || "Taşıyıcı bilgisi paylaşılmadı";
  const isBest = numericPrice(option) === lowestPrice;
  return `
    <article class="offer-card ${isBest ? "is-best" : ""}" data-offer-index="${index}">
      <div class="offer-tags">${optionTags(option, lowestPrice, shortestTransit)}</div>
      <div class="carrier-heading">
        <span class="carrier-avatar" aria-hidden="true">${monogram(carrier)}</span>
        <div><h3 title="${escapeHtml(carrier)}">${escapeHtml(carrier)}</h3><small>${escapeHtml(option.service_level || "Hizmet seviyesi paylaşılmadı")}</small></div>
      </div>
      <div class="offer-price">${escapeHtml(formatPrice(option.price_usd))}<small>USD</small></div>
      <dl class="offer-metrics">
        <div><dt>Tahmini transit</dt><dd>${escapeHtml(transitText(option.transit_days))}</dd></div>
        <div><dt>Durum</dt><dd class="${option.bookable ? "status-ready" : "status-info"}">${option.bookable ? "Rezervasyona Uygun" : "Sadece Fiyat Bilgisi"}</dd></div>
      </dl>
      <div class="quote-reference"><span>Teklif referansı</span><code title="${escapeHtml(option.quote_id || "Paylaşılmadı")}">${escapeHtml(option.quote_id || "Paylaşılmadı")}</code></div>
      <button class="offer-select" type="button" data-select-offer="${index}">Teklifi Seç</button>
    </article>`;
}

function renderMarketResults() {
  const options = sortedOptions();
  const prices = quoteState.marketOptions.map(numericPrice).filter(Number.isFinite);
  const transits = quoteState.marketOptions.map(numericTransit).filter(Number.isFinite);
  const lowestPrice = prices.length ? Math.min(...prices) : Number.NEGATIVE_INFINITY;
  const shortestTransit = transits.length ? Math.min(...transits) : Number.NEGATIVE_INFINITY;

  $("#quoteResult").innerHTML = `
    <div class="results-toolbar">
      <div><span class="section-label">GÜNCEL TEKLİFLER</span><h2>${options.length} taşıyıcı teklifi bulundu</h2><p>Tüm seçenekler güncel taşıyıcı yanıtlarına göre listelenmiştir.</p></div>
      <label class="sort-box">SIRALAMA
        <select id="sortOffers" class="sort-select">
          <option value="price" ${quoteState.sort === "price" ? "selected" : ""}>En düşük fiyat</option>
          <option value="speed" ${quoteState.sort === "speed" ? "selected" : ""}>En hızlı teslimat</option>
          <option value="carrier" ${quoteState.sort === "carrier" ? "selected" : ""}>Taşıyıcı adı</option>
        </select>
      </label>
    </div>
    <div class="offer-grid">${options.map((option, index) => renderCarrierCard(option, index, lowestPrice, shortestTransit)).join("")}</div>`;

  $("#sortOffers").addEventListener("change", (event) => {
    quoteState.sort = event.target.value;
    renderMarketResults();
  });
  $$('[data-select-offer]').forEach((button) => button.addEventListener("click", () => {
    openQuoteDialog(sortedOptions()[Number(button.dataset.selectOffer)], "LTL");
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
  if (!value) return "Paylaşılmadı";
  const date = new Date(`${String(value).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("tr-TR", { day: "2-digit", month: "short", year: "numeric" }).format(date);
}

function includedServices(item) {
  const raw = firstValue(item, ["included_services", "services_included", "services", "accessorials"]);
  if (Array.isArray(raw)) return raw.map((value) => typeof value === "object" ? value.name || value.label : value).filter(Boolean);
  if (raw && typeof raw === "object") return Object.entries(raw).filter(([, value]) => Boolean(value)).map(([key]) => key.replaceAll("_", " "));
  if (typeof raw === "string") return [raw];
  return [];
}

function renderNetworkResult(action, data) {
  const item = firstNetworkResult(data);
  const vehicle = firstValue(item, ["vehicle_type", "equipment_type", "mode"]) || transportNames[action];
  const services = includedServices(item);
  const quote = {
    ...item,
    carrier_name: "WARP Network",
    service_level: vehicle,
    price_usd: firstValue(item, ["price_usd", "total_price_usd"]),
    transit_days: firstValue(item, ["transit_days", "estimated_transit_days"]),
    quote_id: firstValue(item, ["quote_id", "id"])
  };
  const pickup = firstValue(item, ["pickup_date", "pickup_at"]) || $("#pickupDate").value;
  const delivery = firstValue(item, ["delivery_date", "estimated_delivery_date", "delivery_at"]);

  $("#quoteResult").innerHTML = `
    <div class="results-toolbar">
      <div><span class="section-label">TAŞIMA TEKLİFİ</span><h2>Teklifiniz hazır</h2><p>${escapeHtml(transportNames[action])} gönderiniz için güncel ağ fiyatı.</p></div>
    </div>
    <article class="network-offer">
      <div class="network-primary">
        <div class="carrier-heading"><span class="carrier-avatar" aria-hidden="true">WN</span><div><h3>WARP Network</h3><small>${escapeHtml(vehicle)}</small></div></div>
        <div class="offer-price">${escapeHtml(formatPrice(quote.price_usd))}<small>USD</small></div>
        <ul class="service-chips">${services.length ? services.map((service) => `<li>${escapeHtml(service)}</li>`).join("") : "<li>Dahil olan hizmetler paylaşılmadı</li>"}</ul>
      </div>
      <div class="network-metrics">
        <div><span>Araç tipi</span><strong>${escapeHtml(vehicle)}</strong></div>
        <div><span>Tahmini transit</span><strong>${escapeHtml(transitText(quote.transit_days))}</strong></div>
        <div><span>Pickup tarihi</span><strong>${escapeHtml(humanDate(pickup))}</strong></div>
        <div><span>Delivery tarihi</span><strong>${escapeHtml(humanDate(delivery))}</strong></div>
      </div>
      <button id="selectNetwork" class="offer-select" type="button">Teklifi Seç</button>
    </article>`;
  $("#selectNetwork").addEventListener("click", () => openQuoteDialog(quote, transportNames[action]));
}

function renderQuoteResult(action, data) {
  if (action === "quote-ltl-market") {
    const options = Array.isArray(data?.market_options) ? data.market_options : [];
    if (!options.length) {
      renderMessage(
        "empty",
        "Bu rota için teklif bulunamadı",
        "Bu rota için şu anda taşıyıcı teklifi bulunamadı. Bilgileri kontrol ederek yeniden deneyin.",
        safeCustomerNote(data?.note)
      );
      return;
    }
    quoteState.marketOptions = options;
    quoteState.sort = "price";
    renderMarketResults();
    return;
  }
  renderNetworkResult(action, data);
}

function summaryMarkup(quote, mode) {
  return `
    <div><span>Taşıyıcı</span><strong>${escapeHtml(quote.carrier_name || "WARP Network")}</strong></div>
    <div><span>Taşıma tipi</span><strong>${escapeHtml(mode)}</strong></div>
    <div><span>Fiyat</span><strong>${escapeHtml(formatPrice(quote.price_usd))} USD</strong></div>
    <div><span>Transit süresi</span><strong>${escapeHtml(transitText(quote.transit_days))}</strong></div>
    <div><span>Hizmet</span><strong>${escapeHtml(quote.service_level || "Paylaşılmadı")}</strong></div>
    <div><span>Teklif referansı</span><strong>${escapeHtml(quote.quote_id || "Paylaşılmadı")}</strong></div>`;
}

function contactMessage(quote) {
  return `Merhaba, ${quote.carrier_name || "WARP Network"} için ${formatPrice(quote.price_usd)} tutarındaki teklif hakkında bilgi almak istiyorum. Teklif referansı: ${quote.quote_id || "Paylaşılmadı"}`;
}

function digits(value) {
  return String(value || "").replace(/[^0-9]/g, "");
}

function configureContact(quote) {
  const message = contactMessage(quote);
  const whatsapp = digits(quoteState.contact.whatsapp);
  const phone = String(quoteState.contact.phone || "").trim();
  const email = String(quoteState.contact.email || "").trim();
  const contactLinks = [
    [$("#whatsappLink"), whatsapp ? `https://wa.me/${whatsapp}?text=${encodeURIComponent(message)}` : ""],
    [$("#phoneLink"), phone ? `tel:${phone}` : ""],
    [$("#emailLink"), email ? `mailto:${email}?subject=${encodeURIComponent("Taşıma teklifi")}&body=${encodeURIComponent(message)}` : ""]
  ];
  let activeCount = 0;
  contactLinks.forEach(([link, href]) => {
    if (href) {
      link.href = href;
      link.removeAttribute("aria-disabled");
      activeCount += 1;
    } else {
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
    }
  });
  $("#contactNotice").hidden = activeCount > 0;
}

function openQuoteDialog(quote, mode) {
  if (!quote) return;
  $("#selectedQuoteSummary").innerHTML = summaryMarkup(quote, mode);
  configureContact(quote);
  const dialog = $("#quoteDialog");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

async function loadContact() {
  try {
    const response = await fetch("/api/public-config");
    if (!response.ok) return;
    quoteState.contact = await response.json();
    const phone = String(quoteState.contact.phone || "").trim();
    if (phone) {
      $("#headerPhone").href = `tel:${phone}`;
      $("#headerPhone").removeAttribute("aria-disabled");
      $("#headerPhoneText").textContent = phone;
    }
  } catch {
    quoteState.contact = { whatsapp: "", phone: "", email: "" };
  }
}

$("#quoteForm").addEventListener("input", (event) => {
  if (event.target.matches("input, select")) clearValidation(event.target);
});

$$('input[name="quoteType"]').forEach((input) => input.addEventListener("change", updateDimensionRules));

$$('[data-next]').forEach((button) => button.addEventListener("click", () => {
  if (validatePanel(quoteState.step)) setStep(Number(button.dataset.next));
}));

$$('[data-back]').forEach((button) => button.addEventListener("click", () => setStep(Number(button.dataset.back))));

$("#quoteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!validatePanel(1)) { setStep(1); return; }
  if (!validatePanel(2)) { setStep(2); return; }
  const action = selectedAction();
  const button = $("#submitQuote");
  button.disabled = true;
  button.innerHTML = "Teklifler Hazırlanıyor <span aria-hidden=\"true\">…</span>";
  await fetchQuote(action, buildPayload(action));
  button.disabled = false;
  button.innerHTML = "Teklifleri Karşılaştır <span aria-hidden=\"true\">→</span>";
});

$("#quoteResult").addEventListener("click", (event) => {
  if (event.target.closest("[data-retry]")) setStep(1);
});

$(".dialog-close").addEventListener("click", () => $("#quoteDialog").close());
$("#quoteDialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

$$('[aria-disabled="true"]').forEach((element) => element.addEventListener("click", (event) => {
  if (element.getAttribute("aria-disabled") === "true") event.preventDefault();
}));

$("#pickupDate").min = new Date().toISOString().slice(0, 10);
$("#pickupDate").value = futureDate();
$("#year").textContent = new Date().getFullYear();
updateDimensionRules();
loadContact();
